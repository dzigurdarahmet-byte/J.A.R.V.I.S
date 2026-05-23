"""JARVIS tone-of-voice polish.

Принимает «сырой» ответ skill'а и пропускает через Claude для перефразировки
в фирменном стиле Marvel JARVIS:
  — уважительно, лаконично, с долей сухого английского юмора;
  — обращение «Босс»;
  — без эмодзи, без воды;
  — длина примерно как оригинал;
  — все факты (числа, имена, ID) сохраняются дословно.

Применяется только в каналах HUD/Telegram. В голосовом канале и в Алисе
не используется — там latency критичнее, чем стиль.
"""
from __future__ import annotations

from core.logging import get_logger
from core.providers import ClaudeProvider, Message

logger = get_logger(__name__)

_SYSTEM = (
    "Ты — J.A.R.V.I.S., персональный ассистент Босса (стиль Marvel: "
    "уважительный, лаконичный, остроумный, чуть-чуть саркастичный — "
    "но никогда грубый). Обращайся «Босс». Никогда «вы», «сэр», «господин»."
)

_INSTRUCT = """Это сырой ответ одного из служебных модулей (skill). Перефразируй \
его в свой стиль ОДНИМ ответом, без вступлений типа «Конечно, вот:» и без \
маркеров «Перефразирую:».

Жёсткие правила:
1. Сохрани ВСЕ факты дословно: цифры, время, имена, идентификаторы (#1, #2 и т.д.),
   адреса, URL'ы, размеры файлов, проценты, валюты.
2. Если в оригинале — список (нумерованный или маркированный), оставь список
   в том же порядке, не меняй пункты местами.
3. Длина — близко к оригиналу. Не сокращай факты, не добавляй советы и комментарии.
4. Без эмодзи, без markdown, без бессодержательных подводок («Босс, отличный вопрос!»).
5. Если оригинал уже короткий и звучит нормально (одно слово или предложение) — \
   просто верни его без изменений или с минимальной правкой.
6. Если оригинал — это сообщение об ошибке, передай суть, но в твоём стиле.

Сырой ответ skill'а:
---
{raw}
---

Твоя перефразировка:"""


async def polish_for_jarvis(
    raw: str,
    claude: ClaudeProvider,
    *,
    timeout_sec: float = 6.0,
) -> str:
    """Пропустить raw через Claude в JARVIS-стиле.

    Возвращает перефразированный текст. При любой ошибке (timeout, API down)
    возвращает исходный raw — fail-open, чтобы не убивать ответ Боссу.
    """
    if not raw or not raw.strip():
        return raw
    # Совсем короткие ответы (1-2 слова) не имеет смысла полировать.
    if len(raw.strip()) < 12:
        return raw

    prompt = _INSTRUCT.format(raw=raw)
    try:
        import asyncio
        polished = await asyncio.wait_for(
            claude.chat(
                messages=[Message(role="user", content=prompt)],
                system=_SYSTEM,
            ),
            timeout=timeout_sec,
        )
    except asyncio.TimeoutError:
        logger.warning("jarvis_polish_timeout", chars=len(raw), timeout=timeout_sec)
        return raw
    except Exception as e:
        logger.warning("jarvis_polish_failed", error=str(e), chars=len(raw))
        return raw

    polished = (polished or "").strip()
    # Параноя: если ответ Claude подозрительно отличается по длине — лучше raw
    # (это бывает когда Claude вместо перефразировки начинает «помогать» с задачей)
    if not polished:
        return raw
    if len(polished) > len(raw) * 3 + 80:
        logger.warning("jarvis_polish_too_long",
                       raw_len=len(raw), polished_len=len(polished))
        return raw
    return polished
