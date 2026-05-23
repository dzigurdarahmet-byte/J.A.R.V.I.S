"""Router — 4-уровневая маршрутизация запросов Босса.

L1: Keyword/regex (быстро, дёшево) — будущие скиллы регистрируются сюда.
L2: Fuzzy match (≈90% похожесть) — не реализован на MVP.
L3: MCP/multi-agent — не реализован на MVP.
L4: Claude — fallback, текущий primary brain.

На текущем этапе у нас нет скиллов, поэтому Router пока всегда летит в L4.
Но архитектура готова: добавить скилл = register_skill(skill_instance).
"""

from .router import Router, Skill, SkillResult

__all__ = ["Router", "Skill", "SkillResult"]
