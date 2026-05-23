"""Voice subsystem: STT (Whisper) + TTS (Silero) + VAD (silero-vad).

Общие компоненты для каналов:
- channels/telegram (voice messages)
- channels/local_voice (микрофон/наушники hands-free)
"""

from .anti_hallucination import clean_or_reject, gate_audio_segment, is_hallucination
from .stt import WhisperSTT
from .tts import SileroTTS
from .vad import VADStream

__all__ = [
    "WhisperSTT",
    "SileroTTS",
    "VADStream",
    "clean_or_reject",
    "gate_audio_segment",
    "is_hallucination",
]
