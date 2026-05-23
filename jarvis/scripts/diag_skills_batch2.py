"""Smoke-тест volume + github + screenshot skills (match + safe-run где можно)."""
from __future__ import annotations
import asyncio, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from core.skills.volume_skill import VolumeSkill, _read_state
from core.skills.github_skill import GitHubSkill, _load_pat
from core.skills.screenshot_skill import ScreenshotDescribeSkill, _grab_and_pack


async def main() -> int:
    # ── VOLUME — только чтение текущего состояния, не меняем уровень ──
    print("=== VOLUME ===")
    vol = VolumeSkill()
    for q in ["какая громкость", "громче", "тише", "выключи звук", "включи звук", "громкость 50", "какая погода"]:
        score = vol.match(q)
        marker = "match" if score > 0 else "no-match"
        print(f"  [{marker}] {q!r}")
    # реальное чтение state
    try:
        cur, muted = _read_state()
        print(f"  current: volume={cur}% muted={muted}")
    except Exception as e:
        print(f"  [read state FAIL] {type(e).__name__}: {e}")

    # ── GITHUB — match + check PAT loaded ──
    print("\n=== GITHUB ===")
    pat = _load_pat()
    print(f"  PAT loaded: {'yes (' + pat[:6] + '...)' if pat else 'NO'}")
    gh = GitHubSkill()
    for q in [
        "какие у меня PR",
        "мои open issues",
        "последние коммиты в J.A.R.V.I.S",
        "issues в J.A.R.V.I.S",
        "что в задачах",  # not match
    ]:
        score = gh.match(q)
        marker = "match" if score > 0 else "no-match"
        print(f"  [{marker}] {q!r}")
    # реальный call к API — только если PAT есть, чтобы не светить ошибку
    if pat:
        res = await gh.run("какие у меня PR", request_id="t")
        print(f"  live PR call → {res.text[:150]}")

    # ── SCREENSHOT — match + проверка что grab работает (без vision) ──
    print("\n=== SCREENSHOT ===")
    # ScreenshotDescribeSkill требует claude provider — для match используем None
    sk = ScreenshotDescribeSkill.__new__(ScreenshotDescribeSkill)  # bypass __init__
    sk._patterns = ScreenshotDescribeSkill.__init__.__wrapped__ if hasattr(ScreenshotDescribeSkill.__init__, "__wrapped__") else []
    # проще — пересоздадим с фейк-claude
    class FakeClaude:
        async def chat_with_image(self, *a, **kw): return "[fake vision]"
    sk = ScreenshotDescribeSkill(FakeClaude())
    for q in [
        "что у меня на экране",
        "опиши что на экране",
        "посмотри на мой экран",
        "сделай мне скриншот",
        "что в задачах",  # not match
    ]:
        score = sk.match(q)
        marker = "match" if score > 0 else "no-match"
        print(f"  [{marker}] {q!r}")
    # реальный screenshot — проверим что Pillow работает (без отправки на vision)
    try:
        b = await asyncio.to_thread(_grab_and_pack)
        print(f"  grab OK — {len(b)} bytes (JPEG)")
    except Exception as e:
        print(f"  [grab FAIL] {type(e).__name__}: {e}")

    return 0


sys.exit(asyncio.run(main()))
