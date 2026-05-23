"""Deepseek LLM provider — OpenAI-compatible API.

Model: deepseek-chat (V3.x) — общая модель уровня Sonnet, дешевле в 3-15 раз.
Альтернативно: deepseek-reasoner (chain-of-thought) — для сложных задач.

API endpoint: https://api.deepseek.com/v1 (формат openai/v1).
Auth: Bearer token из DEEPSEEK_API_KEY.

Без vision и без native tool-use (есть только function calling, не Anthropic-style).
Для tool-use остаётся primary Claude.
"""
from __future__ import annotations

import asyncio
from typing import Final

import httpx

from core.logging import get_logger
from core.providers.base import Message

logger = get_logger(__name__)

DEEPSEEK_BASE: Final = "https://api.deepseek.com/v1"
DEFAULT_MODEL: Final = "deepseek-chat"
REQUEST_TIMEOUT: Final = 30.0
MAX_RETRIES: Final = 2
BASE_BACKOFF_SEC: Final = 1.0


class DeepseekProvider:
    """OpenAI-compatible Deepseek client."""

    name = "deepseek-chat"

    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_MODEL,
        base_url: str = DEEPSEEK_BASE,
    ) -> None:
        if not api_key:
            raise ValueError("DeepseekProvider: пустой api_key")
        self._api_key = api_key
        self._model = model
        self._base = base_url.rstrip("/")
        self._client: httpx.AsyncClient | None = None

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=REQUEST_TIMEOUT,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
            )
        return self._client

    async def chat(
        self,
        messages: list[Message],
        system: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 1.0,
    ) -> str:
        client = await self._ensure_client()
        oai_messages = []
        if system:
            oai_messages.append({"role": "system", "content": system})
        for m in messages:
            oai_messages.append({"role": m.role, "content": m.content})

        payload = {
            "model": self._model,
            "messages": oai_messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": False,
        }

        last_exc: Exception | None = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                r = await client.post(f"{self._base}/chat/completions", json=payload)
                r.raise_for_status()
                data = r.json()
                choices = data.get("choices") or []
                if not choices:
                    raise RuntimeError(f"deepseek empty choices: {data}")
                text = (choices[0].get("message") or {}).get("content", "")
                usage = data.get("usage") or {}
                logger.info(
                    "deepseek_chat_ok",
                    attempt=attempt,
                    model=self._model,
                    input_tokens=usage.get("prompt_tokens", 0),
                    output_tokens=usage.get("completion_tokens", 0),
                )
                return text
            except httpx.HTTPStatusError as e:
                last_exc = e
                if e.response.status_code in (400, 401, 403):
                    # auth / bad request — не ретраим
                    logger.error("deepseek_api_error", status=e.response.status_code, body=e.response.text[:200])
                    raise
                logger.warning("deepseek_retry", attempt=attempt, status=e.response.status_code)
            except Exception as e:
                last_exc = e
                logger.warning("deepseek_retry", attempt=attempt, error=str(e)[:120])
            await asyncio.sleep(BASE_BACKOFF_SEC * attempt)

        assert last_exc is not None
        raise last_exc

    async def healthcheck(self) -> bool:
        """Лёгкий ping — HEAD без потребления токенов."""
        try:
            client = await self._ensure_client()
            r = await client.head(f"{self._base}/models", timeout=5.0)
            return r.status_code < 500
        except Exception as e:
            logger.debug("deepseek_healthcheck_failed", error=str(e))
            return False

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
