"""Низкоуровневый dump запроса к Yandex SpeechKit TTS — что отдаёт API."""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import requests  # noqa: E402

from core.config import settings  # noqa: E402

API_KEY = settings.yandex_api_key.get_secret_value()
FOLDER = settings.yandex_folder_id

# Без прокси (Yandex РФ)
for k in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy"):
    os.environ.pop(k, None)

URL = "https://tts.api.cloud.yandex.net/speech/v1/synthesize"
HEADERS = {"Authorization": f"Api-Key {API_KEY}"}
DATA = {
    "text": "Привет, Босс.",
    "voice": "alena",
    "folderId": FOLDER,
    "format": "lpcm",
    "sampleRateHertz": 48000,
    "lang": "ru-RU",
}
print(f"POST {URL}")
print(f"DATA: {DATA}")
r = requests.post(URL, headers=HEADERS, data=DATA, timeout=15, proxies={"http": None, "https": None})
print(f"Status: {r.status_code}")
print(f"Headers: {dict(r.headers)}")
if r.status_code != 200:
    print(f"Body: {r.text[:500]}")
else:
    print(f"Body bytes: {len(r.content)}")
