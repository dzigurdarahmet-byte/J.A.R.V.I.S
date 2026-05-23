'''Главный loop местного голосового канала.

State machine:
    IDLE - LISTENING - PROCESSING - SPEAKING - IDLE
    + PAUSED (отдельное состояние, всё игнорируется кроме F12)

Режимы:
    VAD-mode (default): IDLE = mic слушает, VAD ловит начало речи.
    PTT-mode:           IDLE = mic слушает, но не реагирует. Ctrl+Space нажат - LISTENING.
    WAKE-mode:          IDLE = слушаем wake-слово openWakeWord.

В PROCESSING/SPEAKING микрофон может оставаться открытым (для barge-in).
Speaker verification: только голос Босса принимается, чужие сегменты отбрасываются.
'''
from __future__ import annotations

import asyncio
from collections import deque
from enum import Enum
from pathlib import Path
from typing import Final

import numpy as np

from channels.local_voice.audio import (
    CAPTURE_BLOCK,
    AudioCapture,
    play_audio,
)
from channels.local_voice.hotkeys import HotkeyEvent, HotkeyListener
from core.config import settings
from core.event_bus import EventType, JarvisEvent, bus
from core.logging import get_logger, setup_logging
from core.memory import MemoryManager
from core.metrics import metrics
from core.providers import ClaudeProvider, Message
from core.router import Router
from core.security import PromptGuard
from core.skills import register_all_builtin
from core.voice import SileroTTS, VADStream, WhisperSTT
from core.voice.anti_hallucination import clean_or_reject, gate_audio_segment
from core.voice.speaker_id import SpeakerVerifier
from core.voice.wake import WakeDetector

logger = get_logger(__name__)
WORKSPACE_DIR: Final = Path(__file__).resolve().parents[2] / "workspace"


class State(Enum):
    IDLE = "idle"
    LISTENING = "listening"
    PROCESSING = "processing"
    SPEAKING = "speaking"
    PAUSED = "paused"


class Mode(Enum):
    VAD = "vad"
    PTT = "ptt"
    WAKE = "wake"


HISTORY_LIMIT: Final = 12
PTT_BUFFER_MAX_SEC: Final = 30
BARGE_IN_ENABLED: Final = True
BARGE_IN_MIN_DELAY_SEC: Final = 0.4


VOICE_BASE_PROMPT: Final = (
    "Ты - J.A.R.V.I.S., персональный голосовой ассистент Босса. "
    "Стиль: Marvel JARVIS - уважительный, лаконичный, остроумный. "
    "Обращайся 'Босс'. Никогда 'вы', 'сэр', 'господин'. "
    "ВАЖНО: ответ будет ОЗВУЧЕН - пиши КАК для устной речи: "
    "короткие предложения, без markdown, без списков, без эмодзи, без скобок. "
    "Если можно тремя словами - отвечай тремя."
)


def build_voice_system_prompt(memory: MemoryManager) -> str:
    addendum = memory.snapshot().render_system_addendum()
    if addendum:
        return VOICE_BASE_PROMPT + "\n\n" + addendum
    return VOICE_BASE_PROMPT


class LocalVoiceLoop:
    def __init__(
        self,
        claude: ClaudeProvider,
        stt: WhisperSTT,
        tts: SileroTTS,
        memory: MemoryManager,
        mode: Mode = Mode.VAD,
    ) -> None:
        self._claude = claude
        self._stt = stt
        self._tts = tts
        self._memory = memory
        self._mode = mode
        self._state = State.IDLE
        self._capture = AudioCapture()
        self._hotkeys = HotkeyListener()
        self._vad = VADStream()
        self._router = Router(
            claude_provider=claude, memory=memory, base_prompt=VOICE_BASE_PROMPT
        )
        # claude здесь — SmartProvider; передаём как smart_provider, чтобы
        # голосом можно было сказать «пользуйся Дипсиком» прямо в voice loop.
        register_all_builtin(self._router, memory, claude=claude, smart_provider=claude)
        from core.skills.weekly_skill import WeeklySkill
        self._router.register_skill(WeeklySkill(claude))
        recent = memory.load_recent_context(limit_messages=HISTORY_LIMIT)
        self._history: deque[Message] = deque(recent, maxlen=HISTORY_LIMIT)
        if recent:
            logger.info("voice_history_warmed", messages=len(recent))
        self._ptt_buffer: list[np.ndarray] = []
        # Auto-select: если models/wake/dzarvis.onnx есть — используем кастомную
        # русскую модель «Джарвис», иначе fallback на встроенную "hey_jarvis".
        self._wake = WakeDetector.auto()
        self._wake_armed = False
        self._speaker = SpeakerVerifier(WORKSPACE_DIR / "owner_voice.npy")
        if self._speaker.has_reference:
            logger.info("speaker_verification_active")
        else:
            logger.warning(
                "speaker_verification_inactive",
                note="run scripts/enroll_voice.py to teach Jarvis your voice",
            )

    async def run(self) -> None:
        await self._capture.start()
        await self._hotkeys.start()
        self._vad.start()
        if self._mode == Mode.WAKE:
            self._wake.preload()
        # Preload Resemblyzer model in executor - чтобы не блокировать loop при первой реплике
        if self._speaker.has_reference:
            await self._speaker.preload()
        logger.info("local_voice_started", mode=self._mode.value)
        try:
            await self._main_loop()
        finally:
            await self._capture.stop()
            await self._hotkeys.stop()

    async def _main_loop(self) -> None:
        hotkey_task = asyncio.create_task(self._hotkey_consumer())
        try:
            while True:
                if self._state == State.PAUSED:
                    await asyncio.sleep(0.1)
                    continue

                chunk = await self._capture.queue.get()

                if self._state == State.IDLE and self._mode == Mode.VAD:
                    segment = self._vad.feed(chunk)
                    if segment is not None:
                        await self._process_segment(segment)

                elif self._state == State.IDLE and self._mode == Mode.WAKE:
                    if self._wake.ready and self._wake.feed(chunk):
                        metrics.record(
                            "wake",
                            channel="local_voice",
                            provider=self._wake._score_key,
                            meta={"score": round(self._wake.last_score, 3)},
                        )
                        await bus.publish(JarvisEvent(
                            type=EventType.SYSTEM,
                            source="channel:local_voice",
                            channel="local_voice",
                            data={"text": "wake", "score": round(self._wake.last_score, 3)},
                        ))
                        logger.info("wake_armed_waiting_for_speech")
                        self._wake_armed = True
                    elif self._wake_armed:
                        segment = self._vad.feed(chunk)
                        if segment is not None:
                            self._wake_armed = False
                            await self._process_segment(segment)

                elif self._state == State.LISTENING and self._mode == Mode.PTT:
                    self._ptt_buffer.append(chunk)
                    total_samples = sum(c.shape[0] for c in self._ptt_buffer)
                    if total_samples >= PTT_BUFFER_MAX_SEC * 16000:
                        logger.warning("ptt_buffer_overflow_autostop")
                        await self._finish_ptt()
        finally:
            hotkey_task.cancel()

    async def _hotkey_consumer(self) -> None:
        while True:
            event = await self._hotkeys.queue.get()
            await self._handle_hotkey(event)

    async def _handle_hotkey(self, event: HotkeyEvent) -> None:
        if event == HotkeyEvent.TOGGLE_PAUSE:
            if self._state == State.PAUSED:
                self._state = State.IDLE
                self._capture.unmute()
                logger.info("voice_resumed")
            else:
                self._state = State.PAUSED
                self._capture.mute()
                logger.info("voice_paused")

        elif event == HotkeyEvent.SWITCH_MODE:
            order = [Mode.VAD, Mode.PTT, Mode.WAKE]
            idx = (order.index(self._mode) + 1) % len(order)
            self._mode = order[idx]
            if self._mode == Mode.WAKE and not self._wake.ready:
                self._wake.preload()
            self._wake_armed = False
            self._wake.reset()
            logger.info("voice_mode_switched", mode=self._mode.value)

        elif event == HotkeyEvent.PTT_DOWN:
            if self._mode == Mode.PTT and self._state == State.IDLE:
                self._state = State.LISTENING
                self._ptt_buffer = []
                logger.info("ptt_started")

        elif event == HotkeyEvent.PTT_UP:
            if self._mode == Mode.PTT and self._state == State.LISTENING:
                await self._finish_ptt()

    async def _finish_ptt(self) -> None:
        if not self._ptt_buffer:
            self._state = State.IDLE
            return
        segment = np.concatenate(self._ptt_buffer)
        self._ptt_buffer = []
        await self._process_segment(segment)

    async def _process_segment(self, segment: np.ndarray) -> None:
        self._state = State.PROCESSING
        if not BARGE_IN_ENABLED:
            self._capture.mute()
        try:
            current_segment = segment
            while current_segment is not None:
                next_segment = await self._run_one_exchange(current_segment)
                current_segment = next_segment
        finally:
            self._capture.unmute()
            self._state = State.IDLE
            if self._mode == Mode.WAKE:
                self._wake_armed = False
                self._wake.reset()

    async def _run_one_exchange(self, segment):
        self._state = State.PROCESSING
        logger.info("processing_segment", samples=segment.shape[0])

        passed, reason = gate_audio_segment(segment, sample_rate=16000)
        if not passed:
            logger.info("segment_rejected_by_gate", reason=reason)
            return None

        if not await self._speaker.is_owner(segment, sample_rate=16000):
            logger.info("segment_rejected_not_owner")
            metrics.record("speaker_reject", channel="local_voice")
            return None

        text, no_speech_prob = await self._stt.transcribe_with_meta(
            segment, sample_rate=16000
        )

        cleaned = clean_or_reject(text, no_speech_prob=no_speech_prob)
        if cleaned is None:
            logger.info("segment_rejected_as_hallucination", raw=text[:80])
            return None
        text = cleaned

        if not text:
            logger.info("empty_transcription_skip")
            return None
        logger.info("user_said", text=text)

        clean_text, threats = PromptGuard.sanitize_input(text, channel="local_voice")
        if threats:
            logger.warning("input_sanitized", threats=threats)

        import uuid as _uuid
        req_id = _uuid.uuid4().hex[:12]
        await bus.publish(JarvisEvent(
            type=EventType.USER_INPUT,
            source="channel:local_voice",
            channel="local_voice",
            request_id=req_id,
            data={"text": clean_text},
        ))

        self._history.append(Message(role="user", content=clean_text))
        try:
            reply = await self._router.dispatch(
                text=clean_text,
                history=list(self._history),
                channel="local_voice",
                request_id=req_id,
            )
        except Exception as e:
            logger.error("llm_error", error=str(e))
            reply = "Босс, не дотянулся до Claude. Технические проблемы."

        safe_reply, leaks = PromptGuard.filter_output(reply)
        if leaks:
            logger.error("output_redacted", leaks=leaks)
        self._history.append(Message(role="assistant", content=safe_reply))
        logger.info("jarvis_replied", text=safe_reply)

        await bus.publish(JarvisEvent(
            type=EventType.ASSISTANT_REPLY,
            source="channel:local_voice",
            channel="local_voice",
            request_id=req_id,
            data={"text": safe_reply},
        ))

        await self._memory.append_exchange_async(
            user_text=clean_text,
            assistant_text=safe_reply,
            channel="local_voice",
        )
        asyncio.create_task(self._memory.add_to_vector(clean_text, role="user", channel="local_voice"))
        asyncio.create_task(self._memory.add_to_vector(safe_reply, role="assistant", channel="local_voice"))

        self._state = State.SPEAKING
        audio = await self._tts.synthesize(safe_reply)

        if not BARGE_IN_ENABLED:
            await play_audio(audio, sample_rate=self._tts.sample_rate)
            return None

        self._drain_capture_queue()

        stop_event = asyncio.Event()
        barge_holder = {"segment": None}
        barge_task = asyncio.create_task(
            self._barge_in_listener(stop_event, barge_holder)
        )
        try:
            finished = await play_audio(
                audio,
                sample_rate=self._tts.sample_rate,
                stop_event=stop_event,
            )
        finally:
            stop_event.set()
            try:
                await asyncio.wait_for(barge_task, timeout=1.0)
            except asyncio.TimeoutError:
                barge_task.cancel()

        if not finished and barge_holder["segment"] is not None:
            logger.info("barge_in_triggered", samples=barge_holder["segment"].shape[0])
            metrics.record("barge_in", channel="local_voice")
            return barge_holder["segment"]
        return None

    def _drain_capture_queue(self) -> None:
        while not self._capture.queue.empty():
            try:
                self._capture.queue.get_nowait()
            except asyncio.QueueEmpty:
                break

    async def _barge_in_listener(self, stop_event, holder):
        from core.voice.vad import VADStream
        local_vad = VADStream()
        local_vad.start()
        loop_started = asyncio.get_event_loop().time()
        while not stop_event.is_set():
            try:
                chunk = await asyncio.wait_for(
                    self._capture.queue.get(), timeout=0.1
                )
            except asyncio.TimeoutError:
                continue
            if asyncio.get_event_loop().time() - loop_started < BARGE_IN_MIN_DELAY_SEC:
                continue
            segment = local_vad.feed(chunk)
            if segment is not None:
                if not await self._speaker.is_owner(segment, sample_rate=16000):
                    logger.info("barge_in_rejected_not_owner")
                    local_vad.start()
                    continue
                holder["segment"] = segment
                stop_event.set()
                return


async def run_local_voice(mode: str = "vad") -> None:
    setup_logging()
    logger.info("local_voice_init", mode=mode)

    from core.providers import build_smart_provider
    claude = build_smart_provider(settings)
    whisper_stt = WhisperSTT()
    yk = settings.yandex_api_key
    yf = settings.yandex_folder_id
    # STT: Yandex primary (быстрее, лучше с именами), Whisper fallback при оффлайне
    if yk and yk.get_secret_value() and yf:
        from core.voice.stt_yandex import STTWithFallback, YandexSpeechKitSTT
        yandex_stt = YandexSpeechKitSTT(api_key=yk.get_secret_value(), folder_id=yf)
        stt = STTWithFallback(primary=yandex_stt, fallback=whisper_stt)
        logger.info("stt_provider_chosen", provider="yandex+whisper-fallback")
    else:
        stt = whisper_stt
        logger.info("stt_provider_chosen", provider="whisper")
    # TTS: Yandex Alena если ключи есть + Silero offline fallback.
    # C16: TTSWithFallback автоматически уходит на Silero при network err.
    if yk and yk.get_secret_value() and yf:
        from core.voice.tts_fallback import TTSWithFallback
        from core.voice.tts_yandex import YandexSpeechKitTTS
        yandex_tts = YandexSpeechKitTTS(api_key=yk.get_secret_value(), folder_id=yf, voice="alena")
        silero_tts = SileroTTS()
        tts = TTSWithFallback(primary=yandex_tts, fallback=silero_tts)
        logger.info("tts_provider_chosen", provider="yandex+silero-fallback", voice="alena")
    else:
        tts = SileroTTS()
        logger.info("tts_provider_chosen", provider="silero", speaker="xenia")
    memory = MemoryManager(workspace_dir=WORKSPACE_DIR)

    logger.info("preloading_voice_models")
    await asyncio.gather(stt.preload(), tts.preload())
    logger.info("voice_models_ready")

    loop = LocalVoiceLoop(
        claude=claude,
        stt=stt,
        tts=tts,
        memory=memory,
        mode=Mode(mode),
    )
    try:
        await loop.run()
    finally:
        await claude.close()
