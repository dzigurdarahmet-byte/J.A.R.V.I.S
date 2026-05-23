"""Sync-only repro — без asyncio, без to_thread, без нашего враппера.

Если зависнет — значит SDK сам по себе блокирует. Если упадёт — увидим
реальную traceback (а не stringified-ошибку из FastAPI HTTPException).

Запуск:
    cd "C:\\Users\\Staho\\Documents\\Claude\\Projects\\ДЖАРВИС (2)\\jarvis"
    .\\.venv\\Scripts\\python.exe scripts\\diag_tts_sync.py
"""
from __future__ import annotations

import os
import sys
import time
import traceback
import warnings
from pathlib import Path

warnings.filterwarnings("ignore", category=DeprecationWarning)
HERE = Path(__file__).resolve().parent.parent  # ...\jarvis
sys.path.insert(0, str(HERE))
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

print("=== diag_tts_sync ===", flush=True)
print(f"python: {sys.version.split()[0]}", flush=True)

# --- ключи из .env через settings ---
from core.config import settings
api_key = settings.yandex_api_key.get_secret_value()
folder_id = settings.yandex_folder_id
print(f"api_key prefix: {api_key[:8]}...  folder: {folder_id}", flush=True)

# --- step 1: import (быстро) ---
t0 = time.time()
print("\n[1/4] import speechkit ...", flush=True)
from speechkit import configure_credentials, creds, model_repository
print(f"    OK ({time.time() - t0:.2f}s)", flush=True)

# --- step 2: configure (обычно мгновенно — просто save в module state) ---
t0 = time.time()
print("\n[2/4] configure_credentials ...", flush=True)
try:
    configure_credentials(yandex_credentials=creds.YandexCredentials(api_key=api_key))
    print(f"    OK ({time.time() - t0:.2f}s)", flush=True)
except Exception:
    print(f"    [FAIL] после {time.time() - t0:.2f}s:", flush=True)
    traceback.print_exc()
    sys.exit(2)

# --- step 3: synthesis_model() — тут возможны network вызовы ---
t0 = time.time()
print("\n[3/4] model_repository.synthesis_model() ... (если висит >30 сек — Ctrl+C)", flush=True)
try:
    synth = model_repository.synthesis_model()
    print(f"    OK ({time.time() - t0:.2f}s) — type={type(synth).__name__}", flush=True)
except Exception:
    print(f"    [FAIL] после {time.time() - t0:.2f}s:", flush=True)
    traceback.print_exc()
    sys.exit(3)

synth.voice = "alena"
synth.role = "neutral"
synth.speed = 1.0
print(f"    voice/role/speed assigned", flush=True)

# --- step 4: synthesize — это реальный network вызов ---
t0 = time.time()
print("\n[4/4] synth.synthesize('Привет, Босс.') ...", flush=True)
try:
    result = synth.synthesize("Привет, Босс.", raw_format=False)
    print(f"    OK ({time.time() - t0:.2f}s) — type={type(result).__name__}", flush=True)
    try:
        print(f"    frame_rate={result.frame_rate}, len_raw={len(result.raw_data)}", flush=True)
    except Exception:
        pass
    print("\n[OK] TTS полностью работает — баг где-то в нашей обвязке", flush=True)
except Exception as e:
    print(f"    [FAIL] после {time.time() - t0:.2f}s:", flush=True)
    print(f"    type:  {type(e).__name__}", flush=True)
    print(f"    repr:  {e!r}", flush=True)
    print(f"    str:   {e}", flush=True)
    print("\n    --- full traceback ---", flush=True)
    traceback.print_exc()
    sys.exit(4)
