"""Низкоуровневый тест Yandex Embeddings API — что именно говорит сервер."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402

from core.config import settings  # noqa: E402

API_KEY = settings.yandex_api_key.get_secret_value() if settings.yandex_api_key else ""
FOLDER = settings.yandex_folder_id or ""

URL = "https://llm.api.cloud.yandex.net/foundationModels/v1/textEmbedding"
HEADERS = {
    "Authorization": f"Api-Key {API_KEY}",
    "x-folder-id": FOLDER,
}

print(f"Key prefix: {API_KEY[:10]}…")
print(f"Folder: {FOLDER}")
print(f"URL: {URL}")
print()

# Пробуем БЕЗ прокси (Yandex Cloud РФ — не блокируется)
os.environ.pop("HTTPS_PROXY", None)
os.environ.pop("HTTP_PROXY", None)
os.environ.pop("https_proxy", None)
os.environ.pop("http_proxy", None)

payload = {
    "modelUri": f"emb://{FOLDER}/text-search-doc/latest",
    "text": "Это тестовая фраза для проверки эмбеддингов.",
}
print(f"Payload: {json.dumps(payload, ensure_ascii=False)}")
print()

with httpx.Client(timeout=15.0, trust_env=False) as client:
    r = client.post(URL, json=payload, headers=HEADERS)
    print(f"Status: {r.status_code}")
    print(f"Body: {r.text[:500]}")
