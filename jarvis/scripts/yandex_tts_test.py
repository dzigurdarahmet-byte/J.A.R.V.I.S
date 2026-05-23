"""Smoke: Yandex SpeechKit TTS — синтезируем фразу, сохраняем в WAV."""

from __future__ import annotations

import asyncio
import sys
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402

from core.config import settings  # noqa: E402
from core.voice.tts_yandex import YandexSpeechKitTTS  # noqa: E402


async def main() -> int:
    yk = settings.yandex_api_key.get_secret_value() if settings.yandex_api_key else ""
    yf = settings.yandex_folder_id or ""
    if not yk or not yf:
        print("Нет YANDEX_API_KEY или YANDEX_FOLDER_ID в .env"); return 1

    tts = YandexSpeechKitTTS(api_key=yk, folder_id=yf, voice="alena", emotion="neutral")
    await tts.preload()

    text = "Босс, на связи. Алёна вместо Ксении — звучит живее, не правда ли?"
    print(f"Synthesizing ({len(text)} chars)...")
    audio = await tts.synthesize(text)
    print(f"Got {len(audio)} samples ({len(audio) / tts.sample_rate:.2f} sec)")

    # Сохраним в WAV
    out_path = ROOT / "workspace" / "audio_tmp" / "yandex_smoke.wav"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pcm_int16 = (audio * 32767).astype(np.int16)
    with wave.open(str(out_path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(tts.sample_rate)
        w.writeframes(pcm_int16.tobytes())
    print(f"Saved: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
