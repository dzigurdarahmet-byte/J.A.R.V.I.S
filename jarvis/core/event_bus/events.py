"""Стандартный набор типов событий Джарвиса.

Любой канал/компонент использует только эти строки для type — это позволит
будущим подписчикам матчиться по точным значениям, не угадывая.
"""

from __future__ import annotations

from enum import StrEnum


class EventType(StrEnum):
    # Вход от пользователя через канал
    USER_INPUT = "user_input"        # text/voice от Босса
    # Router решил куда отправить
    ROUTED = "routed"                # intent + skill_name или "llm"
    # Skill отработал
    SKILL_RESULT = "skill_result"
    # Финальный ответ ассистента (готов к показу/озвучке)
    ASSISTANT_REPLY = "assistant_reply"
    # Системные
    SYSTEM = "system"
    HEALTH = "health"
    # Управление каналом
    CHANNEL_STATE = "channel_state"  # paused/resumed/mode_switched
