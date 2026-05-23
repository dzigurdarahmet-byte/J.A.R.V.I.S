"""Ручное скачивание модели cointegrated/rubert-tiny2 в HF cache.

Использует системный прокси (через HTTPS_PROXY env). Кладёт файлы напрямую
в snapshot dir, чтобы sentence-transformers подхватил с диска.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import requests

REPO = "cointegrated/rubert-tiny2"
BASE = f"https://huggingface.co/{REPO}/resolve/main/"
FILES = [
    "config.json",
    "tokenizer_config.json",
    "tokenizer.json",
    "vocab.txt",
    "special_tokens_map.json",
    "pytorch_model.bin",
    "sentence_bert_config.json",
    "modules.json",
    "1_Pooling/config.json",
    "config_sentence_transformers.json",
]


def main() -> int:
    cache_root = Path.home() / ".cache" / "huggingface" / "hub" / "models--cointegrated--rubert-tiny2"
    snap_root = cache_root / "snapshots" / "main"
    snap_root.mkdir(parents=True, exist_ok=True)
    (cache_root / "refs").mkdir(parents=True, exist_ok=True)

    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
    proxies = {"https": proxy, "http": proxy} if proxy else None
    print(f"Proxy: {proxy or '(none)'}")

    for f in FILES:
        url = BASE + f
        target = snap_root / f
        target.parent.mkdir(parents=True, exist_ok=True)
        print(f"GET {f}...", end=" ", flush=True)
        try:
            r = requests.get(url, timeout=30, stream=True, proxies=proxies)
            if r.status_code != 200:
                print(f"SKIP {r.status_code}")
                continue
            total = 0
            with open(target, "wb") as fp:
                for chunk in r.iter_content(64 * 1024):
                    fp.write(chunk)
                    total += len(chunk)
            print(f"{total / 1024 / 1024:.2f} MB")
        except Exception as e:
            print("FAIL", e)

    (cache_root / "refs" / "main").write_text("main", encoding="utf-8")
    print("Done. Snapshot:", snap_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
