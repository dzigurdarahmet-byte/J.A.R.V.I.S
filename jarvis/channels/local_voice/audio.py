"""Аудио I/O для local_voice: захват с микрофона и проигрывание ответов.

sounddevice использует PortAudio под капотом — кроссплатформенно (Windows
видит Bluetooth-наушники как обычные audio devices).

Captured stream даёт 16kHz mono float32 чанки по 512 samples (32 ms) — ровно
то что нужно silero-vad. Playback принимает любой sample rate Silero выдаёт.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Final

import numpy as np
import sounddevice as sd

from core.config import settings
from core.logging import get_logger

logger = get_logger(__name__)

CAPTURE_SR: Final = 16000  # silero-vad требует именно 16kHz
CAPTURE_BLOCK: Final = 512  # 32ms — обязательный размер для silero-vad
CAPTURE_QUEUE_MAX: Final = 200  # ~6.4 сек буфера если consumer не успевает

# ── Input gain boost ─────────────────────────────────────────────────
# Bluetooth Hands-Free profile (FreeBuds Pro 4 итд) отдают речь очень
# тихо: -74…-76 dB при default-стеке Windows. silero-vad на этом уровне
# не срабатывает (VAD_THRESHOLD = 0.5), сегмент даже не создаётся.
#
# Решение: умножить chunk на gain прежде чем класть в очередь. Gain 8x
# поднимает речь -76 → ~-58 dB, и VAD начинает её ловить. Clip защищает
# от перегрузки если в комнате громко.
#
# Управление через .env: JARVIS_AUDIO_INPUT_GAIN. Default 1.0 (бесшумно
# для нормальных микрофонов).
INPUT_GAIN: Final = float(settings.jarvis_audio_input_gain)


class AudioCapture:
    """Непрерывный mic-stream → asyncio.Queue.

    Использование:
        cap = AudioCapture()
        await cap.start()
        try:
            chunk = await cap.queue.get()  # np.ndarray (512,) float32
        finally:
            await cap.stop()

    Mute() — игнорирует входящие чанки (на время SPEAKING), но не глушит сам поток.
    Stop() — закрывает stream полностью.
    """

    def __init__(self, sample_rate: int = CAPTURE_SR, block: int = CAPTURE_BLOCK) -> None:
        self._sr = sample_rate
        self._block = block
        self._stream: sd.InputStream | None = None
        self._muted = threading.Event()
        self.queue: asyncio.Queue[np.ndarray] = asyncio.Queue(maxsize=CAPTURE_QUEUE_MAX)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._current_device_name: str | None = None
        self._monitor_task: asyncio.Task | None = None
        self._stop_monitor = asyncio.Event()

    def _callback(self, indata: np.ndarray, frames: int, _time, _status) -> None:  # noqa: ANN001
        """Вызывается PortAudio из аудио-thread'а. Закидываем чанк в asyncio queue."""
        if self._muted.is_set():
            return
        if self._loop is None:
            return
        chunk = indata[:, 0].copy().astype(np.float32)

        # Input gain boost — для тихих BT-микрофонов. См. INPUT_GAIN выше.
        if INPUT_GAIN != 1.0:
            chunk = np.clip(chunk * INPUT_GAIN, -1.0, 1.0)

        # Раз в ~2 секунды (~62 чанка @ 32ms) логируем RMS уровень.
        self._chunk_counter = getattr(self, "_chunk_counter", 0) + 1
        if self._chunk_counter % 62 == 0:
            rms = float(np.sqrt(np.mean(chunk * chunk)))
            db = 20 * np.log10(rms + 1e-9)
            logger.info("mic_level", rms=round(rms, 5), db=round(float(db), 1))

        try:
            self._loop.call_soon_threadsafe(self._enqueue_nowait, chunk)
        except RuntimeError:
            pass

    def _enqueue_nowait(self, chunk: np.ndarray) -> None:
        try:
            self.queue.put_nowait(chunk)
        except asyncio.QueueFull:
            try:
                _ = self.queue.get_nowait()
                self.queue.put_nowait(chunk)
            except asyncio.QueueEmpty:
                pass

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._open_stream()
        # Hot-swap monitor: каждые 2 сек проверяем что default input не сменился
        self._stop_monitor.clear()
        self._monitor_task = asyncio.create_task(self._device_monitor(), name="audio-device-monitor")

    def _open_stream(self) -> None:
        """Открыть InputStream на текущем default input device."""
        device_info = sd.query_devices(kind="input")
        device_name = device_info["name"]
        self._current_device_name = device_name
        self._stream = sd.InputStream(
            samplerate=self._sr,
            channels=1,
            dtype="float32",
            blocksize=self._block,
            callback=self._callback,
        )
        self._stream.start()
        logger.info(
            "audio_capture_started",
            sample_rate=self._sr,
            block=self._block,
            device=device_name,
        )

    async def _device_monitor(self) -> None:
        """Раз в 2 сек смотрим default input device; если сменился — reopen stream."""
        while not self._stop_monitor.is_set():
            try:
                await asyncio.wait_for(self._stop_monitor.wait(), timeout=2.0)
                return  # stop был запрошен
            except asyncio.TimeoutError:
                pass
            try:
                new_name = sd.query_devices(kind="input")["name"]
            except Exception as e:
                logger.warning("audio_device_query_failed", error=str(e))
                continue
            if new_name != self._current_device_name:
                logger.info(
                    "audio_device_changed",
                    old=self._current_device_name,
                    new=new_name,
                )
                try:
                    if self._stream is not None:
                        self._stream.stop()
                        self._stream.close()
                        self._stream = None
                    self._open_stream()
                    logger.info("audio_device_swapped", to=new_name)
                except Exception as e:
                    logger.error("audio_device_swap_failed", error=str(e))

    async def stop(self) -> None:
        self._stop_monitor.set()
        if self._monitor_task is not None:
            try:
                await asyncio.wait_for(self._monitor_task, timeout=3.0)
            except asyncio.TimeoutError:
                self._monitor_task.cancel()
            self._monitor_task = None
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
            logger.info("audio_capture_stopped")

    def mute(self) -> None:
        """Игнорировать входящие чанки (на время SPEAKING)."""
        self._muted.set()

    def unmute(self) -> None:
        while not self.queue.empty():
            try:
                self.queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        self._muted.clear()


async def play_audio(
    audio: np.ndarray,
    sample_rate: int,
    device: int | str | None = None,
    stop_event: asyncio.Event | None = None,
) -> bool:
    """Проиграть PCM float32 mono на default output (или указанный device).

    Если передан stop_event — поллим его каждые 50ms, при срабатывании
    немедленно sd.stop() и выходим (используется для barge-in).

    Returns:
        True если доиграли до конца, False если прервали через stop_event.
    """
    if audio.size == 0:
        return True
    if audio.dtype != np.float32:
        audio = audio.astype(np.float32)
    if audio.ndim > 1:
        audio = audio[:, 0]

    sd.play(audio, sample_rate, device=device)
    duration = audio.shape[0] / sample_rate

    if stop_event is None:
        await asyncio.sleep(duration)
        sd.wait()
        logger.info("audio_played", duration_sec=round(duration, 2), sample_rate=sample_rate)
        return True

    POLL_INTERVAL_SEC = 0.05
    elapsed = 0.0
    while elapsed < duration:
        if stop_event.is_set():
            sd.stop()
            logger.info(
                "audio_play_interrupted",
                played_sec=round(elapsed, 2),
                total_sec=round(duration, 2),
            )
            return False
        await asyncio.sleep(POLL_INTERVAL_SEC)
        elapsed += POLL_INTERVAL_SEC
    sd.wait()
    logger.info("audio_played", duration_sec=round(duration, 2), sample_rate=sample_rate)
    return True
