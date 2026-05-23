"""Проверка: Yandex Embeddings подхватываются из .env и работают."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.config import settings  # noqa: E402
from core.memory.vector import make_vector_memory  # noqa: E402


async def main() -> int:
    has_key = bool(settings.yandex_api_key and settings.yandex_api_key.get_secret_value())
    print(f"yandex_api_key set: {has_key}")
    print(f"yandex_folder_id : {settings.yandex_folder_id}")

    vm = make_vector_memory(ROOT / "workspace")
    if not vm:
        print("VectorMemory: None"); return 1
    print(f"provider: {vm._provider.name}, dim={vm._provider.dim}")

    # smoke: add + search
    await vm.add("Тестовая фраза про подписку Spotify", role="user", channel="test")
    hits = await vm.search("сколько плачу за музыку", limit=2)
    for h in hits:
        print(f"  {h.get('score'):.3f}  {h.get('text')[:60]}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
