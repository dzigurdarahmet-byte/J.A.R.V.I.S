"""Web HUD сервер: FastAPI + WebSocket.

Архитектура:
    Browser ─WS─► /ws (FastAPI)
                  ├─ публикует входящие сообщения в bus как USER_INPUT
                  └─ подписан на bus → стримит все события в браузер

    Browser ─GET─► /        (single-page HUD)
            ─GET─► /static/* (HTML/CSS/JS статика)
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections import deque
from pathlib import Path
from typing import AsyncIterator, Final

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from core.config import settings
from core.event_bus import EventType, JarvisEvent, bus
from core.logging import get_logger, setup_logging
from core.memory import MemoryManager
from core.metrics import metrics
from core.network import NetworkWatchdog, get_network_state, get_provider_status
from core.providers import ClaudeProvider, Message
from core.router import Router
from core.security import PromptGuard
from core.skills import register_all_builtin

logger = get_logger(__name__)

CHANNEL: Final = "web_hud"
HISTORY_LIMIT: Final = 12
STATIC_DIR: Final = Path(__file__).parent / "static"
WORKSPACE_DIR: Final = Path(__file__).resolve().parents[2] / "workspace"
AVATAR_AUDIO_DIR: Final = WORKSPACE_DIR / "avatar_audio"
AVATAR_AUDIO_DIR.mkdir(parents=True, exist_ok=True)
AVATAR_AUDIO_TTL_SEC: Final = 300  # 5 минут жизни WAV-а после генерации

# Лог-источники для вкладки Logs.
# Ключ — id в URL, значение — путь к файлу. ВНЕ workspace ничего не отдаём.
LOG_SOURCES: Final[dict[str, Path]] = {
    "voice": WORKSPACE_DIR / "voice_run.log",
    "voice-err": WORKSPACE_DIR / "voice_run.err",
    "backup": WORKSPACE_DIR / "backup.log",
}
LOG_TAIL_DEFAULT: Final = 200
LOG_TAIL_MAX: Final = 5000
LOG_STREAM_POLL_SEC: Final = 0.5  # как часто опрашиваем файл на новые строки
LOG_STREAM_MAX_CHUNK: Final = 65536  # за один тик — не больше 64 KB новых данных


def _decode_log_bytes(data: bytes) -> str:
    """Decode log bytes с авто-fallback.

    voice_run.log часто пишется через PowerShell redirect (`> log.txt`),
    а PowerShell на ru-Windows кодирует stdout в CP1251 (ANSI), даже если сам
    процесс пишет UTF-8. В итоге UTF-8 байты ломаются.

    Стратегия:
      1. Попробуй UTF-8.
      2. Если result содержит >2% replacement chars (U+FFFD) — fallback CP1251.
      3. Если CP1251 даёт меньше replacement chars — возвращаем его, иначе UTF-8.
    """
    utf8 = data.decode("utf-8", errors="replace")
    utf8_bad = utf8.count("�")
    # Порог 2% — нормальный лог содержит 0 replacement; mojibake даёт много.
    if utf8_bad > 0 and utf8_bad / max(len(utf8), 1) > 0.02:
        try:
            cp1251 = data.decode("cp1251", errors="replace")
            cp1251_bad = cp1251.count("�")
            if cp1251_bad < utf8_bad:
                return cp1251
        except Exception:
            pass
    return utf8

BASE_PROMPT: Final = (
    "Ты — J.A.R.V.I.S., персональный ассистент Босса. "
    "Стиль: Marvel JARVIS — уважительный, лаконичный, остроумный. "
    "Обращайся 'Босс'. Никогда 'вы', 'сэр', 'господин'. "
    "Это веб-канал, можно использовать markdown в умеренном количестве. "
    "Отвечай по делу, без воды."
)


# ── Pydantic-модели на module-level ─────────────────────────────────
# ВАЖНО: модели должны быть на module-level, не внутри build_app(),
# иначе из-за `from __future__ import annotations` FastAPI не может
# разрешить type hint и трактует параметр как query → 422.
class AvatarSpeakRequest(BaseModel):
    text: str
    emotion: str = "neutral"


# ── Alice (Яндекс Диалоги) API request/response ─────────────────────
# Минимальный набор полей; передаём всё остальное как dict.
# Документация: https://yandex.ru/dev/dialogs/alice/doc/ru/protocol
class AliceSessionIn(BaseModel):
    new: bool = False
    message_id: int = 0
    session_id: str = ""
    skill_id: str = ""
    user_id: str = ""
    # user/application — оставляем как dict, не нужны для роутинга
    model_config = {"extra": "allow"}


class AliceRequestIn(BaseModel):
    command: str = ""
    original_utterance: str = ""
    type: str = "SimpleUtterance"
    model_config = {"extra": "allow"}


class AliceWebhookRequest(BaseModel):
    meta: dict = {}
    session: AliceSessionIn = AliceSessionIn()
    request: AliceRequestIn = AliceRequestIn()
    state: dict = {}
    version: str = "1.0"
    model_config = {"extra": "allow"}


class WebHudState:
    """Глобальное состояние HUD: подключённые WS клиенты + история чата."""

    def __init__(self) -> None:
        self.clients: set[WebSocket] = set()
        self.history: deque[Message] = deque(maxlen=HISTORY_LIMIT)

    async def broadcast(self, payload: dict) -> None:
        """Послать JSON всем подключённым клиентам."""
        dead: list[WebSocket] = []
        for ws in self.clients:
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.clients.discard(ws)


def build_app(claude: ClaudeProvider, memory: MemoryManager) -> FastAPI:
    """Собрать FastAPI приложение."""
    app = FastAPI(title="J.A.R.V.I.S. Web HUD", version="0.1.0")
    state = WebHudState()
    polish_channels = frozenset(
        c.strip() for c in (settings.jarvis_polish_channels or "").split(",") if c.strip()
    )
    # Stateful skills для L2 tool-use в Router
    from core.skills.todo_skill import TodoStore
    from core.skills.reminders_skill import RemindersStore
    todo_store_for_tools = TodoStore(WORKSPACE_DIR / "todo.json")
    reminders_store_for_tools = RemindersStore(WORKSPACE_DIR / "reminders.json")
    router = Router(
        claude_provider=claude,
        memory=memory,
        base_prompt=BASE_PROMPT,
        polish_channels=polish_channels,
        todo_store=todo_store_for_tools,
        reminders_store=reminders_store_for_tools,
    )
    # claude — на самом деле SmartProvider (см. run_web_hud → build_smart_provider).
    # Передаём как smart_provider, чтобы LLMSwitcherSkill мог писать выбор Босса.
    register_all_builtin(router, memory, claude=claude, smart_provider=claude)
    from core.skills.weekly_skill import WeeklySkill
    router.register_skill(WeeklySkill(claude))
    from core.skills.screenshot_skill import ScreenshotDescribeSkill
    router.register_skill(ScreenshotDescribeSkill(claude))
    from core.skills.awareness_skill import AwarenessSkill
    router.register_skill(AwarenessSkill(claude, WORKSPACE_DIR))

    # Warm-up истории из daily-лога
    initial = memory.load_recent_context(limit_messages=HISTORY_LIMIT)
    state.history.extend(initial)
    if initial:
        logger.info("web_hud_history_warmed", messages=len(initial))

    # ── BUS observer: рассылаем ВСЕ события подключённым браузерам ──
    @bus.on(bus.WILDCARD)
    async def _broadcast_to_hud(event: JarvisEvent) -> None:
        await state.broadcast({"event": event.to_dict()})

    # ── C15: подписываем metrics collector на bus (idempotent — повторный
    #    attach_bus просто продублирует подписку, поэтому делаем один раз
    #    через флаг на самом collector'е) ─────────────────────────────
    if not getattr(metrics, "_bus_attached", False):
        metrics.attach_bus(bus)
        metrics._bus_attached = True  # type: ignore[attr-defined]

    # ── HTTP routes ─────────────────────────────────────────────────
    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        index_html = STATIC_DIR / "index.html"
        if index_html.exists():
            return index_html.read_text(encoding="utf-8")
        return "<h1>Web HUD не собран. Запусти build статики.</h1>"

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/api/status")
    async def status() -> dict:
        # provider_status — какие LLM сейчас доступны (для HUD-индикатора)
        try:
            llm_status = await claude.provider_status() if hasattr(claude, "provider_status") else {}
        except Exception:
            llm_status = {}
        return {
            "ok": True,
            "channel": CHANNEL,
            "history_messages": len(state.history),
            "ws_clients": len(state.clients),
            "owner": settings.jarvis_owner_name,
            "network": {
                "state": get_network_state().value,
                "probes": get_provider_status(),
            },
            "providers": llm_status,
        }

    # ── Logs API (C14) ──────────────────────────────────────────────
    @app.get("/api/logs/sources")
    async def log_sources() -> list[dict]:
        """Список доступных лог-источников + размер файла."""
        result = []
        for src_id, path in LOG_SOURCES.items():
            exists = path.exists()
            size = path.stat().st_size if exists else 0
            result.append({
                "id": src_id,
                "name": path.name,
                "exists": exists,
                "size_bytes": size,
            })
        return result

    @app.get("/api/logs/tail")
    async def log_tail(source: str, lines: int = LOG_TAIL_DEFAULT) -> dict:
        """Вернуть последние N строк лога. lines clamp до LOG_TAIL_MAX."""
        path = LOG_SOURCES.get(source)
        if path is None:
            raise HTTPException(status_code=404, detail=f"unknown source: {source}")
        if not path.exists():
            return {"lines": [], "size_bytes": 0, "source": source}
        lines = max(1, min(lines, LOG_TAIL_MAX))
        try:
            raw = path.read_bytes()
            text = _decode_log_bytes(raw)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"read failed: {e}")
        all_lines = text.splitlines()
        tail = all_lines[-lines:] if len(all_lines) > lines else all_lines
        return {
            "lines": tail,
            "size_bytes": path.stat().st_size,
            "source": source,
        }

    @app.get("/api/logs/stream")
    async def log_stream(source: str) -> StreamingResponse:
        """SSE-стрим новых строк лога. Поллит файл раз в LOG_STREAM_POLL_SEC сек."""
        path = LOG_SOURCES.get(source)
        if path is None:
            raise HTTPException(status_code=404, detail=f"unknown source: {source}")

        async def event_gen() -> AsyncIterator[str]:
            # Стартуем с текущего конца файла — новые строки появляются по мере
            # того как процесс пишет в файл.
            last_size = path.stat().st_size if path.exists() else 0
            buf = b""  # для неполных строк между чанками
            try:
                while True:
                    try:
                        current_size = path.stat().st_size if path.exists() else 0
                    except Exception:
                        current_size = 0

                    if current_size < last_size:
                        # Лог пересоздали / ротировали — начинаем с нуля
                        last_size = 0
                        buf = b""
                        yield "event: rotate\ndata: {}\n\n"

                    if current_size > last_size:
                        try:
                            with open(path, "rb") as f:
                                f.seek(last_size)
                                chunk = f.read(min(current_size - last_size, LOG_STREAM_MAX_CHUNK))
                        except Exception as e:
                            yield f"event: error\ndata: {json.dumps({'error': str(e)})}\n\n"
                            await asyncio.sleep(2.0)
                            continue
                        last_size += len(chunk)
                        buf += chunk
                        # Разбиваем по \n, последняя неполная строка остаётся в buf
                        *complete, buf = buf.split(b"\n")
                        for line_bytes in complete:
                            line = _decode_log_bytes(line_bytes).rstrip("\r")
                            if line:
                                yield f"data: {json.dumps({'line': line}, ensure_ascii=False)}\n\n"

                    # Heartbeat чтобы прокси и браузер не разорвали idle-соединение
                    yield ": ping\n\n"
                    await asyncio.sleep(LOG_STREAM_POLL_SEC)
            except asyncio.CancelledError:
                logger.info("log_stream_cancelled", source=source)
                raise

        return StreamingResponse(
            event_gen(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",  # отключает буферизацию nginx если в проде
            },
        )

    # ── Metrics API (C15) ───────────────────────────────────────────
    # Окна выбираются параметром ?window=1h/24h/7d/30d. Парсер ниже.
    _WINDOW_MAP = {
        "1h": 3600,
        "6h": 21600,
        "24h": 86400,
        "7d": 604800,
        "30d": 2592000,
    }
    _DEFAULT_WINDOW_SEC = 86400  # 24h
    _BUCKET_BY_WINDOW = {
        3600: 60,      # 1h -> 1 min buckets
        21600: 300,    # 6h -> 5 min
        86400: 600,    # 24h -> 10 min
        604800: 3600,  # 7d -> 1 hour
        2592000: 21600,  # 30d -> 6 hour
    }

    def _parse_window(window: str) -> int:
        return _WINDOW_MAP.get(window, _DEFAULT_WINDOW_SEC)

    @app.get("/api/metrics/summary")
    async def metrics_summary(window: str = "24h") -> dict:
        sec = _parse_window(window)
        return metrics.summary(window_sec=sec)

    @app.get("/api/metrics/timeseries")
    async def metrics_timeseries(event_type: str, window: str = "24h") -> dict:
        sec = _parse_window(window)
        bucket = _BUCKET_BY_WINDOW.get(sec, 600)
        return {
            "event_type": event_type,
            "window_sec": sec,
            "bucket_sec": bucket,
            "points": metrics.timeseries(event_type, sec, bucket),
        }

    @app.get("/api/metrics/events")
    async def metrics_events(limit: int = 100) -> dict:
        return {"events": metrics.recent_events(limit=limit)}

    # ── Goals API ───────────────────────────────────────────────────
    # Читаем GoalsStore (тот же что использует GoalsSkill) и отдаём
    # списком с подсчитанным процентом и темпом. Хранилище — JSON, лочки
    # на уровне GoalsStore.add_progress(). Этот endpoint read-only, поэтому
    # лок не берём — просто load().
    @app.get("/api/goals")
    async def goals_api(include_completed: bool = False) -> dict:
        from core.skills.goals_skill import GoalsStore, _format_pace
        store = GoalsStore(WORKSPACE_DIR / "goals.json")
        data = store.load()
        items = data.get("items", [])
        if not include_completed:
            items = [g for g in items if not g.get("completed_at")]

        # Денормализуем — добавляем percent, pace_text, days_left.
        from datetime import date as _date
        today = _date.today()
        out: list[dict] = []
        for g in items:
            target = max(float(g.get("target", 0) or 0), 1.0)
            current = float(g.get("current", 0) or 0)
            pct = min(1.0, current / target)
            days_left: int | None = None
            deadline_iso = g.get("deadline_iso")
            if deadline_iso:
                try:
                    deadline = _date.fromisoformat(deadline_iso)
                    days_left = (deadline - today).days
                except Exception:
                    days_left = None
            out.append({
                "id": g["id"],
                "name": g.get("name", ""),
                "target": g.get("target", 0),
                "current": g.get("current", 0),
                "unit": g.get("unit", ""),
                "percent": round(pct * 100, 1),
                "deadline_iso": deadline_iso,
                "days_left": days_left,
                "pace_text": _format_pace(g).strip(" ()"),
                "completed_at": g.get("completed_at"),
                "created_iso": g.get("created_iso"),
                "history_len": len(g.get("history", [])),
            })
        return {"goals": out, "total": len(out)}

    # ── Avatar API (D17) ────────────────────────────────────────────
    # Простой контракт: text -> WAV (через Yandex Alena) + viseme timeline.
    # Аватар во frontend (TalkingHead.js) играет WAV и применяет viseme к
    # mouthOpen/jawOpen blendshapes по timeline. Идеального lip-sync нет,
    # но «рот открывается на гласных» работает.
    # AvatarSpeakRequest — на module-level (см. коммент у определения).
    import time as _time
    import uuid as _uuid
    import wave as _wave

    _avatar_tts = None  # lazy init, чтобы build_app не дёргал Yandex при импорте

    def _get_avatar_tts():
        nonlocal _avatar_tts
        if _avatar_tts is not None:
            return _avatar_tts
        yk = settings.yandex_api_key
        yf = settings.yandex_folder_id
        if not (yk and yk.get_secret_value() and yf):
            return None
        try:
            from core.voice.tts_yandex import YandexSpeechKitTTS
            _avatar_tts = YandexSpeechKitTTS(
                api_key=yk.get_secret_value(),
                folder_id=yf,
                voice="alena",
            )
            return _avatar_tts
        except Exception as e:
            logger.error("avatar_tts_init_failed", error=str(e))
            return None

    def _cleanup_old_audio() -> None:
        """Удалить WAV старше TTL — чтобы директория не разрасталась."""
        now = _time.time()
        for f in AVATAR_AUDIO_DIR.glob("*.wav"):
            try:
                if now - f.stat().st_mtime > AVATAR_AUDIO_TTL_SEC:
                    f.unlink(missing_ok=True)
            except Exception:
                pass

    @app.post("/api/avatar/speak")
    async def avatar_speak(req: AvatarSpeakRequest) -> dict:
        text = (req.text or "").strip()
        if not text:
            raise HTTPException(status_code=400, detail="empty text")

        tts = _get_avatar_tts()
        if tts is None:
            raise HTTPException(
                status_code=503,
                detail="avatar TTS не сконфигурирован — нет Yandex ключей",
            )

        _cleanup_old_audio()

        # Определяем эмоцию: "auto" → детект по тексту, иначе доверяем клиенту
        from core.voice.emotion import (
            detect_emotion, emotion_to_yandex_role, ALL_EMOTIONS,
        )
        req_emotion = (req.emotion or "neutral").lower()
        if req_emotion == "auto":
            final_emotion = detect_emotion(text)
        elif req_emotion in ALL_EMOTIONS:
            final_emotion = req_emotion  # type: ignore[assignment]
        else:
            final_emotion = "neutral"
        yandex_role = emotion_to_yandex_role(final_emotion, voice="alena")

        # Генерим аудио — с per-call role override (без долгой переинициализации)
        try:
            await tts.preload()
            audio = await tts.synthesize(text, role=yandex_role)
        except Exception as e:
            logger.error("avatar_tts_synth_failed", error=str(e))
            raise HTTPException(status_code=502, detail=f"TTS failed: {e}")

        sample_rate = tts.sample_rate
        duration_sec = float(audio.shape[0]) / float(sample_rate)

        # Сохраняем как WAV (16-bit PCM)
        import numpy as np  # уже наверняка импортирован выше, но локально для ясности
        request_id = _uuid.uuid4().hex[:12]
        wav_path = AVATAR_AUDIO_DIR / f"{request_id}.wav"
        try:
            pcm16 = np.clip(audio * 32767.0, -32768, 32767).astype(np.int16)
            with _wave.open(str(wav_path), "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(sample_rate)
                wf.writeframes(pcm16.tobytes())
        except Exception as e:
            logger.error("avatar_wav_save_failed", error=str(e))
            raise HTTPException(status_code=500, detail=f"wav save failed: {e}")

        # Viseme timeline по тексту
        from core.voice.viseme import text_to_visemes
        visemes = text_to_visemes(text, duration_sec)

        logger.info(
            "avatar_speak_ok",
            request_id=request_id,
            chars=len(text),
            duration_sec=round(duration_sec, 2),
            visemes=len(visemes),
            emotion=final_emotion,
            yandex_role=yandex_role,
        )

        return {
            "request_id": request_id,
            "audio_url": f"/api/avatar/audio/{request_id}",
            "duration_ms": int(duration_sec * 1000),
            "sample_rate": sample_rate,
            "visemes": visemes,
            "emotion": final_emotion,
            "yandex_role": yandex_role,
        }

    @app.get("/api/avatar/audio/{request_id}")
    async def avatar_audio(request_id: str):
        # Защита от path traversal
        if not request_id.isalnum() or len(request_id) != 12:
            raise HTTPException(status_code=400, detail="invalid request_id")
        path = AVATAR_AUDIO_DIR / f"{request_id}.wav"
        if not path.exists():
            raise HTTPException(status_code=404, detail="audio not found or expired")
        return FileResponse(str(path), media_type="audio/wav")

    @app.get("/avatar", response_class=HTMLResponse)
    async def avatar_page():
        page = STATIC_DIR / "avatar.html"
        if page.exists():
            return page.read_text(encoding="utf-8")
        return "<h1>avatar.html не найден</h1>"

    @app.get("/api/avatar/model")
    async def avatar_model_proxy(url: str):
        """Proxy для GLB-моделей. Обходит CORS и mixed content (http<->https).

        Whitelist доменов — только надёжные источники.
        """
        ALLOWED_HOSTS = (
            "models.readyplayer.me",
            "api.readyplayer.me",
            "raw.githubusercontent.com",
            "cdn.jsdelivr.net",
        )
        from urllib.parse import urlparse
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            raise HTTPException(status_code=400, detail="bad scheme")
        if parsed.hostname not in ALLOWED_HOSTS:
            raise HTTPException(
                status_code=403,
                detail=f"host not allowed; allowed: {', '.join(ALLOWED_HOSTS)}",
            )
        import httpx
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                r = await client.get(url, follow_redirects=True)
                r.raise_for_status()
        except httpx.HTTPError as e:
            raise HTTPException(status_code=502, detail=f"upstream failed: {e}")
        # GLB — это binary файл с magic bytes "glTF"
        content_type = r.headers.get("content-type", "model/gltf-binary")
        return StreamingResponse(
            iter([r.content]),
            media_type=content_type,
            headers={
                "Cache-Control": "public, max-age=3600",
                "Access-Control-Allow-Origin": "*",
            },
        )

    # ── Alice webhook (D18.1 — Яндекс Диалоги входной канал) ───────
    # Алиса POST-ит сюда каждый запрос пользователя. Мы:
    #  1. валидируем skill_id (отбиваем чужие навыки)
    #  2. session.new → приветствие
    #  3. command "выход"/"стоп"/"хватит" → end_session=true
    #  4. иначе — роутим в Router и возвращаем ответ
    # КРИТИЧНО: Alice ждёт ответ ≤2 сек, иначе "навык не отвечает".
    # Поэтому Router.dispatch обёрнут в asyncio.wait_for; fallback на короткий
    # apology если не успели.
    ALICE_EXIT_WORDS = {
        "выход", "стоп", "хватит", "пока", "до свидания",
        "закрой навык", "закрыть", "конец",
    }
    ALICE_GREETING = "Слушаю, Босс."

    def _alice_response(text: str, session: AliceSessionIn, end: bool = False, tts: str | None = None) -> dict:
        resp: dict = {"text": text[:1024], "end_session": end}
        if tts:
            resp["tts"] = tts[:1024]
        return {
            "response": resp,
            "session": {
                "session_id": session.session_id,
                "message_id": session.message_id,
                "user_id": session.user_id,
            },
            "version": "1.0",
        }

    @app.post("/api/alice/webhook")
    async def alice_webhook(payload: AliceWebhookRequest) -> dict:
        sess = payload.session
        cmd = (payload.request.command or payload.request.original_utterance or "").strip()

        # Защита по skill_id (если выставлен в .env)
        expected_id = settings.alice_skill_id
        if expected_id and sess.skill_id and sess.skill_id != expected_id:
            logger.warning("alice_skill_id_mismatch", got=sess.skill_id, expected=expected_id)
            return _alice_response(
                "Этот навык настроен на другого Босса. Сорян.",
                sess, end=True,
            )

        # Приветствие при открытии навыка
        if sess.new and not cmd:
            return _alice_response(ALICE_GREETING, sess, end=False)

        # Выход
        low = cmd.lower().strip(".!?")
        if low in ALICE_EXIT_WORDS:
            return _alice_response("До связи, Босс.", sess, end=True)

        if not cmd:
            return _alice_response("Босс, я не расслышал. Повтори?", sess, end=False)

        # Публикуем USER_INPUT в bus
        request_id = uuid.uuid4().hex[:12]
        clean_text, threats = PromptGuard.sanitize_input(cmd, channel="alice")
        if threats:
            logger.warning("alice_input_sanitized", threats=threats)

        await bus.publish(JarvisEvent(
            type=EventType.USER_INPUT,
            source="channel:alice",
            channel="alice",
            request_id=request_id,
            data={"text": clean_text, "user_id": sess.user_id},
        ))

        # Роутим с жёстким timeout — Алиса ждёт ≤2 сек
        timeout = settings.alice_response_timeout_sec
        try:
            reply = await asyncio.wait_for(
                router.dispatch(
                    text=clean_text,
                    history=[Message(role="user", content=clean_text)],
                    channel="alice",
                    request_id=request_id,
                ),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            logger.warning("alice_dispatch_timeout", timeout=timeout, text=clean_text[:60])
            reply = "Босс, сейчас думаю — переспроси через пару секунд."
        except Exception as e:
            logger.error("alice_dispatch_failed", error=str(e))
            reply = "Босс, технические проблемы."

        safe_reply, leaks = PromptGuard.filter_output(reply)
        if leaks:
            logger.error("alice_output_redacted", leaks=leaks)

        # Публикуем финальный ответ
        await bus.publish(JarvisEvent(
            type=EventType.ASSISTANT_REPLY,
            source="router",
            channel="alice",
            request_id=request_id,
            data={"text": safe_reply, "speakable": True},
        ))

        # tts: для Alice TTS нужно убрать markdown/ID/url — в простом MVP оставляем как есть
        return _alice_response(safe_reply, sess, end=False, tts=safe_reply)

    # ── WebSocket: live events + ввод ───────────────────────────────
    @app.websocket("/ws")
    async def ws_endpoint(websocket: WebSocket) -> None:
        await websocket.accept()
        state.clients.add(websocket)
        logger.info("web_hud_client_connected", total=len(state.clients))

        # Отправить historical context (последние сообщения)
        await websocket.send_json({
            "snapshot": {
                "history": [
                    {"role": m.role, "content": m.content}
                    for m in state.history
                ],
            }
        })

        try:
            while True:
                raw = await websocket.receive_text()
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                text = (payload.get("text") or "").strip()
                if not text:
                    continue

                request_id = uuid.uuid4().hex[:12]

                # Sanitize
                clean_text, threats = PromptGuard.sanitize_input(text, channel=CHANNEL)
                if threats:
                    logger.warning("web_hud_input_sanitized", threats=threats)

                # Публикуем user_input в bus
                await bus.publish(JarvisEvent(
                    type=EventType.USER_INPUT,
                    source=f"channel:{CHANNEL}",
                    channel=CHANNEL,
                    request_id=request_id,
                    data={"text": clean_text},
                ))

                # Добавляем в историю и роутим
                state.history.append(Message(role="user", content=clean_text))
                try:
                    reply = await router.dispatch(
                        text=clean_text,
                        history=list(state.history),
                        channel=CHANNEL,
                        request_id=request_id,
                    )
                except Exception as e:
                    logger.error("web_hud_router_error", error=str(e))
                    reply = "Босс, технические проблемы — не дотянулся до Claude."

                safe_reply, leaks = PromptGuard.filter_output(reply)
                if leaks:
                    logger.error("web_hud_output_redacted", leaks=leaks)

                state.history.append(Message(role="assistant", content=safe_reply))

                # Публикуем финальный ответ
                await bus.publish(JarvisEvent(
                    type=EventType.ASSISTANT_REPLY,
                    source="router",
                    channel=CHANNEL,
                    request_id=request_id,
                    data={"text": safe_reply},
                ))

                # Tier 2 persistence
                await memory.append_exchange_async(
                    user_text=clean_text,
                    assistant_text=safe_reply,
                    channel=CHANNEL,
                )
                # Tier 3 — векторная память (fire-and-forget, не блокируем ответ)
                import asyncio as _aio
                _aio.create_task(memory.add_to_vector(clean_text, role="user", channel=CHANNEL))
                _aio.create_task(memory.add_to_vector(safe_reply, role="assistant", channel=CHANNEL))

        except WebSocketDisconnect:
            state.clients.discard(websocket)
            logger.info("web_hud_client_disconnected", total=len(state.clients))
        except Exception as e:
            logger.error("web_hud_ws_error", error=str(e))
            state.clients.discard(websocket)

    return app


async def run_web_hud(host: str = "127.0.0.1", port: int = 8000) -> None:
    """Точка входа."""
    import uvicorn

    setup_logging()
    logger.info("web_hud_starting", host=host, port=port)

    from core.providers import build_smart_provider
    claude = build_smart_provider(settings)
    memory = MemoryManager(workspace_dir=WORKSPACE_DIR)

    app = build_app(claude, memory)

    # C16: запускаем network watchdog фоном — он публикует SYSTEM events
    # с network_state в bus каждые 30 сек.
    watchdog = NetworkWatchdog(bus)
    await watchdog.start()

    # Reminders scheduler — фоном проверяет workspace/reminders.json
    # и публикует ASSISTANT_REPLY когда пора напомнить.
    from core.skills.reminders_skill import ReminderScheduler, RemindersStore
    reminders_store = RemindersStore(WORKSPACE_DIR / "reminders.json")
    reminder_scheduler = ReminderScheduler(reminders_store)
    await reminder_scheduler.start()

    # F3: Proactive watcher — JARVIS первый инициирует. Тик каждые 60 сек.
    from core.proactive import ProactiveWatcher
    from core.skills.todo_skill import TodoStore as _TodoStore
    proactive_watcher = ProactiveWatcher(
        claude=claude,
        memory=memory,
        workspace_dir=WORKSPACE_DIR,
        todo_store=_TodoStore(WORKSPACE_DIR / "todo.json"),
        reminders_store=reminders_store,
    )
    await proactive_watcher.start()

    config = uvicorn.Config(
        app, host=host, port=port, log_level="warning", access_log=False
    )
    server = uvicorn.Server(config)
    try:
        await server.serve()
    finally:
        await proactive_watcher.stop()
        await reminder_scheduler.stop()
        await watchdog.stop()
        await claude.close()
