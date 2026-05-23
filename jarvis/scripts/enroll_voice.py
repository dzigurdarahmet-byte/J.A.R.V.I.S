"""Enrollment: записать голос Босса (30 секунд) - reference embedding.

Запуск:
    .venv\\Scripts\\python.exe scripts\\enroll_voice.py

Что произойдёт:
  1. Откроется мик (FreeBuds или дефолт)
  2. Скрипт скажет начать запись через 3 секунды
  3. Читай любой текст 30 секунд - Resemblyzer обучен на content-independent embeddings
     Например: "Раз два три четыре пять, вышел зайчик погулять..." или любой текст
  4. По таймеру запись остановится, посчитается средний embedding
     по 6 нарезанным отрезкам, и сохранится в workspace/owner_voice.npy
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import sounddevice as sd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.logging import setup_logging
from core.voice.speaker_id import RESEMBLYZER_SR

SAMPLE_RATE = RESEMBLYZER_SR
TARGET_DURATION_SEC = 30
WORKSPACE = Path(__file__).resolve().parents[1] / "workspace"
REFERENCE_PATH = WORKSPACE / "owner_voice.npy"


def record_audio(duration_sec: int) -> np.ndarray:
    print(f"\n=== Запись {duration_sec} секунд ===")
    print("Босс, читай любой текст в течение 30 секунд. Начинаю через 3 секунды...")
    for i in (3, 2, 1):
        print(f"  {i}...")
        time.sleep(1)
    print("\n>>> ГОВОРИ <<<\n")
    audio = sd.rec(
        int(duration_sec * SAMPLE_RATE),
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype="float32",
        blocking=False,
    )
    for sec_left in range(duration_sec, 0, -1):
        time.sleep(1)
        if sec_left % 5 == 0 or sec_left <= 5:
            print(f"  осталось {sec_left} сек...")
    sd.wait()
    print("\n>>> ЗАПИСЬ ОСТАНОВЛЕНА <<<\n")
    return audio[:, 0]


def compute_reference_embedding(audio: np.ndarray) -> np.ndarray:
    """Считаем средний embedding по 6 чанкам. Robust к шуму."""
    from resemblyzer import VoiceEncoder

    encoder = VoiceEncoder(verbose=False)
    duration = audio.shape[0] / SAMPLE_RATE
    print(f"Аудио {duration:.1f} сек, RMS={float(np.sqrt(np.mean(audio*audio))):.4f}")

    chunk_count = 6
    chunk_len = audio.shape[0] // chunk_count
    embeddings = []
    for i in range(chunk_count):
        chunk = audio[i * chunk_len : (i + 1) * chunk_len]
        if chunk.shape[0] < SAMPLE_RATE:
            continue
        emb = encoder.embed_utterance(chunk)
        embeddings.append(emb)
        print(f"  chunk {i+1}/{chunk_count}: emb shape={emb.shape}")
    if not embeddings:
        raise RuntimeError("Не получилось вычислить ни одного embedding")
    mean_emb = np.mean(embeddings, axis=0)
    mean_emb = mean_emb / np.linalg.norm(mean_emb)
    return mean_emb.astype(np.float32)


def main() -> None:
    setup_logging()
    print(f"\nИспользуется input device: {sd.query_devices(kind='input')['name']}")
    audio = record_audio(TARGET_DURATION_SEC)
    rms = float(np.sqrt(np.mean(audio * audio)))
    if rms < 0.005:
        print(f"\nRMS слишком низкий ({rms:.5f}) - мик не услышал. Перезапиши.")
        sys.exit(1)
    print(f"RMS = {rms:.4f}, нормально.")
    reference = compute_reference_embedding(audio)
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    np.save(REFERENCE_PATH, reference)
    print(f"\nReference embedding: shape={reference.shape}, norm={np.linalg.norm(reference):.3f}")
    print(f"Сохранено: {REFERENCE_PATH}")
    print("\nEnrollment завершён. Перезапусти run_voice.py - теперь Джарвис будет узнавать только тебя.")


if __name__ == "__main__":
    main()
