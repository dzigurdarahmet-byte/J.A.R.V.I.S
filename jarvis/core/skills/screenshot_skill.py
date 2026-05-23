"""Skill: скриншот экрана + описание через Claude vision.

«Опиши что на экране» / «что у меня на экране сейчас».

Шаги:
  1. PIL.ImageGrab.grab() → PIL.Image на весь экран (multi-monitor — все)
  2. Resize если шире 1600px (экономим токены и не теряем читабельность)
  3. Save в JPEG bytes
  4. claude.chat_with_image() → Claude vision описание
"""
from __future__ import annotations

import asyncio
import io
import re

from PIL import ImageGrab

from core.logging import get_logger
from core.providers import ClaudeProvider
from core.router import SkillResult
from core.skills.base import KeywordSkill

logger = get_logger(__name__)

MAX_WIDTH = 1600    # ресайз чтобы не отправлять 4K скрин на vision-эндпоинт
JPEG_QUALITY = 85

_PROMPT = (
    "Это скриншот экрана компьютера Босса (J.A.R.V.I.S.). "
    "Опиши кратко что на нём видно: какие окна открыты, чем Босс занят. "
    "Без воды, 2-3 предложения. Если есть ошибки / предупреждения на экране — "
    "сразу обрати внимание Босса на них."
)


def _grab_and_pack() -> bytes:
    """Снять весь экран и упаковать в JPEG bytes."""
    img = ImageGrab.grab(all_screens=True)
    if img.width > MAX_WIDTH:
        new_h = int(img.height * MAX_WIDTH / img.width)
        img = img.resize((MAX_WIDTH, new_h), resample=1)  # 1 = BILINEAR
    if img.mode != "RGB":
        img = img.convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    return buf.getvalue()


class ScreenshotDescribeSkill(KeywordSkill):
    name = "screenshot_describe"
    keywords = [
        r"\b(?:что|чё)\s+(?:у\s+меня\s+)?(?:сейчас\s+)?на\s+экране\b",
        r"\bопиши\s+(?:что\s+)?(?:сейчас\s+)?на\s+экране\b",
        r"\bпосмотри\s+на\s+(?:мой\s+)?экран\b",
        r"\bсделай\s+(?:мне\s+)?скриншот",
        r"\bвзгляни\s+на\s+экран\b",
    ]

    def __init__(self, claude: ClaudeProvider) -> None:
        super().__init__()
        self._claude = claude

    async def run(self, text: str, request_id: str) -> SkillResult:
        try:
            image_bytes = await asyncio.to_thread(_grab_and_pack)
        except Exception as e:
            logger.error("screenshot_grab_failed", error=str(e))
            return SkillResult(text=f"Не смог сделать скриншот: {type(e).__name__}", speakable=True)

        logger.info("screenshot_taken", size_kb=round(len(image_bytes) / 1024))

        # Извлечь пользовательский вопрос если есть — для уточнения промпта
        custom = self._extract_custom_q(text)
        prompt = _PROMPT if not custom else f"{_PROMPT}\n\nДополнительно Босс спрашивает: {custom}"

        try:
            description = await self._claude.chat_with_image(
                image_bytes=image_bytes,
                prompt=prompt,
                media_type="image/jpeg",
            )
        except Exception as e:
            logger.error("screenshot_vision_failed", error=str(e))
            return SkillResult(
                text=f"Скриншот снял, но Claude не смог его описать: {type(e).__name__}.",
                speakable=True,
            )

        return SkillResult(text=description.strip(), speakable=True)

    @staticmethod
    def _extract_custom_q(text: str) -> str | None:
        """Если после ключевой фразы есть дополнительный вопрос — берём его."""
        m = re.search(
            r"(?:на\s+экране|на\s+скрин(?:е|шоте))[:\s,]+(.+)$",
            text, flags=re.IGNORECASE | re.DOTALL,
        )
        if m:
            extra = m.group(1).strip().rstrip(".!?")
            if len(extra) > 3:
                return extra
        return None
