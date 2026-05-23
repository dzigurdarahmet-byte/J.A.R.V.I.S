"""Weekly summary — раз в неделю Claude сжимает daily-логи в саммари недели.

Запускается из scheduler в воскресенье 21:00:
  - читает workspace/daily/YYYY-MM-DD.md за последние 7 дней
  - отдаёт Claude'у с промптом «сделай человечный итог недели»
  - сохраняет в workspace/weekly/YYYY-WNN.md
  - отдаёт текст обратно вызывающему (Telegram bot его шлёт Боссу)
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

from core.logging import get_logger
from core.providers import ClaudeProvider, Message

logger = get_logger(__name__)


def _last_n_days_paths(workspace_dir: Path, days: int = 7) -> list[Path]:
    """Список путей к daily-логам за последние N дней (включая сегодня)."""
    daily_dir = workspace_dir / "daily"
    out: list[Path] = []
    today = date.today()
    for offset in range(days):
        d = today - timedelta(days=offset)
        p = daily_dir / f"{d.isoformat()}.md"
        if p.exists() and p.stat().st_size > 0:
            out.append(p)
    return out


def _collect_week_text(workspace_dir: Path, days: int = 7) -> str:
    """Слепить контент daily-логов за неделю в один текст для Claude.

    Лимит: ~15000 символов (запас по токенам).
    Если очень много — берём только последние блоки.
    """
    parts: list[str] = []
    total = 0
    LIMIT = 15_000
    for p in _last_n_days_paths(workspace_dir, days=days):
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:
            continue
        parts.append(f"=== {p.stem} ===\n{text.strip()}\n")
        total += len(text)
        if total >= LIMIT:
            break
    return "\n".join(parts)


WEEKLY_SYSTEM_PROMPT = """Ты — J.A.R.V.I.S., персональный ассистент Босса (Сергей Стаховский).
Делаешь краткий, человечный итог недели по дневным логам.

Структура итога:
1. 🎯 Главное за неделю — 2-3 пункта.
2. 📊 Активность — сколько запросов, во сколько каналов.
3. 📝 Что Босс просил запомнить — выжимка из заметок.
4. 🌤 Погодная аномалия / финансовые движения — если были.
5. 🌱 Над чем поработать на следующей неделе — 1-2 идеи.

Стиль: Marvel-JARVIS — уважительный, лаконичный, остроумный. Markdown допустим.
Обращайся «Босс». Не повторяй банальности. Длина: 200-400 слов.
"""


async def generate_weekly_summary(
    claude: ClaudeProvider,
    workspace_dir: Path,
    days: int = 7,
) -> str:
    """Сгенерировать summary недели + сохранить в workspace/weekly/."""
    week_text = _collect_week_text(workspace_dir, days=days)
    if not week_text:
        return "За неделю в логах пусто, Босс."

    user_prompt = (
        "Вот мои дневные логи за последние 7 дней. "
        "Сделай человечный итог по структуре выше.\n\n"
        f"{week_text}"
    )
    messages = [Message(role="user", content=user_prompt)]

    try:
        reply = await claude.chat(
            messages=messages,
            system=WEEKLY_SYSTEM_PROMPT,
            max_tokens=900,
            temperature=0.7,
        )
    except Exception as e:
        logger.error("weekly_summary_claude_failed", error=str(e))
        return f"Босс, не получилось сделать саммари — {e}"

    # Сохраняем в workspace/weekly/YYYY-WNN.md
    weekly_dir = workspace_dir / "weekly"
    weekly_dir.mkdir(parents=True, exist_ok=True)
    iso_year, iso_week, _ = date.today().isocalendar()
    fname = weekly_dir / f"{iso_year}-W{iso_week:02d}.md"
    header = f"# Итог недели {iso_year}-W{iso_week:02d}\n_Сгенерировано {datetime.now():%Y-%m-%d %H:%M}_\n\n"
    fname.write_text(header + reply.strip() + "\n", encoding="utf-8")
    logger.info("weekly_summary_saved", path=str(fname), length=len(reply))
    return reply
