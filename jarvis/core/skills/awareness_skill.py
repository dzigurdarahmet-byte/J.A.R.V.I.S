"""Skills для context awareness (F2).

Два intent'а в одном skill:
  REMEMBER — «запомни что я сейчас делаю» / «зафиксируй контекст»
            → snapshot screen → Claude vision describe → save в buffer
  QUERY    — «что я делал N минут назад» / «над чем я зависаю» / «чем я
            был занят» → query buffer + Claude формирует ответ
"""
from __future__ import annotations

import asyncio
import io
import re
from datetime import datetime, timedelta

from PIL import ImageGrab

from core.awareness import get_buffer
from core.logging import get_logger
from core.providers import ClaudeProvider, Message
from core.router import SkillResult
from core.skills.base import KeywordSkill

logger = get_logger(__name__)

# Снимок всего экрана (Босс выбрал full-screen scope)
MAX_WIDTH = 1600
JPEG_QUALITY = 80

_DESCRIBE_PROMPT = (
    "Ты — глаза Джарвиса. Кратко (1-2 предложения, без воды) опиши чем "
    "Босс сейчас занят на компьютере. Что за приложение, над чем работает, "
    "если видны конкретные файлы/проекты — назови. Без оценок и советов."
)


def _grab() -> bytes:
    img = ImageGrab.grab(all_screens=True)
    if img.width > MAX_WIDTH:
        new_h = int(img.height * MAX_WIDTH / img.width)
        img = img.resize((MAX_WIDTH, new_h), resample=1)
    if img.mode != "RGB":
        img = img.convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    return buf.getvalue()


# ─── Парсеры для query intents ──────────────────────────────────────
# «N минут/часов назад»
_AGO_PATTERN = re.compile(
    r"(?:(\d+)\s*)?(минут\w*|мин|час\w*|ч)\s+назад\b",
    re.IGNORECASE,
)
# «полчаса назад»
_HALF_AGO = re.compile(r"\bпол\s*-?\s*часа\s+назад\b", re.IGNORECASE)


def _parse_ago(text: str, now: datetime | None = None) -> datetime | None:
    now = now or datetime.now()
    if _HALF_AGO.search(text):
        return now - timedelta(minutes=30)
    m = _AGO_PATTERN.search(text)
    if not m:
        return None
    qty_str, unit = m.group(1), m.group(2).lower()
    qty = int(qty_str) if qty_str else 1
    if unit.startswith(("мин",)):
        return now - timedelta(minutes=qty)
    if unit.startswith(("час", "ч")):
        return now - timedelta(hours=qty)
    return None


# ─── Skill ──────────────────────────────────────────────────────────
_REMEMBER_PATTERNS = [
    re.compile(r"\bзапомни\s+(?:что\s+)?(?:я\s+)?(?:сейчас\s+)?(?:делаю|занимаюсь|работаю)\b", re.IGNORECASE),
    re.compile(r"\bзафиксируй\s+(?:что\s+я\s+)?(?:сейчас\s+)?(?:на\s+экране|делаю|занят|контекст)\b", re.IGNORECASE),
    re.compile(r"\bотметь\s+(?:контекст|чем\s+я\s+занят)\b", re.IGNORECASE),
]
_AGO_QUERY_PATTERNS = [
    # "30 минут назад" / "час назад" / "полчаса назад" — число опционально
    re.compile(r"\bчто\s+я\s+делал[аи]?\s+(?:\d+\s*)?(?:минут\w*|мин|час\w*|ч)\s+назад\b", re.IGNORECASE),
    re.compile(r"\bчто\s+я\s+делал[аи]?\s+пол\s*-?\s*часа\s+назад\b", re.IGNORECASE),
    re.compile(r"\bчем\s+я\s+был\s+занят\s+(?:\d+\s*)?(?:минут\w*|мин|час\w*|ч|пол\s*-?\s*часа)\s+назад\b", re.IGNORECASE),
]
_RECENT_QUERY_PATTERNS = [
    re.compile(r"\bнад\s+чем\s+я\s+(?:сейчас\s+)?(?:зависаю|залип|сижу)\b", re.IGNORECASE),
    re.compile(r"\bчем\s+я\s+(?:был\s+)?занят\s+(?:последнее\s+время|последние)\b", re.IGNORECASE),
    re.compile(r"\bчто\s+я\s+делал[аи]?\s+(?:последнее\s+время|последний\s+час|сегодня)\b", re.IGNORECASE),
]


class AwarenessSkill(KeywordSkill):
    name = "awareness"
    keywords = [
        r"\bзапомни\s+(?:что\s+)?(?:я\s+)?(?:сейчас\s+)?(?:делаю|занимаюсь|работаю)\b",
        r"\bзафиксируй\s+(?:что\s+я\s+)?(?:сейчас\s+)?(?:на\s+экране|делаю|занят|контекст)\b",
        r"\bотметь\s+(?:контекст|чем\s+я\s+занят)\b",
        r"\bчто\s+я\s+делал[аи]?\s+(?:\d+\s*)?(?:минут\w*|мин|час\w*|ч|пол\s*-?\s*часа)\s+назад\b",
        r"\bчем\s+я\s+был\s+занят\s+(?:\d+\s*)?(?:минут\w*|мин|час\w*|ч|пол\s*-?\s*часа)\s+назад\b",
        r"\bнад\s+чем\s+я\s+(?:сейчас\s+)?(?:зависаю|залип|сижу)\b",
        r"\bчем\s+я\s+(?:был\s+)?занят\s+последнее",
        r"\bчто\s+я\s+делал[аи]?\s+(?:последнее\s+время|последний\s+час|сегодня)\b",
    ]

    def __init__(self, claude: ClaudeProvider, workspace_dir) -> None:
        super().__init__()
        self._claude = claude
        self._buffer = get_buffer(workspace_dir)

    async def run(self, text: str, request_id: str) -> SkillResult:
        # 1. REMEMBER — snapshot + describe + save
        if any(p.search(text) for p in _REMEMBER_PATTERNS):
            return await self._remember()

        # 2. AGO query — конкретный момент в прошлом
        if any(p.search(text) for p in _AGO_QUERY_PATTERNS):
            return await self._query_ago(text)

        # 3. RECENT query — над чем зависаю / что делал последнее время
        if any(p.search(text) for p in _RECENT_QUERY_PATTERNS):
            return await self._query_recent()

        return SkillResult(
            text="Скажи 'запомни что делаю' или 'что я делал N минут назад'.",
            speakable=True,
        )

    # ─── intents ────────────────────────────────────────────────────
    async def _remember(self) -> SkillResult:
        try:
            image_bytes = await asyncio.to_thread(_grab)
        except Exception as e:
            return SkillResult(text=f"Не смог снять экран: {type(e).__name__}", speakable=True)

        try:
            description = await self._claude.chat_with_image(
                image_bytes=image_bytes,
                prompt=_DESCRIBE_PROMPT,
                media_type="image/jpeg",
            )
        except Exception as e:
            logger.error("awareness_describe_failed", error=str(e))
            return SkillResult(text=f"Снимок сделал, но описание не получилось: {type(e).__name__}", speakable=True)

        description = (description or "").strip()
        if not description:
            return SkillResult(text="Снимок есть, но описание пустое.", speakable=True)

        entry = self._buffer.add(description, trigger="manual")
        logger.info("awareness_remember", chars=len(description))
        return SkillResult(
            text=f"Зафиксировал: {description}",
            speakable=True,
        )

    async def _query_ago(self, text: str) -> SkillResult:
        target = _parse_ago(text)
        if target is None:
            return SkillResult(text="Не понял когда (через сколько минут/часов).", speakable=True)
        entry = self._buffer.near_time(target, tolerance=timedelta(minutes=20))
        if entry is None:
            human = target.strftime("%H:%M")
            return SkillResult(
                text=f"На момент около {human} нет зафиксированных контекстов. "
                     "Скажи 'запомни что делаю' чтобы я начал собирать историю.",
                speakable=True,
            )
        when = datetime.fromisoformat(entry.at_iso).strftime("%H:%M")
        return SkillResult(
            text=f"В {when} ты был занят: {entry.description}",
            speakable=True,
        )

    async def _query_recent(self) -> SkillResult:
        items = self._buffer.recent(limit=6)
        if not items:
            return SkillResult(
                text="Истории нет, Босс. Скажи 'запомни что делаю' — начну её собирать.",
                speakable=True,
            )
        # Если только одна запись — выдаём её
        if len(items) == 1:
            e = items[0]
            when = datetime.fromisoformat(e.at_iso).strftime("%H:%M")
            return SkillResult(text=f"В {when}: {e.description}", speakable=True)
        # Несколько — даём Claude собрать аггрегацию
        lines = []
        for e in items:
            try:
                t = datetime.fromisoformat(e.at_iso).strftime("%H:%M")
            except ValueError:
                t = "?"
            lines.append(f"[{t}] {e.description}")
        history = "\n".join(lines)
        prompt = (
            "Босс спрашивает чем он был занят последнее время. Ниже — "
            "хронология контекстов с временными метками. Сделай короткое "
            "(2-3 предложения) summary — что было основное, есть ли паттерн "
            "(долго на одном, переключался часто, и т.п.). Без воды и без оценок.\n\n"
            f"{history}"
        )
        try:
            summary = await self._claude.chat(
                messages=[Message(role="user", content=prompt)],
                system="Ты — Джарвис. Обращайся «Босс». Лаконично.",
            )
            return SkillResult(text=(summary or "").strip(), speakable=True)
        except Exception:
            # Fallback — просто список
            return SkillResult(text="Последнее время:\n" + history, speakable=True)
