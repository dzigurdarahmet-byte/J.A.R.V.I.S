"""OllamaProvider — локальная LLM через Ollama (http://localhost:11434).

Ollama — простой self-hosted runtime для LLM (Llama 3, Qwen, Mistral и др.).
Установка: https://ollama.com/download (Windows installer).
Запуск модели: `ollama pull qwen2.5:7b` → `ollama serve` (стартует автоматически).

Используется как 3-й уровень fallback в SmartProvider:
    Claude (primary) → YandexGPT (secondary) → Ollama (offline tertiary)

Если Ollama не установлен / не запущен — healthcheck вернёт False, и
SmartProvider молча пропустит этого провайдера.

API: совместимое с OpenAI /v1/chat/completions; используем native /api/chat
(проще и стабильнее на разных версиях Ollama).
"""

from __future__ import annotations

from typing import Final

import httpx

from core.logging import get_logger
from core.providers.base import Message

logger = get_logger(__name__)

DEFAULT_BASE_URL: Final = "http://localhost:11434"
DEFAULT_MODEL: Final = "qwen2.5:7b"  # хорошо знает русский, 5GB веса
REQUEST_TIMEOUT_SEC: Final = 60.0  # local inference на CPU может занять 30+ сек
HEALTHCHECK_TIMEOUT_SEC: Final = 2.0


class OllamaProvider:
    """LLM-провайдер для локального Ollama runtime."""

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        model: str = DEFAULT_MODEL,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._client = httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SEC)
        self.name = f"ollama:{model}"

    async def chat(
        self,
        messages: list[Message],
        system: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 1.0,
    ) -> str:
        """Отправить диалог в локальную LLM. Возвращает финальный текст."""
        # Преобразуем history в Ollama-формат
        ollama_msgs: list[dict[str, str]] = []
        if system:
            ollama_msgs.append({"role": "system", "content": system})
        for m in messages:
            ollama_msgs.append({"role": m.role, "content": m.content})

        payload = {
            "model": self._model,
            "messages": ollama_msgs,
            "stream": False,
            "options": {
                "temperature": float(temperature),
                "num_predict": int(max_tokens),
            },
        }

        try:
            r = await self._client.post(
                f"{self._base_url}/api/chat",
                json=payload,
            )
            r.raise_for_status()
            data = r.json()
        except httpx.ConnectError as e:
            logger.warning("ollama_offline", url=self._base_url, error=str(e)[:100])
            raise
        except httpx.ReadTimeout as e:
            logger.warning("ollama_timeout", model=self._model, error=str(e)[:100])
            raise
        except httpx.HTTPStatusError as e:
            logger.error(
                "ollama_http_error",
                status=e.response.status_code,
                body=e.response.text[:200],
            )
            raise

        # Ollama /api/chat возвращает {message: {role, content}, ...}
        text = (data.get("message") or {}).get("content", "").strip()
        # Сбор meta для отладки
        eval_count = data.get("eval_count")
        prompt_eval_count = data.get("prompt_eval_count")
        total_duration_ns = data.get("total_duration", 0)
        logger.info(
            "ollama_chat_ok",
            model=self._model,
            chars=len(text),
            input_tokens=prompt_eval_count,
            output_tokens=eval_count,
            duration_sec=round(total_duration_ns / 1e9, 2) if total_duration_ns else None,
        )
        return text

    async def healthcheck(self) -> bool:
        """Жив ли Ollama runtime и доступна ли модель."""
        try:
            r = await self._client.get(
                f"{self._base_url}/api/tags",
                timeout=HEALTHCHECK_TIMEOUT_SEC,
            )
            if r.status_code != 200:
                return False
            tags = r.json().get("models", [])
            # Имена в Ollama: "qwen2.5:7b" → проверяем точное совпадение или префикс
            available = {m.get("name", "") for m in tags}
            model_ok = self._model in available or any(
                name.startswith(self._model.split(":")[0]) for name in available
            )
            if not model_ok and available:
                logger.warning(
                    "ollama_model_not_pulled",
                    requested=self._model,
                    available=sorted(available),
                    hint=f"запусти: ollama pull {self._model}",
                )
            return model_ok
        except (httpx.ConnectError, httpx.ReadTimeout, httpx.RequestError):
            return False
        except Exception as e:  # noqa: BLE001
            logger.warning("ollama_healthcheck_unexpected", error=str(e)[:100])
            return False

    async def close(self) -> None:
        await self._client.aclose()
