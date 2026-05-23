"""Speaker verification для local_voice (owner-only).

Resemblyzer 256-dim GE2E embeddings.
PyTorch вызовы выполняются в thread executor чтобы не блокировать asyncio loop.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Final

import numpy as np

from core.config import settings
from core.logging import get_logger

logger = get_logger(__name__)

EMBEDDING_DIM: Final = 256
# Default 0.65 — для случая «вокруг шумно, нужна точность». Если Босс
# поменял микрофон (FreeBuds vs другой) — записанный embedding в
# owner_voice.npy уже не сходится точно, и similarity падает. Понижаем
# через settings.jarvis_speaker_similarity_threshold (.env).
SIMILARITY_THRESHOLD: Final = float(settings.jarvis_speaker_similarity_threshold)
RESEMBLYZER_SR: Final = 16000
MIN_SEGMENT_SEC: Final = 0.7


class SpeakerVerifier:
    """Owner-only голосовой фильтр.

    PyTorch inference выносится в thread executor (asyncio-friendly).
    Перед runtime использованием нужно вызвать `await verifier.preload()` —
    это загрузит модель и reference в фоне.
    """

    def __init__(self, reference_path: Path) -> None:
        self._reference_path = Path(reference_path)
        self._reference: np.ndarray | None = None
        self._encoder = None

    @property
    def has_reference(self) -> bool:
        if self._reference is not None:
            return True
        return self._reference_path.exists()

    async def preload(self) -> bool:
        """Загрузить модель и reference в executor. Returns True если verifier готов."""
        loop = asyncio.get_event_loop()
        ok = await loop.run_in_executor(None, self._load_sync)
        return ok

    def _load_sync(self) -> bool:
        """Синхронная часть загрузки — выполняется в executor."""
        if self._encoder is None:
            try:
                from resemblyzer import VoiceEncoder
                self._encoder = VoiceEncoder(verbose=False)
                logger.info("speaker_encoder_loaded")
            except Exception as e:
                logger.error("speaker_encoder_load_failed", error=str(e))
                return False
        if self._reference is None:
            if not self._reference_path.exists():
                logger.warning(
                    "speaker_reference_missing",
                    path=str(self._reference_path),
                )
                return False
            try:
                self._reference = np.load(self._reference_path)
                logger.info(
                    "speaker_reference_loaded",
                    dim=int(self._reference.shape[0]),
                )
            except Exception as e:
                logger.error("speaker_reference_load_failed", error=str(e))
                return False
        return True

    def _embed_sync(self, audio: np.ndarray) -> np.ndarray | None:
        """Sync embedding — для запуска в executor."""
        if self._encoder is None or self._reference is None:
            return None
        if audio.dtype != np.float32:
            audio = audio.astype(np.float32)
        try:
            return self._encoder.embed_utterance(audio)
        except Exception as e:
            logger.warning("speaker_embed_failed", error=str(e))
            return None

    async def is_owner(
        self,
        audio: np.ndarray,
        sample_rate: int = RESEMBLYZER_SR,
        threshold: float = SIMILARITY_THRESHOLD,
    ) -> bool:
        """True если сегмент похож на Босса. Fail-open если verifier не готов."""
        if not self.has_reference:
            return True
        if sample_rate != RESEMBLYZER_SR:
            logger.warning("speaker_unexpected_sr", got=sample_rate)
            return True
        duration_sec = audio.shape[0] / sample_rate
        if duration_sec < MIN_SEGMENT_SEC:
            return True
        # Ленивая загрузка если preload не звали
        if self._encoder is None or self._reference is None:
            loop = asyncio.get_event_loop()
            ok = await loop.run_in_executor(None, self._load_sync)
            if not ok:
                return True  # fail-open
        # Embedding в executor чтобы не блокировать loop
        loop = asyncio.get_event_loop()
        emb = await loop.run_in_executor(None, self._embed_sync, audio)
        if emb is None or self._reference is None:
            return True
        sim = float(np.dot(emb, self._reference))
        is_match = sim >= threshold
        logger.info(
            "speaker_check",
            similarity=round(sim, 3),
            threshold=threshold,
            match=is_match,
            duration_sec=round(duration_sec, 2),
        )
        return is_match
