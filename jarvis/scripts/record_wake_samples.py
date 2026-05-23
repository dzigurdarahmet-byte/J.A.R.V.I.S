"""Сбор тренировочных samples для custom wake-word «Джарвис».

Сценарий:
    1. Скрипт проводит Босса через ~15 манер произнесения слова «Джарвис».
       Каждая манера — 10 повторов, итого ~150 positive samples.
    2. Дополнительно собирается 30-60 секунд negative речи (произвольный
       текст БЕЗ слова «Джарвис») — для дискриминатора.
    3. Все WAV сохраняются в workspace/wake_samples/raw/ в формате
       16 kHz mono int16 (то, что ждёт openWakeWord training).

После записи — запустить scripts/extract_wake_dataset.py для финальной
обработки и подготовки к Colab training.

Запуск:
    .venv\\Scripts\\python.exe scripts\\record_wake_samples.py
    # опционально — продолжить после прерывания:
    .venv\\Scripts\\python.exe scripts\\record_wake_samples.py --resume

Тайминги:
    ~2 сек на запись + ~2 сек паузы между = 4 сек на utterance
    150 utterances ≈ 10 минут
    + 1 минута на negative speech
    Итого ~12 минут.
"""

from __future__ import annotations

import argparse
import sys
import time
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import sounddevice as sd

SAMPLE_RATE = 16000
UTTERANCE_DURATION_SEC = 1.8  # длина одной записи
PAUSE_BETWEEN_SEC = 1.0  # пауза после записи перед следующей подсказкой
NEGATIVE_DURATION_SEC = 60.0  # длина negative-блока
MIN_RMS_THRESHOLD = 0.005  # ниже — запись считается пустой, пропускаем

WORKSPACE = Path(__file__).resolve().parents[1] / "workspace"
RAW_DIR = WORKSPACE / "wake_samples" / "raw"
POSITIVE_DIR = RAW_DIR / "positive"
NEGATIVE_DIR = RAW_DIR / "negative"


@dataclass
class Style:
    """Манера произнесения. 10 записей на каждый стиль."""

    code: str
    title: str
    hint: str  # подсказка Боссу — как говорить
    repeats: int = 10


# 15 стилей × 10 повторов = 150 positive samples
STYLES: list[Style] = [
    Style("01_quiet", "Тихо нейтрально", "обычная громкость, спокойно"),
    Style("02_loud", "Громко", "как зовёшь из соседней комнаты"),
    Style("03_happy", "Радостно", "с улыбкой, приподнято"),
    Style("04_tired", "Устало", "вялым голосом, конец рабочего дня"),
    Style("05_annoyed", "Раздражённо", "с лёгким недовольством"),
    Style("06_question", "Как вопрос", "интонация вверх: «Джарвис?»"),
    Style("07_command", "Уверенный приказ", "интонация вниз, точка: «Джарвис.»"),
    Style("08_whisper", "Шёпотом", "тихо, шипяще"),
    Style("09_fast", "Быстро", "почти проглатывая, торопливо"),
    Style("10_slow", "Растягивая", "«Джа-а-арви-и-с»"),
    Style("11_distant", "Издалека", "отойди от мика на 2-3 метра"),
    Style("12_close", "Близко", "почти губами в мик"),
    Style("13_mid_sentence", "В середине фразы", "«Послушай, Джарвис, потом продолжишь»"),
    Style("14_start_sentence", "В начале фразы", "«Джарвис, что там у нас?»"),
    Style("15_end_sentence", "В конце фразы", "«Сделай это, Джарвис»"),
]

# Темы для negative речи — Босс должен говорить произвольный текст БЕЗ слова Джарвис.
NEGATIVE_PROMPTS = [
    "Расскажи о прошлой неделе — что было интересного, что планируешь",
    "Произвольный текст с цифрами: годы, суммы, телефоны, адреса",
    "Разговор с кем-то рядом: 'Оля, передай соль, какой прогноз погоды'",
    "Рабочие термины: про маркетинг, AI, проекты, любая текущая тема",
]


def list_input_devices() -> None:
    print("\n=== Доступные input devices ===")
    for i, d in enumerate(sd.query_devices()):
        if d.get("max_input_channels", 0) > 0:
            default = " (DEFAULT)" if i == sd.default.device[0] else ""
            print(f"  [{i}] {d['name']}{default}")


def record_blocking(duration_sec: float, label: str = "") -> np.ndarray:
    """Запись с countdown'ом."""
    if label:
        print(f"  >>> {label}")
    print(f"      готов... ", end="", flush=True)
    for c in (3, 2, 1):
        time.sleep(0.6)
        print(f"{c} ", end="", flush=True)
    print("ЗАПИСЬ")
    audio = sd.rec(
        int(duration_sec * SAMPLE_RATE),
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype="float32",
        blocking=True,
    )
    return audio[:, 0]


def save_wav(path: Path, audio: np.ndarray) -> None:
    """Сохранить как 16-bit PCM WAV mono 16 kHz."""
    audio_clipped = np.clip(audio, -1.0, 1.0)
    audio_int16 = (audio_clipped * 32767).astype(np.int16)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(audio_int16.tobytes())


def compute_rms(audio: np.ndarray) -> float:
    if audio.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(audio.astype(np.float64) ** 2)))


def count_existing(dir_path: Path, pattern: str) -> int:
    if not dir_path.exists():
        return 0
    return len(list(dir_path.glob(pattern)))


def record_style(style: Style, resume: bool) -> int:
    """Записать N сэмплов одного стиля. Возвращает сколько было успешно сохранено."""
    print()
    print("=" * 60)
    print(f"СТИЛЬ {style.code}: {style.title}")
    print(f"Подсказка: {style.hint}")
    print("=" * 60)

    existing = count_existing(POSITIVE_DIR, f"{style.code}_*.wav") if resume else 0
    if existing:
        print(f"Уже записано: {existing}/{style.repeats} — продолжаем с {existing + 1}")
    saved = existing

    for i in range(existing + 1, style.repeats + 1):
        label = f"[{style.code}] запись {i}/{style.repeats} — скажи «Джарвис»"
        attempt = 0
        while True:
            attempt += 1
            audio = record_blocking(UTTERANCE_DURATION_SEC, label=label)
            rms = compute_rms(audio)
            if rms < MIN_RMS_THRESHOLD:
                print(f"      RMS={rms:.5f} слишком тихо. Повторим эту запись (Ctrl+C — пропустить).")
                if attempt >= 3:
                    print("      Пропускаю эту запись.")
                    break
                continue
            out = POSITIVE_DIR / f"{style.code}_{i:03d}.wav"
            save_wav(out, audio)
            print(f"      RMS={rms:.4f}  -> {out.name}")
            saved += 1
            break
        time.sleep(PAUSE_BETWEEN_SEC)

    return saved


def record_negatives() -> int:
    """Запись negative — длинная фраза без слова Джарвис."""
    print()
    print("=" * 60)
    print("NEGATIVE SAMPLES — обычная речь БЕЗ слова «Джарвис»")
    print("=" * 60)
    print(f"Запишу {len(NEGATIVE_PROMPTS)} блоков по {int(NEGATIVE_DURATION_SEC)} сек.")
    print("ВАЖНО: НЕ произноси слово «Джарвис» — это испортит negative-датасет.\n")

    NEGATIVE_DIR.mkdir(parents=True, exist_ok=True)
    saved = 0
    for idx, prompt in enumerate(NEGATIVE_PROMPTS, start=1):
        print(f"\n--- блок {idx}/{len(NEGATIVE_PROMPTS)} ---")
        print(f"Тема: {prompt}")
        audio = record_blocking(NEGATIVE_DURATION_SEC, label=f"говори ~{int(NEGATIVE_DURATION_SEC)} сек")
        rms = compute_rms(audio)
        if rms < MIN_RMS_THRESHOLD:
            print(f"      RMS={rms:.5f} — пропуск, недостаточно звука")
            continue
        out = NEGATIVE_DIR / f"neg_{idx:02d}.wav"
        save_wav(out, audio)
        print(f"      RMS={rms:.4f}  -> {out.name}")
        saved += 1

    return saved


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--resume", action="store_true", help="продолжить после прерывания")
    parser.add_argument("--styles-only", action="store_true", help="записать только positive (без negative блока)")
    parser.add_argument("--negatives-only", action="store_true", help="записать только negative speech")
    parser.add_argument("--device", type=int, default=None, help="индекс input device (по умолчанию default)")
    args = parser.parse_args()

    if args.device is not None:
        sd.default.device = (args.device, sd.default.device[1])

    list_input_devices()
    print()
    print("Параметры записи:")
    print(f"  sample rate:   {SAMPLE_RATE} Hz")
    print(f"  длина utt:     {UTTERANCE_DURATION_SEC} сек")
    print(f"  positive:      {len(STYLES)} стилей × {STYLES[0].repeats} = {len(STYLES) * STYLES[0].repeats}")
    print(f"  negative:      {len(NEGATIVE_PROMPTS)} блоков × {int(NEGATIVE_DURATION_SEC)} сек")
    print(f"  raw директория: {RAW_DIR}")
    print()

    input("Подключи мик (FreeBuds или встроенный), нажми Enter для старта...")

    total_pos = 0
    if not args.negatives_only:
        for style in STYLES:
            try:
                total_pos += record_style(style, resume=args.resume)
            except KeyboardInterrupt:
                print(f"\nПрерывание — записано {total_pos} positive. --resume чтобы продолжить.")
                sys.exit(0)

    total_neg = 0
    if not args.styles_only:
        try:
            total_neg = record_negatives()
        except KeyboardInterrupt:
            print(f"\nПрерывание — записано {total_neg} negative блоков.")
            sys.exit(0)

    print()
    print("=" * 60)
    print("ГОТОВО")
    print("=" * 60)
    print(f"Positive samples: {total_pos} файлов в {POSITIVE_DIR}")
    print(f"Negative blocks:  {total_neg} файлов в {NEGATIVE_DIR}")
    print()
    print("Следующий шаг:")
    print("  .venv\\Scripts\\python.exe scripts\\extract_wake_dataset.py")


if __name__ == "__main__":
    main()
