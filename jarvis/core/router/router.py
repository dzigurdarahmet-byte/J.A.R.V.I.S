"""Router: распределяет вход Босса между skills и Claude.

Использование:
    router = Router(claude_provider=claude, memory=memory)
    router.register_skill(TimeSkill())
    reply = await router.dispatch(text="сколько время", history=[...], channel="voice")

Дизайн:
- Skill — лёгкий Protocol с методами match(text) → score:float и run(...) → SkillResult
- Скиллы сортируются по score, лучший выигрывает порог
- Если ни один не подошёл — fallback на Claude
- Router публикует EventType.ROUTED в bus с информацией о решении
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from core.event_bus import EventType, JarvisEvent, bus
from core.logging import get_logger
from core.memory import MemoryManager
from core.providers import ClaudeProvider, Message
from core.router.context import (
    ContextStore,
    extract_followup_args,
    is_short_followup,
)

logger = get_logger(__name__)

# Порог матчинга skill'a: 0.0 — никто не подошёл, 1.0 — точный матч.
SKILL_MATCH_THRESHOLD: float = 0.5


@dataclass(slots=True)
class SkillResult:
    """Результат работы скилла."""

    text: str                         # текст ответа для пользователя
    data: dict | None = None          # доп. данные (для логов, метрик)
    speakable: bool = True            # озвучивать через TTS или нет


@runtime_checkable
class Skill(Protocol):
    """Контракт скилла. Скиллы — stateless или с встроенным состоянием."""

    name: str

    def match(self, text: str) -> float:
        """Оценить уверенность что это запрос к этому скиллу. 0.0..1.0."""
        ...

    async def run(self, text: str, request_id: str) -> SkillResult:
        """Выполнить запрос. Должен быть быстрым (target <500мс)."""
        ...


class Router:
    """4-уровневая маршрутизация:
    - L1: keyword-match по скиллам (быстро, дёшево, точно).
    - L2: Claude с tool-use — сам решает какой скилл звать (контекст, follow-up).
    - L4: Claude чистым текстом — общие вопросы.
    """

    def __init__(
        self,
        claude_provider: ClaudeProvider,
        memory: MemoryManager,
        base_prompt: str,
        enable_tool_use: bool = True,
        enable_context_followup: bool = True,
        polish_channels: frozenset[str] | None = None,
        todo_store=None,         # для tool-use todo_add / todo_list / todo_done
        reminders_store=None,    # для tool-use reminder_add / reminder_list
    ) -> None:
        self._claude = claude_provider
        self._memory = memory
        self._base_prompt = base_prompt
        self._skills: list[Skill] = []
        self._enable_tool_use = enable_tool_use
        self._enable_context_followup = enable_context_followup
        self._ctx_store = ContextStore()
        # JARVIS-tone polish: на каких каналах перефразировывать через Claude
        self._polish_channels: frozenset[str] = polish_channels or frozenset()
        # Stateful skills для L2 tool-use
        self._todo_store = todo_store
        self._reminders_store = reminders_store

    def register_skill(self, skill: Skill) -> None:
        """Подключить скилл. Можно вызывать в любой момент."""
        self._skills.append(skill)
        logger.info("router_skill_registered", name=skill.name)

    async def dispatch(
        self,
        text: str,
        history: list[Message],
        channel: str,
        request_id: str = "",
    ) -> str:
        """Маршрут: skill матч → run; иначе Claude.

        Возвращает финальный текст ответа.
        """
        # ── Multi-step detect: если текст похож на цепочку запросов
        # (есть связка «и/затем/потом/после этого/+/.») И матчатся 2+
        # разных skill'а — обходим L1, идём сразу в L2 (Claude tool-use).
        # Это позволяет Claude составить chain из нескольких tools.
        skipping_l1_for_chain = (
            self._enable_tool_use
            and self._is_multi_step_request(text)
        )

        # L1: skills (пропускаем если detected chain)
        skill, score = (None, 0.0) if skipping_l1_for_chain else self._best_skill(text)
        if skill is not None and score >= SKILL_MATCH_THRESHOLD:
            await bus.publish(JarvisEvent(
                type=EventType.ROUTED,
                source="router",
                channel=channel,
                request_id=request_id,
                data={"intent": skill.name, "score": round(score, 2), "level": "L1"},
            ))
            logger.info("router_to_skill", skill=skill.name, score=round(score, 2))
            try:
                result = await skill.run(text, request_id=request_id)
                # JARVIS-tone polish: только для каналов из polish_channels
                # (HUD/Telegram). Голос/Alice — без полировки.
                final_text = result.text
                if channel in self._polish_channels:
                    from core.voice.jarvis_tone import polish_for_jarvis
                    final_text = await polish_for_jarvis(result.text, self._claude)
                await bus.publish(JarvisEvent(
                    type=EventType.SKILL_RESULT,
                    source=f"skill:{skill.name}",
                    channel=channel,
                    request_id=request_id,
                    data={"text": final_text, "speakable": result.speakable,
                          "raw": result.text if final_text != result.text else None},
                ))
                # запоминаем intent для возможных L1.5 follow-up'ов
                self._ctx_store.set(channel, intent=skill.name, args={"text": text})
                return final_text
            except Exception as e:
                logger.error("skill_failed_falling_back_to_llm",
                             skill=skill.name, error=str(e))
                # падаем в Claude

        # L1.5: короткий follow-up по последнему intent в канале
        if self._enable_context_followup and is_short_followup(text):
            ctx = self._ctx_store.get_fresh(channel)
            if ctx is not None:
                new_args = extract_followup_args(text, ctx.intent)
                if new_args:
                    routed_intent = new_args.pop("intent", ctx.intent)
                    reply = await self._run_followup(routed_intent, new_args, request_id)
                    if reply is not None:
                        await bus.publish(JarvisEvent(
                            type=EventType.ROUTED,
                            source="router",
                            channel=channel,
                            request_id=request_id,
                            data={
                                "intent": routed_intent,
                                "level": "L1.5",
                                "followup_of": ctx.intent,
                                "args": new_args,
                            },
                        ))
                        await bus.publish(JarvisEvent(
                            type=EventType.SKILL_RESULT,
                            source=f"skill:{routed_intent}",
                            channel=channel,
                            request_id=request_id,
                            data={"text": reply, "speakable": True, "via": "L1.5"},
                        ))
                        self._ctx_store.set(channel, intent=routed_intent, args=new_args)
                        return reply

        # L2: Claude с tool-use (если включено и есть инструменты)
        if self._enable_tool_use:
            try:
                from core.skills.tool_registry import TOOL_SCHEMAS, make_tool_runner
            except ImportError:
                TOOL_SCHEMAS, make_tool_runner = None, None  # type: ignore[assignment]

            if TOOL_SCHEMAS and make_tool_runner is not None:
                await bus.publish(JarvisEvent(
                    type=EventType.ROUTED,
                    source="router",
                    channel=channel,
                    request_id=request_id,
                    data={"intent": "llm+tools", "level": "L2"},
                ))
                try:
                    runner = make_tool_runner(
                        self._memory,
                        todo_store=self._todo_store,
                        reminders_store=self._reminders_store,
                    )
                    # F4: RAG-augmentation system prompt'а по последнему user-input
                    last_user = next(
                        (m.content for m in reversed(history) if m.role == "user"),
                        "",
                    )
                    system_prompt = await self._build_system_prompt(query=last_user)
                    reply_text, tool_calls = await self._claude.chat_with_tools(
                        messages=history,
                        tools=TOOL_SCHEMAS,
                        tool_runner=runner,
                        system=system_prompt,
                    )
                    if tool_calls:
                        await bus.publish(JarvisEvent(
                            type=EventType.SKILL_RESULT,
                            source="router:L2",
                            channel=channel,
                            request_id=request_id,
                            data={"tool_calls": tool_calls},
                        ))
                        # Запоминаем последний вызов как context для последующих L1.5
                        last_call = tool_calls[-1]
                        intent_map = {
                            "get_weather": "weather",
                            "get_weather_forecast": "weather_forecast",
                            "get_time_in_city": "timezone",
                            "get_currency_rates": "currency",
                            "get_crypto_rates": "crypto",
                        }
                        last_intent = intent_map.get(last_call.get("tool", ""))
                        if last_intent:
                            self._ctx_store.set(
                                channel,
                                intent=last_intent,
                                args=last_call.get("input") or {},
                            )
                    return reply_text
                except Exception as e:
                    logger.error("router_l2_failed_falling_back", error=str(e))
                    # Падение L2 не должно убить ответ — идём в L4

        # L4: Claude чистым текстом (fallback)
        await bus.publish(JarvisEvent(
            type=EventType.ROUTED,
            source="router",
            channel=channel,
            request_id=request_id,
            data={"intent": "llm", "level": "L4"},
        ))
        return await self._call_claude(history, request_id=request_id)

    async def _run_followup(
        self,
        intent: str,
        args: dict[str, str],
        request_id: str,
    ) -> str | None:
        """Выполнить follow-up через существующий tool-runner. None если intent неизвестен."""
        try:
            from core.skills.tool_registry import TOOL_RUNNERS
        except ImportError:
            return None

        # маппинг intent -> tool name
        tool_name_by_intent = {
            "weather": "get_weather",
            "weather_forecast": "get_weather_forecast",
            "timezone": "get_time_in_city",
            "currency": "get_currency_rates",
            "crypto": "get_crypto_rates",
        }
        tool_name = tool_name_by_intent.get(intent)
        if not tool_name:
            return None
        runner = TOOL_RUNNERS.get(tool_name)
        if not runner:
            return None
        try:
            return await runner(args, self._memory)
        except Exception as e:
            logger.error("l15_followup_failed", intent=intent, args=args, error=str(e))
            return None

    # Регулярки для детекта цепочечного запроса. Conjunction должно быть
    # между двумя глаголами/командами, не в одиночку.
    _CHAIN_CONJUNCTIONS = (
        r"\s+и\s+(?:после|потом|затем|тоже|также)?\s*(?:сразу\s+)?",
        r"\s+(?:затем|потом|после\s+этого|после\s+чего)\s+",
        r"\s+\+\s+",
        r"\s*;\s+",
    )

    def _is_multi_step_request(self, text: str) -> bool:
        """Эвристика: цепочечный запрос требующий >1 действия.

        Условия (все):
          1. В тексте есть связка между двумя частями (и/затем/потом/+/;).
          2. Минимум 2 skill'а матчатся с ненулевым score (по разным частям).
          3. Эти skill'а — РАЗНЫЕ (один и тот же 2 раза — не chain, просто
             синонимы в одной фразе).

        Если хотя бы одно не выполнено → обычный single-skill путь.
        """
        if not text or len(text) < 12:
            return False
        import re as _re
        has_conj = any(_re.search(p, text, _re.IGNORECASE) for p in self._CHAIN_CONJUNCTIONS)
        if not has_conj:
            return False
        # Сколько разных skill'ов матчатся?
        matched: set[str] = set()
        for skill in self._skills:
            try:
                if float(skill.match(text)) >= SKILL_MATCH_THRESHOLD:
                    matched.add(skill.name)
                    if len(matched) >= 2:
                        return True
            except Exception:
                continue
        return False

    def _best_skill(self, text: str) -> tuple[Skill | None, float]:
        if not self._skills:
            return None, 0.0
        best: Skill | None = None
        best_score = 0.0
        for skill in self._skills:
            try:
                score = float(skill.match(text))
            except Exception as e:
                logger.warning("skill_match_error", skill=skill.name, error=str(e))
                continue
            if score > best_score:
                best_score = score
                best = skill
        return best, best_score

    async def _call_claude(self, history: list[Message], request_id: str) -> str:
        # text запроса = последнее user-сообщение для RAG-augmentation
        last_user = next((m.content for m in reversed(history) if m.role == "user"), "")
        system_prompt = await self._build_system_prompt(query=last_user)
        return await self._claude.chat(messages=history, system=system_prompt)

    async def _build_system_prompt(self, query: str = "") -> str:
        """Собирает system prompt:
          1. base_prompt
          2. addendum от MemoryManager (MEMORY.md и т.п.)
          3. RAG-augmentation: top-N релевантных фрагментов из vector_db
             по запросу (F4 — conversational continuity).
        """
        parts = [self._base_prompt]
        addendum = self._memory.snapshot().render_system_addendum()
        if addendum:
            parts.append(addendum)
        # F4: RAG из vector_db (если query достаточно содержательный)
        if query and len(query.strip()) >= 5:
            try:
                hits = await self._memory.search_vector(query, limit=4)
            except Exception as e:
                logger.warning("rag_search_failed", error=str(e))
                hits = []
            # Фильтруем по similarity — иначе шум
            relevant = [h for h in hits if h.get("score", 0) >= 0.45]
            if relevant:
                lines = [
                    "Релевантные фрагменты из прошлых разговоров с Боссом "
                    "(используй для контекста только если уместно, не упоминай "
                    "что они «извлечены»):",
                ]
                for h in relevant[:3]:
                    role = h.get("role", "")
                    text = (h.get("text") or "").strip()
                    if not text:
                        continue
                    # Обрежем длинные фрагменты до 300 chars
                    if len(text) > 300:
                        text = text[:300] + "..."
                    speaker = "Босс" if role == "user" else "ты ранее"
                    lines.append(f"— {speaker}: {text}")
                if len(lines) > 1:
                    parts.append("\n".join(lines))
        return "\n\n".join(parts)
