"""Telegram bot — первый рабочий канал Джарвиса.

На MVP делаем прямую цепочку: Boss → Telegram → Claude → Telegram → Boss.
Event Bus подключим на следующем шаге.

Безопасность:
- Owner whitelist: только TELEGRAM_OWNER_CHAT_ID может общаться с ботом.
  Если в .env не указан — первый написавший становится owner (логируем chat_id).
- PromptGuard на входе.
- Любая ошибка LLM → нейтральное "Босс, у меня технические проблемы" + log.
"""

from __future__ import annotations

import asyncio
import uuid
from contextvars import ContextVar
from pathlib import Path
from typing import Final

import structlog
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.types import Message as TgMessage

from core.config import settings
from core.event_bus import EventType, JarvisEvent, bus
from core.logging import get_logger, setup_logging
from core.memory import MemoryManager
from core.providers import ClaudeProvider, Message
from core.router import Router
from core.security import PromptGuard
from core.skills import register_all_builtin

logger = get_logger(__name__)
_request_id_var: ContextVar[str] = ContextVar("request_id", default="")

WORKSPACE_DIR: Final[Path] = Path(__file__).resolve().parents[2] / "workspace"

BASE_PROMPT: Final[str] = (
    "Ты — J.A.R.V.I.S., персональный ассистент Босса. "
    "Стиль: уважительный, лаконичный, остроумный (как JARVIS в Marvel). "
    "Обращайся 'Босс'. Никогда не 'вы', 'сэр', 'господин'. "
    "Отвечай по делу, без воды. Если можно тремя словами — отвечай тремя."
)


def build_system_prompt(memory: MemoryManager) -> str:
    """SOUL/USER/MEMORY всегда подкладываются в system prompt."""
    addendum = memory.snapshot().render_system_addendum()
    if addendum:
        return f"{BASE_PROMPT}\n\n{addendum}"
    return BASE_PROMPT


HISTORY_LIMIT: Final = 12  # последних сообщений в контексте на чат


class ChatHistory:
    """Per-chat история сообщений в памяти текущего процесса.

    Hot cache над MemoryManager: первый запрос на chat_id подгружает
    последние N exchanges из daily-лога (восстановление контекста после
    рестарта). Дальше работает как sliding-window.
    """

    def __init__(self, memory: MemoryManager, limit: int = HISTORY_LIMIT) -> None:
        self._memory = memory
        self._limit = limit
        self._by_chat: dict[int, list[Message]] = {}
        self._warmed: set[int] = set()

    def _warm(self, chat_id: int) -> None:
        """Подгрузить недавний контекст из daily-лога один раз на chat_id."""
        if chat_id in self._warmed:
            return
        self._warmed.add(chat_id)
        recent = self._memory.load_recent_context(limit_messages=self._limit)
        if recent:
            self._by_chat[chat_id] = recent
            logger.info("telegram_history_warmed", chat_id=chat_id, messages=len(recent))

    def get(self, chat_id: int) -> list[Message]:
        self._warm(chat_id)
        return list(self._by_chat.get(chat_id, []))

    def append(self, chat_id: int, msg: Message) -> None:
        self._warm(chat_id)
        history = self._by_chat.setdefault(chat_id, [])
        history.append(msg)
        if len(history) > self._limit:
            self._by_chat[chat_id] = history[-self._limit:]

    def reset(self, chat_id: int) -> None:
        """Очистить in-memory сессию (daily-лог НЕ трогается)."""
        self._by_chat.pop(chat_id, None)
        self._warmed.discard(chat_id)


def _is_owner(chat_id: int) -> bool:
    """Проверка whitelist. Если owner_chat_id не задан — пропускаем первого (учится)."""
    owner = settings.telegram_owner_chat_id
    if owner is None:
        logger.warning(
            "telegram_owner_not_set",
            note="первый написавший станет owner; добавь его chat_id в .env",
            chat_id=chat_id,
        )
        return True
    return chat_id == owner


def build_bot(claude: ClaudeProvider, memory: MemoryManager):
    """Собрать aiogram Bot + Dispatcher с подключёнными хендлерами.

    Возвращает (bot, dp, alert_scheduler). alert_scheduler стартуется/стопится
    в run_bot(), но создаётся здесь — чтобы хендлеры могли дёргать
    notify_user_activity() через замыкание.
    """
    # Если задан TELEGRAM_PROXY_URL — поднимаем сессию через прокси
    # (актуально для РФ из-за блокировки api.telegram.org)
    if settings.telegram_proxy_url:
        session: AiohttpSession | None = AiohttpSession(proxy=settings.telegram_proxy_url)
        logger.info("telegram_proxy_enabled", proxy_url=settings.telegram_proxy_url)
    else:
        session = None

    bot = Bot(
        token=settings.telegram_bot_token.get_secret_value(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        session=session,  # None → дефолтная aiohttp без прокси
    )
    dp = Dispatcher()
    history = ChatHistory(memory=memory)
    router = Router(claude_provider=claude, memory=memory, base_prompt=BASE_PROMPT)
    # claude — на самом деле SmartProvider; пробрасываем как smart_provider,
    # чтобы LLMSwitcherSkill мог переключать primary LLM из Telegram-чата.
    register_all_builtin(router, memory, claude=claude, smart_provider=claude)
    # Skills, требующие Claude (регистрируем после register_all_builtin)
    from core.skills.weekly_skill import WeeklySkill
    router.register_skill(WeeklySkill(claude))

    # AlertScheduler: создаём здесь, чтобы хендлеры могли вызывать
    # notify_user_activity() и сбрасывать таймер «тишины» для care-message.
    from core.alerts import AlertScheduler
    _owner_chat = settings.telegram_owner_chat_id

    async def _alert_send(text: str) -> None:
        if not _owner_chat:
            return
        try:
            await bot.send_message(_owner_chat, text, parse_mode="Markdown")
        except Exception as e:
            logger.warning("alert_send_failed", error=str(e))

    alert_scheduler = AlertScheduler(WORKSPACE_DIR, _alert_send)

    @dp.message(CommandStart())
    async def on_start(msg: TgMessage) -> None:
        chat_id = msg.chat.id
        request_id = uuid.uuid4().hex[:12]
        _request_id_var.set(request_id)
        structlog.contextvars.bind_contextvars(request_id=request_id, chat_id=chat_id)
        logger.info("telegram_start", username=msg.from_user.username if msg.from_user else None)

        if not _is_owner(chat_id):
            await msg.answer("Доступ ограничен.")
            return

        history.reset(chat_id)
        await msg.answer(
            "Босс. Я на связи.\n\n"
            "Пиши любой вопрос или команду — отвечу через Claude. "
            "<i>/reset</i> очищает контекст разговора."
        )
        structlog.contextvars.clear_contextvars()

    @dp.message(F.text == "/reset")
    async def on_reset(msg: TgMessage) -> None:
        chat_id = msg.chat.id
        if not _is_owner(chat_id):
            return
        history.reset(chat_id)
        await msg.answer("Контекст очищен.")

    @dp.message(F.photo)
    async def on_photo(msg: TgMessage) -> None:
        """Vision: фото → Claude vision → ответ + Tier 3 память."""
        chat_id = msg.chat.id
        request_id = uuid.uuid4().hex[:12]
        _request_id_var.set(request_id)
        structlog.contextvars.bind_contextvars(
            request_id=request_id, chat_id=chat_id, channel="telegram",
        )
        try:
            if not _is_owner(chat_id):
                await msg.answer("Доступ ограничен.")
                return
            alert_scheduler.notify_user_activity()
            # Берём самое большое из присланных размеров
            photo = msg.photo[-1]
            await msg.answer("Смотрю на фото, Босс…")
            file = await bot.get_file(photo.file_id)
            buf = await bot.download_file(file.file_path)
            image_bytes = buf.read()
            logger.info("telegram_photo_received", size_kb=round(len(image_bytes) / 1024))

            caption = (msg.caption or "Опиши что на фото. Если есть текст — прочти.").strip()
            try:
                reply = await claude.chat_with_image(
                    image_bytes=image_bytes,
                    prompt=caption,
                    media_type="image/jpeg",
                    system="Ты — JARVIS, ассистент Босса. Стиль: Marvel JARVIS — уважительный, лаконичный, остроумный. Обращайся 'Босс'.",
                )
            except Exception as e:
                logger.error("telegram_vision_error", error=str(e))
                await msg.answer("Босс, технические проблемы с распознаванием фото.")
                return

            safe_reply, _ = PromptGuard.filter_output(reply)
            await msg.answer(safe_reply)

            # Publish в bus
            await bus.publish(JarvisEvent(
                type=EventType.ASSISTANT_REPLY,
                source="channel:telegram",
                channel="telegram",
                request_id=request_id,
                data={"text": safe_reply, "chat_id": chat_id, "modality": "vision"},
            ))

            # Tier 3: запишем «Босс прислал фото: <ответ>»
            import asyncio as _aio
            _aio.create_task(memory.add_to_vector(
                f"[фото от Босса] {caption}", role="user", channel="telegram",
            ))
            _aio.create_task(memory.add_to_vector(
                safe_reply, role="assistant", channel="telegram",
            ))
        finally:
            structlog.contextvars.clear_contextvars()

    @dp.message(F.text)
    async def on_text(msg: TgMessage) -> None:
        chat_id = msg.chat.id
        request_id = uuid.uuid4().hex[:12]
        _request_id_var.set(request_id)
        structlog.contextvars.bind_contextvars(
            request_id=request_id,
            chat_id=chat_id,
            channel="telegram",
        )

        try:
            if not _is_owner(chat_id):
                logger.warning("telegram_blocked_non_owner")
                await msg.answer("Доступ ограничен.")
                return

            alert_scheduler.notify_user_activity()
            user_text = msg.text or ""
            clean_text, threats = PromptGuard.sanitize_input(user_text, channel="telegram")
            if threats:
                logger.warning("telegram_input_sanitized", threats=threats)

            # Publish in bus — Web HUD и audit-логгер это увидят
            await bus.publish(JarvisEvent(
                type=EventType.USER_INPUT,
                source="channel:telegram",
                channel="telegram",
                request_id=request_id,
                data={"text": clean_text, "chat_id": chat_id},
            ))

            # typing indicator пока думаем
            await msg.bot.send_chat_action(chat_id, "typing")

            history.append(chat_id, Message(role="user", content=clean_text))
            messages = history.get(chat_id)

            try:
                reply = await router.dispatch(
                    text=clean_text,
                    history=messages,
                    channel="telegram",
                    request_id=request_id,
                )
            except Exception as e:
                logger.error("telegram_llm_error", error=str(e), error_type=type(e).__name__)
                await msg.answer("Босс, у меня технические проблемы — не дотянулся до Claude.")
                # откатываем последнее user-сообщение, чтобы не оставлять висящий контекст
                history.reset(chat_id)
                return

            safe_reply, leaks = PromptGuard.filter_output(reply)
            if leaks:
                logger.error("telegram_output_redacted", leaks=leaks)

            history.append(chat_id, Message(role="assistant", content=safe_reply))
            await msg.answer(safe_reply)

            # Publish финальный ответ в bus → Web HUD покажет в реальном времени
            await bus.publish(JarvisEvent(
                type=EventType.ASSISTANT_REPLY,
                source="channel:telegram",
                channel="telegram",
                request_id=request_id,
                data={"text": safe_reply, "chat_id": chat_id},
            ))

            # Tier 2 persistence: пишем exchange в workspace/daily/today.md
            await memory.append_exchange_async(
                user_text=clean_text,
                assistant_text=safe_reply,
                channel="telegram",
            )
            # Tier 3 — векторная память
            import asyncio as _aio
            _aio.create_task(memory.add_to_vector(clean_text, role="user", channel="telegram"))
            _aio.create_task(memory.add_to_vector(safe_reply, role="assistant", channel="telegram"))

        finally:
            structlog.contextvars.clear_contextvars()

    return bot, dp, alert_scheduler


async def run_bot() -> None:
    """Точка входа: создать Claude, MemoryManager, бота, запустить polling."""
    setup_logging()
    logger.info(
        "telegram_bot_starting",
        bot_username=settings.telegram_bot_username,
        owner_chat_id=settings.telegram_owner_chat_id,
    )

    # Smart provider: Claude primary + YandexGPT fallback (если ключи Yandex есть)
    from core.providers import build_smart_provider
    claude = build_smart_provider(settings)
    memory = MemoryManager(workspace_dir=WORKSPACE_DIR)

    bot, dp, alert_scheduler = build_bot(claude, memory)

    # Heartbeat: утренний и вечерний брифинг в owner_chat
    from core.briefings import evening_brief, morning_brief
    from core.scheduler import Scheduler

    # Persistent state — для catch-up через restart процесса
    scheduler = Scheduler(persist_path=WORKSPACE_DIR / "scheduler_state.json")
    owner_chat = settings.telegram_owner_chat_id

    async def _send_morning() -> None:
        if not owner_chat:
            return
        text = await morning_brief(memory)
        await bot.send_message(owner_chat, text, parse_mode="Markdown")
        logger.info("morning_brief_sent", chat_id=owner_chat)

    async def _send_evening() -> None:
        if not owner_chat:
            return
        text = await evening_brief(memory)
        await bot.send_message(owner_chat, text, parse_mode="Markdown")
        logger.info("evening_brief_sent", chat_id=owner_chat)

    # Weekly summary — воскресенье 21:00 (Claude сжимает 7 daily-логов)
    from core.weekly_summary import generate_weekly_summary

    async def _send_weekly() -> None:
        from datetime import datetime
        if not owner_chat:
            return
        # weekday: 6 = воскресенье. Стреляет ежедневно в 21:00, но реальный send только в Вс.
        if datetime.now().weekday() != 6:
            return
        text = await generate_weekly_summary(claude, WORKSPACE_DIR)
        await bot.send_message(owner_chat, "🗓 *Итог недели*\n\n" + text, parse_mode="Markdown")
        logger.info("weekly_summary_sent", chat_id=owner_chat)

    # catch_up=True для брифингов: если бот был выключен в 08:00 / 22:00
    # и сегодня брифинг ещё не приходил — отправим сразу при старте.
    scheduler.add_daily("morning_brief", hour=8, minute=0, fn=_send_morning, catch_up=True)
    scheduler.add_daily("evening_brief", hour=22, minute=0, fn=_send_evening, catch_up=True)
    # weekly — только по воскресеньям, catch-up не нужен (не критично если пропустим)
    scheduler.add_daily("weekly_summary", hour=21, minute=0, fn=_send_weekly)
    scheduler.start()

    # Proactive alerts — финансы/погода/care-message. Создан в build_bot,
    # чтобы хендлеры могли вызывать notify_user_activity() через замыкание.
    alert_scheduler.start()

    try:
        await dp.start_polling(bot)
    finally:
        await alert_scheduler.stop()
        await scheduler.stop()
        await claude.close()
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(run_bot())
    except (KeyboardInterrupt, SystemExit):
        pass
