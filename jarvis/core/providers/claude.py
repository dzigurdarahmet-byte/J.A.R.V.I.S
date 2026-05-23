"""Claude (Anthropic) provider — primary brain Джарвиса на MVP.

Тонкая обёртка над anthropic.AsyncAnthropic с retry, structured logging,
маскировкой ошибок и контроль расхода токенов.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, Final

from anthropic import APIConnectionError, APIStatusError, AsyncAnthropic, RateLimitError

from core.logging import get_logger
from core.providers.base import Message

logger = get_logger(__name__)

DEFAULT_MODEL: Final = "claude-sonnet-4-6"
DEFAULT_MAX_TOKENS: Final = 1024
DEFAULT_TEMPERATURE: Final = 1.0
MAX_RETRIES: Final = 3
BASE_BACKOFF_SEC: Final = 1.0
MAX_TOOL_ITERATIONS: Final = 4  # защита от бесконечного цикла tool_use

# Тип runner-а для tool_use: (tool_name, tool_args) -> текстовый результат
ToolRunner = Callable[[str, dict[str, Any]], Awaitable[str]]


class ClaudeProvider:
    """Async-обёртка Anthropic SDK."""

    name = "claude-sonnet-4.6"

    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_MODEL,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> None:
        if not api_key:
            raise ValueError("ClaudeProvider requires non-empty api_key")
        self._client = AsyncAnthropic(api_key=api_key)
        self._model = model
        self._default_max_tokens = max_tokens

    async def chat(
        self,
        messages: list[Message],
        system: str | None = None,
        max_tokens: int | None = None,
        temperature: float = DEFAULT_TEMPERATURE,
    ) -> str:
        """Получить ответ Claude. Делает до 3 попыток на сетевые/rate-limit ошибки."""
        anthropic_messages = [{"role": m.role, "content": m.content} for m in messages]
        kwargs: dict[str, object] = {
            "model": self._model,
            "messages": anthropic_messages,
            "max_tokens": max_tokens or self._default_max_tokens,
            "temperature": temperature,
        }
        if system:
            kwargs["system"] = system

        attempt = 0
        last_exc: Exception | None = None
        while attempt < MAX_RETRIES:
            attempt += 1
            try:
                response = await self._client.messages.create(**kwargs)  # type: ignore[arg-type]
                # Anthropic возвращает массив content-блоков; берём text-блоки и склеиваем.
                text_chunks: list[str] = []
                for block in response.content:
                    if getattr(block, "type", None) == "text":
                        text_chunks.append(block.text)
                result = "".join(text_chunks).strip()
                logger.info(
                    "claude_chat_ok",
                    model=self._model,
                    input_tokens=response.usage.input_tokens,
                    output_tokens=response.usage.output_tokens,
                    attempt=attempt,
                )
                return result
            except RateLimitError as e:
                last_exc = e
                wait = BASE_BACKOFF_SEC * (2 ** (attempt - 1))
                logger.warning("claude_rate_limited", attempt=attempt, wait_sec=wait)
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(wait)
            except APIConnectionError as e:
                last_exc = e
                wait = BASE_BACKOFF_SEC * (2 ** (attempt - 1))
                logger.warning("claude_conn_error", attempt=attempt, wait_sec=wait, error=str(e))
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(wait)
            except APIStatusError as e:
                # 4xx — клиентская ошибка, нет смысла ретраить
                logger.error("claude_api_error", status=e.status_code, message=str(e))
                raise

        assert last_exc is not None
        logger.error("claude_exhausted_retries", attempts=MAX_RETRIES, last_error=str(last_exc))
        raise last_exc

    async def chat_with_tools(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]],
        tool_runner: ToolRunner,
        system: str | None = None,
        max_tokens: int | None = None,
        temperature: float = DEFAULT_TEMPERATURE,
        max_iterations: int = MAX_TOOL_ITERATIONS,
    ) -> tuple[str, list[dict[str, Any]]]:
        """Чат с tool-use (L2).

        Claude может вызывать функции из `tools`. Каждый tool_use мы выполняем
        через `tool_runner(name, args) -> str` и возвращаем результат Claude'у.
        Цикл крутится до max_iterations или пока Claude не вернёт чистый текст.

        Returns:
            (final_text, tool_calls_log) — финальный ответ + лог вызовов скиллов
            (для bus и observability).
        """
        # Конвертим Message в anthropic-формат
        anthropic_messages: list[dict[str, Any]] = [
            {"role": m.role, "content": m.content} for m in messages
        ]
        kwargs: dict[str, Any] = {
            "model": self._model,
            "max_tokens": max_tokens or self._default_max_tokens,
            "temperature": temperature,
            "tools": tools,
        }
        if system:
            kwargs["system"] = system

        tool_calls_log: list[dict[str, Any]] = []

        for iteration in range(max_iterations):
            response = await self._client.messages.create(
                messages=anthropic_messages, **kwargs,  # type: ignore[arg-type]
            )
            logger.info(
                "claude_tools_iter",
                iteration=iteration + 1,
                stop_reason=response.stop_reason,
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
            )

            # Собираем text и tool_use блоки из ответа
            assistant_blocks: list[dict[str, Any]] = []
            text_parts: list[str] = []
            tool_uses: list[Any] = []
            for block in response.content:
                btype = getattr(block, "type", None)
                if btype == "text":
                    text_parts.append(block.text)
                    assistant_blocks.append({"type": "text", "text": block.text})
                elif btype == "tool_use":
                    assistant_blocks.append({
                        "type": "tool_use",
                        "id": block.id,
                        "name": block.name,
                        "input": block.input,
                    })
                    tool_uses.append(block)

            if response.stop_reason != "tool_use" or not tool_uses:
                # Финальный текстовый ответ
                return "".join(text_parts).strip(), tool_calls_log

            # Иначе — нужно выполнить tool calls и продолжить
            anthropic_messages.append({"role": "assistant", "content": assistant_blocks})

            # Параллельно выполняем все tool_use из этого хода
            async def _exec(tu: Any) -> tuple[str, str]:
                try:
                    args = dict(tu.input) if tu.input else {}
                    result = await tool_runner(tu.name, args)
                    return tu.id, result
                except Exception as e:
                    logger.error("tool_runner_error", tool=tu.name, error=str(e))
                    return tu.id, f"[tool error: {e}]"

            results = await asyncio.gather(*(_exec(tu) for tu in tool_uses))

            tool_results_blocks: list[dict[str, Any]] = []
            for tu, (tu_id, result_text) in zip(tool_uses, results, strict=True):
                tool_calls_log.append({
                    "tool": tu.name,
                    "input": dict(tu.input) if tu.input else {},
                    "result": result_text[:200],
                })
                tool_results_blocks.append({
                    "type": "tool_result",
                    "tool_use_id": tu_id,
                    "content": result_text,
                })

            anthropic_messages.append({"role": "user", "content": tool_results_blocks})

        # Превысили лимит итераций — отдаём то что есть
        logger.warning("claude_tools_max_iterations", max_iter=max_iterations)
        return (
            "Босс, я застрял в цикле вызовов инструментов — прерываюсь.",
            tool_calls_log,
        )

    async def chat_with_image(
        self,
        image_bytes: bytes,
        prompt: str,
        media_type: str = "image/jpeg",
        system: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 1.0,
    ) -> str:
        """Multimodal-запрос: картинка + текстовый промпт.

        Возвращает текст-описание / ответ Claude.
        Размер картинки — до 5MB у Anthropic, до 8000×8000 px.
        """
        import base64

        b64 = base64.b64encode(image_bytes).decode("ascii")
        content = [
            {
                "type": "image",
                "source": {"type": "base64", "media_type": media_type, "data": b64},
            },
            {"type": "text", "text": prompt},
        ]
        kwargs: dict[str, object] = {
            "model": self._model,
            "messages": [{"role": "user", "content": content}],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if system:
            kwargs["system"] = system

        response = await self._client.messages.create(**kwargs)  # type: ignore[arg-type]
        text_parts: list[str] = []
        for block in response.content:
            if getattr(block, "type", None) == "text":
                text_parts.append(block.text)
        logger.info(
            "claude_vision_ok",
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            image_size_kb=round(len(image_bytes) / 1024),
        )
        return "".join(text_parts).strip()

    async def healthcheck(self) -> bool:
        """Минимальный chat для проверки доступности (1 токен)."""
        try:
            await self.chat(
                messages=[Message(role="user", content="ping")],
                max_tokens=1,
                temperature=0.0,
            )
            return True
        except Exception as e:
            logger.warning("claude_healthcheck_failed", error=str(e))
            return False

    async def close(self) -> None:
        """Закрыть HTTP-клиент."""
        await self._client.close()
