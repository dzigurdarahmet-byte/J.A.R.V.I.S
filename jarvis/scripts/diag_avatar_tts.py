"""Диагностика TTS пути для /api/avatar/speak.

Воспроизводит ровно тот же путь что HUD: YandexSpeechKitTTS.preload() +
synthesize("Привет"), плюс text_to_visemes() — но без FastAPI обёртки, чтобы
увидеть настоящую traceback из speechkit SDK.

Запуск:
    cd "C:\\Users\\Staho\\Documents\\Claude\\Projects\\ДЖАРВИС (2)\\jarvis"
    .\\.venv\\Scripts\\python.exe scripts\\diag_avatar_tts.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import traceback
from pathlib import Path

# Запускаемся из репо/jarvis — добавляем cwd чтобы импорты core.* работали
HERE = Path(__file__).resolve().parent.parent  # ...\jarvis
sys.path.insert(0, str(HERE))

# Убедимся, что .env подхватился — config.settings грузит его сам
os.environ.setdefault("PYTHONIOENCODING", "utf-8")


async def main() -> int:
    print("=== diag_avatar_tts ===")
    print(f"python: {sys.version.split()[0]}")
    print(f"cwd:    {Path.cwd()}")
    print(f"jarvis: {HERE}")

    # 1) Settings + ключи
    try:
        from core.config import settings
        yk = settings.yandex_api_key
        yf = settings.yandex_folder_id
        has_key = bool(yk and yk.get_secret_value())
        print(f"yandex_api_key: {'set' if has_key else 'MISSING'}")
        print(f"yandex_folder_id: {'set' if yf else 'MISSING'}")
        if not (has_key and yf):
            print("\n[FAIL] нет Yandex ключей в .env — synthesize не запустится")
            return 2
    except Exception:
        print("\n[FAIL] не смог загрузить core.config.settings:")
        traceback.print_exc()
        return 3

    # 2) Версии библиотек
    print("\n--- versions ---")
    for mod_name in ("speechkit", "pydantic", "pydub", "fastapi", "scipy", "httpx", "grpc"):
        try:
            mod = __import__(mod_name)
            ver = getattr(mod, "__version__", getattr(mod, "VERSION", "?"))
            print(f"{mod_name}: {ver}")
        except Exception as e:
            print(f"{mod_name}: NOT INSTALLED ({e.__class__.__name__})")

    # 2b) Env vars — proxy и SSL могут заворачивать HTTP-трафик SDK куда не надо
    print("\n--- env (proxy/ssl) ---")
    proxy_vars = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
                  "http_proxy", "https_proxy", "all_proxy", "no_proxy",
                  "REQUESTS_CA_BUNDLE", "SSL_CERT_FILE", "CURL_CA_BUNDLE",
                  "GRPC_PROXY", "GRPC_PROXY_EXP")
    found_any = False
    for var in proxy_vars:
        val = os.environ.get(var)
        if val:
            print(f"  {var}={val}")
            found_any = True
    if not found_any:
        print("  (proxy/SSL переменных нет — это норм)")

    # 3) Преднагрузка
    print("\n--- preload ---")
    try:
        from core.voice.tts_yandex import YandexSpeechKitTTS
    except Exception:
        print("[FAIL] не смог импортировать YandexSpeechKitTTS:")
        traceback.print_exc()
        return 4

    tts = YandexSpeechKitTTS(
        api_key=yk.get_secret_value(),
        folder_id=yf,
        voice="alena",
    )
    try:
        await tts.preload()
        print("preload: OK")
    except Exception:
        print("[FAIL] preload упал — это уже репро:")
        traceback.print_exc()
        return 5

    # 4) Синтез
    print("\n--- synthesize ---")
    try:
        audio = await tts.synthesize("Привет, Босс.")
        print(f"synthesize: OK — {audio.shape[0]} samples @ {tts.sample_rate} Hz")
        dur = audio.shape[0] / tts.sample_rate
        print(f"длительность: {dur:.2f} сек")
    except Exception as e:
        print("[FAIL] synthesize упал — это РЕПРО ошибки HUD:")
        print(f"  type:  {type(e).__name__}")
        print(f"  repr:  {e!r}")
        print(f"  str:   {e}")
        print("  --- full traceback ---")
        traceback.print_exc()
        return 6

    # 5) Visemes
    print("\n--- visemes ---")
    try:
        from core.voice.viseme import text_to_visemes
        v = text_to_visemes("Привет, Босс.", dur)
        print(f"visemes: OK — {len(v)} точек")
    except Exception:
        print("[FAIL] text_to_visemes упал:")
        traceback.print_exc()
        return 7

    print("\n[OK] вся цепочка прошла — значит проблема в FastAPI-обвязке, а не в SDK")
    return 0


if __name__ == "__main__":
    try:
        rc = asyncio.run(main())
    except KeyboardInterrupt:
        rc = 130
    sys.exit(rc)
