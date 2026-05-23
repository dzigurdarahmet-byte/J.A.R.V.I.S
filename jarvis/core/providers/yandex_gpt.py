"""YandexGPT provider — fallback brain Джарвиса.

REST API: https://yandex.cloud/ru/docs/foundation-models/concepts/yandexgpt/
Стоимость: yandexgpt — ~0.40₽/1000 токенов синхронно (на 2026 год).

Тот же интерфейс, что у ClaudeProvider, чтобы Router мог свободно
переключаться. Используется как fallback (если Claude недоступен)
или как primary для коротких диалогов на русском.
"""

from __future__ import annotations

import asyncio
from typing import Final

import httpx

from core.logging import get_logger
from core.providers.base import Message

logger = get_logger(__name__)

API_URL: Final = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"
DEFAULT_MODEL: Final = "yandexgpt/latest"  # full-size pro
DEFAULT_MAX_TOKENS: Final = 1024
DEFAULT_TEMPERATURE: Final = 0.6
MAX_RETRIES: Final = 3
BASE_BACKOFF_SEC: Final = 1.0
REQUEST_TIMEOUT_SEC: Final = 30.0


class YandexGPTProvider:
    """Async-обёртка над YandexGPT REST API."""

    name = "yandexgpt"

    def __init__(
        self,
        api_key: str,
        folder_id: str,
        model: str = DEFAULT_MODEL,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> None:
        if not api_key:
            raise ValueError("YandexGPTProvider requires non-empty api_key")
        if not folder_id:
            raise ValueError("YandexGPTProvider requires non-empty folder_id")
        self._api_key = api_key
        self._folder_id = folder_id
        # model_uri = "gpt://{folder_id}/{model}"
        self._model_uri = f"gpt://{folder_id}/{model}"
        self._default_max_tokens = max_tokens
        self._client = httpx.AsyncClient(
            timeout=REQUEST_TIMEOUT_SEC,
            headers={
                "Authorization": f"Api-Key {api_key}",
                "Content-Type": "application/json",
            },
        )

    async def chat(
        self,
        messages: list[Message],
        system: str | None = None,
        max_tokens: int | None = None,
        temperature: float = DEFAULT_TEMPERATURE,
    ) -> str:
        """Получить ответ YandexGPT. До 3 попыток на 5xx / network."""
        yc_messages: list[dict[str, str]] = []
        if system:
            yc_messages.append({"role": "system", "text": system})
        for m in messages:
            # YandexGPT использует role="user"/"assistant", тот же набор что у нас
            yc_messages.append({"role": m.role, "text": m.content})

        payload = {
            "modelUri": self._model_uri,
            "completionOptions": {
                "stream": False,
                "temperature": temperature,
                "maxTokens": str(max_tokens or self._default_max_tokens),
            },
            "messages": yc_messages,
        }

        attempt = 0
        last_exc: Exception | None = None
        while attempt < MAX_RETRIES:
            attempt += 1
            try:
                r = await self._client.post(API_URL, json=payload)
                if r.status_code >= 500:
                    raise httpx.HTTPStatusError(
                        f"server {r.status_code}", request=r.request, response=r,
                    )
                r.raise_for_status()
                data = r.json()
                alternatives = data.get("result", {}).get("alternatives") or []
                if not alternatives:
                    raise RuntimeError("yandexgpt empty alternatives")
                text = (alternatives[0].get("message") or {}).get("text") or ""
                usage = data.get("result", {}).get("usage") or {}
                logger.info(
                    "yandexgpt_chat_ok",
                    model=self._model_uri,
                    input_tokens=usage.get("inputTextTokens"),
                    output_tokens=usage.get("completionTokens"),
                    attempt=attempt,
                )
                return text.strip()
            except (httpx.ConnectError, httpx.ReadTimeout, httpx.HTTPStatusError) as e:
                last_exc = e
                wait = BASE_BACKOFF_SEC * (2 ** (attempt - 1))
                logger.warning(
                    "yandexgpt_retry",
                    attempt=attempt,
                    wait_sec=wait,
                    error=str(e)[:200],
                )
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(wait)
            except httpx.HTTPError as e:
                # 4xx — клиентская ошибка, не ретраим
                logger.error("yandexgpt_client_error", error=str(e)[:200])
                raise

        assert last_exc is not None
        logger.error("yandexgpt_exhausted_retries", attempts=MAX_RETRIES, last=str(last_exc)[:200])
        raise last_exc

    async def healthcheck(self) -> bool:
        """Минимальный chat для проверки доступа."""
        try:
            await self.chat(
                messages=[Message(role="user", content="ping")],
                max_tokens=5,
                temperature=0.0,
            )
            return True
        except Exception as e:
            logger.warning("yandexgpt_healthcheck_failed", error=str(e))
            return False

    async def close(self) -> None:
        await self._client.aclose()
