"""Подготовка датасета для openWakeWord training.

Что делает:
    1. Берёт raw записи из workspace/wake_samples/raw/.
    2. Positive samples — уже короткие (~1.8 сек), просто нормализует амплитуду
       и обрезает тишину по краям через energy gating.
    3. Negative блоки — длинные (~60 сек) — режет на куски ~3 сек через VAD,
       чтобы у дискриминатора было разнообразие фоновой речи.
    4. Все samples сохраняются как 16-bit PCM WAV 16 kHz mono.
    5. Также упаковываются в один dataset.zip для удобной загрузки в Colab.

Выход:
    workspace/wake_samples/processed/
        positive/  — нарезанные positives (по умолчанию ~150)
        negative/  — нарезанные negative кусочки (по умолчанию ~50-100)
    workspace/wake_samples/dataset.zip — для загрузки в Colab

Запуск:
    .venv\\Scripts\\python.exe scripts\\extract_wake_dataset.py
"""

from __future__ import annotations

import sys
import wave
import zipfile
from pathlib import Path

import numpy as np

SAMPLE_RATE = 16000
NEGATIVE_CHUNK_SEC = 3.0
NEGATIVE_VAD_THRESHOLD = 0.005  # RMS, ниже которого считаем тишиной
NEGATIVE_MIN_VOICED_FRAC = 0.3  # минимум 30% чанка должно быть voiced

WORKSPACE = Path(__file__).resolve().parents[1] / "workspace"
RAW_DIR = WORKSPACE / "wake_samples" / "raw"
RAW_POS_DIR = RAW_DIR / "positive"
RAW_NEG_DIR = RAW_DIR / "negative"
OUT_DIR = WORKSPACE / "wake_samples" / "processed"
OUT_POS_DIR = OUT_DIR / "positive"
OUT_NEG_DIR = OUT_DIR / "negative"
DATASET_ZIP = WORKSPACE / "wake_samples" / "dataset.zip"


def read_wav(path: Path) -> tuple[np.ndarray, int]:
    """Прочитать WAV, вернуть int16 mono + sample rate."""
    with wave.open(str(path), "rb") as wf:
        sr = wf.getframerate()
        n_channels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        n_frames = wf.getnframes()
        raw = wf.readframes(n_frames)
    if sampwidth != 2:
        raise ValueError(f"{path.name}: ожидаю 16-bit PCM, got sampwidth={sampwidth}")
    audio = np.frombuffer(raw, dtype=np.int16)
    if n_channels > 1:
        audio = audio.reshape(-1, n_channels)[:, 0]
    return audio, sr


def write_wav(path: Path, audio_int16: np.ndarray, sample_rate: int = SAMPLE_RATE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(audio_int16.astype(np.int16).tobytes())


def normalize_amplitude(audio: np.ndarray, target_peak: float = 0.9) -> np.ndarray:
    """Привести peak amplitude к target_peak. Возвращает float32 [-1, 1]."""
    if audio.dtype == np.int16:
        audio = audio.astype(np.float32) / 32768.0
    peak = float(np.max(np.abs(audio)) or 1.0)
    return (audio * (target_peak / peak)).astype(np.float32)


def trim_silence(audio_f32: np.ndarray, frame_ms: int = 20, threshold: float = 0.005) -> np.ndarray:
    """Обрезать тишину по краям. Энергетический VAD по фреймам."""
    frame_len = int(SAMPLE_RATE * frame_ms / 1000)
    if audio_f32.shape[0] < frame_len * 3:
        return audio_f32
    rms = np.array(
        [
            float(np.sqrt(np.mean(audio_f32[i : i + frame_len] ** 2)))
            for i in range(0, audio_f32.shape[0] - frame_len, frame_len)
        ]
    )
    voiced = rms > threshold
    if not voiced.any():
        return audio_f32
    first = int(np.argmax(voiced))
    last = len(voiced) - int(np.argmax(voiced[::-1])) - 1
    # Оставляем небольшой padding
    pad_frames = 2
    first = max(0, first - pad_frames)
    last = min(len(voiced) - 1, last + pad_frames)
    start = first * frame_len
    end = (last + 1) * frame_len
    return audio_f32[start:end]


def process_positives() -> int:
    """Обработать positive samples: trim silence + нормализация."""
    if not RAW_POS_DIR.exists():
        print(f"[positive] директория {RAW_POS_DIR} не существует, пропускаю")
        return 0
    files = sorted(RAW_POS_DIR.glob("*.wav"))
    if not files:
        print(f"[positive] нет .wav в {RAW_POS_DIR}")
        return 0

    print(f"[positive] обрабатываю {len(files)} файлов...")
    saved = 0
    for src in files:
        try:
            audio, sr = read_wav(src)
            if sr != SAMPLE_RATE:
                print(f"  {src.name}: SR={sr}, ожидаю {SAMPLE_RATE} — пропуск")
                continue
            audio_f32 = normalize_amplitude(audio)
            trimmed = trim_silence(audio_f32)
            duration_sec = trimmed.shape[0] / SAMPLE_RATE
            # openWakeWord ожидает positives длиной 1.4-2.0 сек
            if duration_sec < 0.4:
                print(f"  {src.name}: слишком короткий после trim ({duration_sec:.2f}s), пропуск")
                continue
            if duration_sec > 2.5:
                # Обрезаем хвост
                trimmed = trimmed[: int(2.0 * SAMPLE_RATE)]
            audio_int16 = (np.clip(trimmed, -1.0, 1.0) * 32767).astype(np.int16)
            out = OUT_POS_DIR / src.name
            write_wav(out, audio_int16)
            saved += 1
        except Exception as e:
            print(f"  {src.name}: ошибка — {e}")
    print(f"[positive] сохранено {saved} -> {OUT_POS_DIR}")
    return saved


def process_negatives() -> int:
    """Нарезать длинные negative блоки на чанки 3 сек."""
    if not RAW_NEG_DIR.exists():
        print(f"[negative] директория {RAW_NEG_DIR} не существует, пропускаю")
        return 0
    files = sorted(RAW_NEG_DIR.glob("*.wav"))
    if not files:
        print(f"[negative] нет .wav в {RAW_NEG_DIR}")
        return 0

    chunk_samples = int(NEGATIVE_CHUNK_SEC * SAMPLE_RATE)
    print(f"[negative] обрабатываю {len(files)} файлов...")
    saved = 0
    for src in files:
        try:
            audio, sr = read_wav(src)
            if sr != SAMPLE_RATE:
                print(f"  {src.name}: SR={sr}, пропуск")
                continue
            audio_f32 = audio.astype(np.float32) / 32768.0
            total_samples = audio_f32.shape[0]
            n_chunks = total_samples // chunk_samples
            kept = 0
            for ci in range(n_chunks):
                chunk = audio_f32[ci * chunk_samples : (ci + 1) * chunk_samples]
                # Простой voice detection: какая доля кадров громче threshold
                frame_len = int(0.02 * SAMPLE_RATE)
                frames = [
                    chunk[fi : fi + frame_len]
                    for fi in range(0, chunk.shape[0] - frame_len, frame_len)
                ]
                if not frames:
                    continue
                voiced_frac = float(
                    np.mean(
                        [float(np.sqrt(np.mean(f**2))) > NEGATIVE_VAD_THRESHOLD for f in frames]
                    )
                )
                if voiced_frac < NEGATIVE_MIN_VOICED_FRAC:
                    continue
                # Нормализуем и сохраняем
                norm = normalize_amplitude(chunk, target_peak=0.85)
                int16 = (np.clip(norm, -1.0, 1.0) * 32767).astype(np.int16)
                out = OUT_NEG_DIR / f"{src.stem}_chunk{ci:03d}.wav"
                write_wav(out, int16)
                kept += 1
                saved += 1
            print(f"  {src.name}: {n_chunks} чанков -> {kept} оставлено")
        except Exception as e:
            print(f"  {src.name}: ошибка — {e}")
    print(f"[negative] сохранено {saved} -> {OUT_NEG_DIR}")
    return saved


def build_dataset_zip(n_pos: int, n_neg: int) -> None:
    """Упаковать processed/ в один zip для Colab."""
    if DATASET_ZIP.exists():
        DATASET_ZIP.unlink()
    print(f"\nУпаковываю в {DATASET_ZIP}...")
    with zipfile.ZipFile(DATASET_ZIP, "w", zipfile.ZIP_DEFLATED) as zf:
        for src in sorted(OUT_POS_DIR.glob("*.wav")):
            zf.write(src, arcname=f"positive/{src.name}")
        for src in sorted(OUT_NEG_DIR.glob("*.wav")):
            zf.write(src, arcname=f"negative/{src.name}")
    size_kb = DATASET_ZIP.stat().st_size / 1024
    print(f"  positive: {n_pos}, negative: {n_neg}, размер: {size_kb:.1f} KB")


def main() -> None:
    print(f"raw:       {RAW_DIR}")
    print(f"processed: {OUT_DIR}")
    print(f"dataset:   {DATASET_ZIP}")
    print()

    if not RAW_DIR.exists():
        print(f"ОШИБКА: {RAW_DIR} не существует.")
        print("Сначала запусти scripts/record_wake_samples.py")
        sys.exit(1)

    n_pos = process_positives()
    n_neg = process_negatives()

    if n_pos == 0 and n_neg == 0:
        print("\nНичего не обработано. Сначала запиши samples через record_wake_samples.py.")
        sys.exit(1)

    build_dataset_zip(n_pos, n_neg)

    print()
    print("=" * 60)
    print("ГОТОВО")
    print("=" * 60)
    print(f"positive: {n_pos} файлов")
    print(f"negative: {n_neg} файлов")
    print(f"dataset:  {DATASET_ZIP} ({DATASET_ZIP.stat().st_size / 1024:.1f} KB)")
    print()
    print("Следующий шаг — Colab:")
    print("  1. Открой scripts/train_wake_dzarvis.ipynb на colab.research.google.com")
    print("  2. Загрузи dataset.zip в первую ячейку")
    print("  3. Runtime -> Change runtime type -> T4 GPU -> Save")
    print("  4. Run All. Через 6-8 часов в Drive появится dzarvis.onnx")


if __name__ == "__main__":
    main()
