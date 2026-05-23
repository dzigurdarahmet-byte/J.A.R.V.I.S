"""Smoke test — проверка установленных deps и базовой работоспособности."""

import sys

# 1. Импорты ключевых модулей
import fastapi
import redis
import anthropic
import aiogram
import torch
import structlog
import pydantic
import faster_whisper

# 2. Версии
print("=== Установленные пакеты ===")
print(f"Python:         {sys.version.split()[0]}")
print(f"fastapi:        {fastapi.__version__}")
print(f"redis:          {redis.__version__}")
print(f"anthropic:      {anthropic.__version__}")
print(f"aiogram:        {aiogram.__version__}")
print(f"torch:          {torch.__version__}")
print(f"structlog:      {structlog.__version__}")
print(f"pydantic:       {pydantic.VERSION}")
print(f"faster_whisper: {faster_whisper.__version__}")
print(f"CUDA available: {torch.cuda.is_available()}")
print(f"CPU threads:    {torch.get_num_threads()}")

# 3. Проверка конфига
print()
print("=== Config (читает .env) ===")
sys.path.insert(0, "..")
from core.config import settings  # type: ignore

print(f"jarvis_env:          {settings.jarvis_env}")
print(f"jarvis_owner_name:   {settings.jarvis_owner_name}")
print(f"jarvis_owner_addr:   {settings.jarvis_owner_addr}")
print(f"redis_url:           {settings.redis_url}")
print(f"telegram_bot_username: {settings.telegram_bot_username}")
print(f"anthropic_api_key:   {'SET' if settings.anthropic_api_key.get_secret_value() else 'EMPTY'}")
print(f"telegram_bot_token:  {'SET' if settings.telegram_bot_token.get_secret_value() else 'EMPTY'}")
print(f"openweather_api_key: {'SET' if settings.openweather_api_key and settings.openweather_api_key.get_secret_value() else 'EMPTY'}")

# 4. Подключение к Redis
print()
print("=== Redis ping (asyncio) ===")
import asyncio
import redis.asyncio as aioredis


async def test_redis():
    r = aioredis.from_url(settings.redis_url)
    pong = await r.ping()
    info = await r.info("server")
    await r.aclose()
    return pong, info


pong, info = asyncio.run(test_redis())
print(f"PING:           {pong}")
print(f"Redis version:  {info.get('redis_version')}")
print(f"Redis mode:     {info.get('redis_mode')}")

# 5. Anthropic API (не делаем реальный запрос — только инициализация клиента)
print()
print("=== Anthropic SDK ===")
client = anthropic.Anthropic(api_key=settings.anthropic_api_key.get_secret_value())
print(f"Client OK:      {client.api_key[:15]}…")

print()
print("ВСЁ РАБОТАЕТ. Phase 1 base stack готов.")
