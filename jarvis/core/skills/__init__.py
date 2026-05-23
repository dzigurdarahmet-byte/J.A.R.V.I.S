"""Скиллы Джарвиса — конкретные функции которые он умеет делать.

Архитектура:
- BaseSkill — общий базовый класс с keyword-matching helper'ом
- builtin/* — пакет встроенных скиллов
- register_all_builtin(router) — массовая регистрация

Каждый скилл — отдельный класс, наследует BaseSkill. Имеет:
- name: уникальный идентификатор (для логов)
- keywords: список паттернов для match() — слова/фразы триггеры
- run(text, request_id) -> SkillResult — собственно работа
"""

from .base import BaseSkill, KeywordSkill
from .registry import register_all_builtin

__all__ = ["BaseSkill", "KeywordSkill", "register_all_builtin"]
