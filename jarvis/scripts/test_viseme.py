"""Smoke-test для core.voice.viseme.text_to_visemes."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.voice.viseme import text_to_visemes


def main() -> None:
    samples = [
        ("Привет, Босс.", 1.5),
        ("Как дела сегодня?", 2.0),
        ("Запусти музыку", 1.8),
    ]
    for text, duration in samples:
        print(f"\n=== '{text}' (duration={duration}s) ===")
        visemes = text_to_visemes(text, duration)
        print(f"total keys: {len(visemes)}")
        for v in visemes:
            print(f"  t={v['t']:.3f}  viseme={v['viseme']:5s}  weight={v['weight']:.2f}")


if __name__ == "__main__":
    main()
