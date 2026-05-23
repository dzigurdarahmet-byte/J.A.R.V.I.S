"""MusicSkill — Yandex Music через JARVIS-аудио (FreeBuds / dynamics).

Скилл получает stream-URL трека через yandex_music API, скачивает MP3,
декодирует через soundfile (без ffmpeg — soundfile сам умеет MP3), и
проигрывает в default audio output через play_audio() — те же FreeBuds
что озвучивают TTS-ответы.

Поддерживается фоновое воспроизведение (один background-task на JARVIS),
прерывание через stop_event, переключение на следующий трек. Если Босс
скажет «стоп» — текущий трек прерывается.

Триггеры:
  - «включи музыку», «поставь музыку», «поиграй музыку», «играй» — рандом из лайков
  - «поставь <запрос>», «включи <запрос>», «играй <запрос>» — поиск+первый трек
  - «следующий трек», «дальше», «следующая песня» — следующий из очереди/новый поиск
  - «стоп», «выключи музыку», «хватит» — остановить
  - «что играет» — название текущего трека
  - «мои плейлисты», «мои лайки», «рекомендации» — метаданные
"""

from __future__ import annotations

import asyncio
import io
import random
import re
from dataclasses import dataclass, field
from typing import Any

import httpx
import numpy as np
import soundfile as sf

# play_audio импортируем ЛЕНИВО внутри _stream_and_play — иначе
# circular import: music_skill → channels.local_voice → loop →
# core.skills.registry → music_skill. На MVP это безопасно — функция
# вызывается только при реальном проигрывании.
from core.config import settings
from core.logging import get_logger
from core.router import SkillResult
from core.skills.base import KeywordSkill

logger = get_logger(__name__)


# ── Player state (singleton per-process) ────────────────────────────
@dataclass
class _PlayerState:
    """Состояние background-плеера: текущий task + stop_event + инфа о треке."""
    task: asyncio.Task | None = None
    stop_event: asyncio.Event | None = None
    track_title: str = ""
    track_artist: str = ""
    queue: list = field(default_factory=list)  # list of yandex_music.Track

    def is_playing(self) -> bool:
        return self.task is not None and not self.task.done()


# ── Streaming pipeline ──────────────────────────────────────────────
async def _download_mp3(url: str, timeout: float = 60.0) -> bytes:
    """Скачать mp3-стрим напрямую (Yandex CDN, без прокси)."""
    async with httpx.AsyncClient(trust_env=False, timeout=timeout) as client:
        r = await client.get(url, follow_redirects=True)
        r.raise_for_status()
        return r.content


def _decode_mp3(mp3_bytes: bytes) -> tuple[np.ndarray, int]:
    """MP3 bytes → (float32 mono, sample_rate). soundfile сам умеет MP3."""
    audio, sr = sf.read(io.BytesIO(mp3_bytes), dtype="float32")
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    return audio.astype(np.float32), sr


async def _stream_and_play(track, stop_event: asyncio.Event) -> None:
    """Скачать трек и проиграть в default output до конца или до stop_event."""
    from channels.local_voice.audio import play_audio  # lazy to break cycle
    try:
        # 1) Получить URL стрима (sync через to_thread — yandex_music sync API)
        info_list = await asyncio.to_thread(track.get_download_info)
        if not info_list:
            logger.warning("music_no_download_info", track_id=track.id)
            return
        best = max(info_list, key=lambda x: x.bitrate_in_kbps)
        url = await asyncio.to_thread(best.get_direct_link)
        logger.info(
            "music_track_url",
            bitrate=best.bitrate_in_kbps,
            codec=best.codec,
            track_id=track.id,
        )

        # 2) Скачать (если stop уже сработал — выходим)
        if stop_event.is_set():
            return
        mp3_bytes = await _download_mp3(url)

        if stop_event.is_set():
            return

        # 3) Декодировать
        audio, sr = await asyncio.to_thread(_decode_mp3, mp3_bytes)
        logger.info(
            "music_play_start",
            duration_sec=round(len(audio) / sr, 1),
            sample_rate=sr,
        )

        # 4) Проиграть с поддержкой прерывания
        await play_audio(audio, sample_rate=sr, stop_event=stop_event)
    except asyncio.CancelledError:
        logger.info("music_play_cancelled")
        raise
    except Exception as e:  # noqa: BLE001 — диагностика
        logger.error("music_play_failed", error=str(e)[:200])


# ── Skill ───────────────────────────────────────────────────────────
class MusicSkill(KeywordSkill):
    name = "music"
    keywords = [
        # Управление воспроизведением
        r"\b(?:включи|поставь|играй|поиграй|запусти)\s+(?:музыку|музло|плейлист|трек|песню|альбом)\b",
        r"\b(?:включи|поставь|играй|поиграй)\s+[А-Яа-яёЁA-Za-z0-9\-\s]+",
        r"\b(?:следующ\w+\s+(?:трек|песн\w*)|дальше|next)\b",
        # «стоп» / «останови» / «хватит» — отдельно для коротких команд
        r"\b(?:стоп|останови(?:сь)?|хватит)\b",
        r"\bвыключи\s+музык\w*\b",
        r"\bпауза\b",
        # Метаданные
        r"\bчто\s+играет\b",
        r"\bдай\s+рекомендации\b",
        r"\bмои\s+(?:плейлисты|лайки|треки)\b",
        r"\bмузыкальные\s+рекомендации\b",
        r"\bяндекс\s+музыка\b",
    ]

    _client = None  # lazy yandex_music.Client
    _player = _PlayerState()  # class-level singleton

    @classmethod
    def _get_client(cls):
        if cls._client is not None:
            return cls._client
        token = getattr(settings, "yandex_music_token", None)
        if not token:
            return None
        secret = token.get_secret_value() if hasattr(token, "get_secret_value") else str(token)
        if not secret.strip():
            return None
        try:
            from yandex_music import Client
            cls._client = Client(secret.strip()).init()
            logger.info("yandex_music_ready")
            return cls._client
        except Exception as e:  # noqa: BLE001
            logger.error("yandex_music_init_failed", error=str(e))
            return None

    # ── Intent dispatch ─────────────────────────────────────────────
    async def run(self, text: str, request_id: str) -> SkillResult:
        low = text.lower().strip()

        # Стоп — не требует клиента
        if re.search(r"\b(?:стоп|останови(?:сь)?|хватит|пауза|выключи\s+музык\w*)\b", low):
            return self._handle_stop()

        client = self._get_client()
        if client is None:
            return SkillResult(
                text=(
                    "Босс, для Яндекс.Музыки нужен токен. Запусти "
                    "`python jarvis\\scripts\\get_yandex_music_token.py` "
                    "и положи токен в `.env` как YANDEX_MUSIC_TOKEN=..."
                ),
                speakable=True,
            )

        # Следующий трек
        if re.search(r"\b(?:следующ\w+\s+(?:трек|песн\w*)|дальше|next)\b", low):
            return await self._handle_next(client)

        # Что играет
        if re.search(r"\bчто\s+играет\b", low):
            return self._handle_now_playing()

        # Поиск+играть с запросом: «поставь Imagine Dragons», «включи блюз»
        m = re.search(
            r"\b(?:включи|поставь|играй|поиграй|запусти)\s+"
            r"(?:музыку|трек|песню|альбом|плейлист)?\s*(.+?)$",
            text, re.IGNORECASE | re.UNICODE,
        )
        if m:
            query = m.group(1).strip().strip('.,!?')
            # Если query пустой (например, «включи музыку») — играем рандом из лайков
            if not query or query.lower() in ("музыку", "музло", "плейлист", "трек", "песню", "альбом"):
                return await self._handle_play_likes_random(client)
            return await self._handle_search_and_play(client, query)

        # Метаданные fallback
        if "плейлист" in low and ("мои" in low or "покажи" in low):
            return await self._handle_my_playlists(client)
        if "лайк" in low or ("мои" in low and "треки" in low):
            return await self._handle_my_likes(client)
        if "рекомендац" in low:
            return await self._handle_recommendations(client)

        return SkillResult(
            text="Не понял. Скажи «включи музыку», «поставь Imagine Dragons», «стоп» или «следующий трек».",
            speakable=True,
        )

    # ── Handlers ────────────────────────────────────────────────────
    async def _handle_search_and_play(self, client, query: str) -> SkillResult:
        try:
            result = await asyncio.to_thread(client.search, query, type_="track")
        except Exception as e:  # noqa: BLE001
            return SkillResult(text=f"Поиск не сработал: {e}", speakable=True)
        if not result or not result.tracks or not result.tracks.results:
            return SkillResult(text=f"По «{query}» ничего не нашёл.", speakable=True)
        track = result.tracks.results[0]
        # очередь — оставшиеся в результатах
        self._player.queue = list(result.tracks.results[1:10])
        await self._start_play(track)
        artist = ", ".join(a.name for a in track.artists[:2]) if track.artists else "—"
        return SkillResult(
            text=f"Играю «{track.title}» — {artist}.",
            speakable=True,
            data={"track": track.title, "artist": artist, "track_id": str(track.id)},
        )

    async def _handle_play_likes_random(self, client) -> SkillResult:
        try:
            likes = await asyncio.to_thread(client.users_likes_tracks)
        except Exception as e:  # noqa: BLE001
            return SkillResult(text=f"Лайки не открыть: {e}", speakable=True)
        if not likes or not likes.tracks:
            return SkillResult(text="Лайков нет — добавь треки в Яндекс.Музыке.", speakable=True)
        # tracks тут — TrackShort, нужны Track-объекты через fetch
        random_short = random.choice(likes.tracks)
        track = await asyncio.to_thread(random_short.fetch_track)
        # Заполним очередь ещё несколькими лайками
        self._player.queue = []
        for ts in random.sample(likes.tracks, min(10, len(likes.tracks))):
            if str(ts.id) != str(random_short.id):
                self._player.queue.append(ts)
        await self._start_play(track)
        artist = ", ".join(a.name for a in track.artists[:2]) if track.artists else "—"
        return SkillResult(
            text=f"Играю «{track.title}» — {artist}.",
            speakable=True,
        )

    async def _handle_next(self, client) -> SkillResult:
        if not self._player.queue:
            return SkillResult(
                text="Очередь пустая. Скажи «включи музыку» или «поставь <запрос>».",
                speakable=True,
            )
        next_item = self._player.queue.pop(0)
        # next_item может быть TrackShort или Track
        if hasattr(next_item, "fetch_track"):
            track = await asyncio.to_thread(next_item.fetch_track)
        else:
            track = next_item
        await self._start_play(track)
        artist = ", ".join(a.name for a in track.artists[:2]) if track.artists else "—"
        return SkillResult(
            text=f"Дальше: «{track.title}» — {artist}.",
            speakable=True,
        )

    def _handle_stop(self) -> SkillResult:
        if not self._player.is_playing():
            return SkillResult(text="Музыка и так не играет.", speakable=True)
        if self._player.stop_event:
            self._player.stop_event.set()
        return SkillResult(text="Останавливаю.", speakable=True)

    def _handle_now_playing(self) -> SkillResult:
        if not self._player.is_playing():
            return SkillResult(text="Сейчас ничего не играет.", speakable=True)
        return SkillResult(
            text=f"Сейчас: «{self._player.track_title}» — {self._player.track_artist}.",
            speakable=True,
        )

    async def _handle_my_playlists(self, client) -> SkillResult:
        try:
            pls = await asyncio.to_thread(client.users_playlists_list)
        except Exception as e:  # noqa: BLE001
            return SkillResult(text=f"Плейлисты не открыть: {e}", speakable=True)
        if not pls:
            return SkillResult(text="Своих плейлистов нет.", speakable=True)
        lines = ["Твои плейлисты:"]
        for p in pls[:10]:
            lines.append(f"  • {p.title} ({p.track_count} треков)")
        return SkillResult(text="\n".join(lines), speakable=False)

    async def _handle_my_likes(self, client) -> SkillResult:
        try:
            likes = await asyncio.to_thread(client.users_likes_tracks)
        except Exception as e:  # noqa: BLE001
            return SkillResult(text=f"Лайки не открыть: {e}", speakable=True)
        n = len(likes.tracks) if likes and likes.tracks else 0
        return SkillResult(text=f"У тебя {n} лайкнутых треков.", speakable=True)

    async def _handle_recommendations(self, client) -> SkillResult:
        try:
            feed = await asyncio.to_thread(client.feed)
        except Exception as e:  # noqa: BLE001
            return SkillResult(text=f"Рекомендации не открыть: {e}", speakable=True)
        items = []
        if feed and getattr(feed, "generated_playlists", None):
            for gp in feed.generated_playlists[:5]:
                items.append(gp.data)
        if not items:
            return SkillResult(text="Рекомендаций нет (нужен Plus?).", speakable=True)
        lines = ["Сегодняшние рекомендации:"]
        for p in items:
            if hasattr(p, "title"):
                lines.append(f"  • {p.title}")
        return SkillResult(text="\n".join(lines), speakable=False)

    # ── Background player control ───────────────────────────────────
    async def _start_play(self, track) -> None:
        """Запустить background-task проигрывания. Прерывает предыдущий если есть."""
        # 1) Прерываем старый
        if self._player.is_playing():
            if self._player.stop_event:
                self._player.stop_event.set()
            try:
                await asyncio.wait_for(self._player.task, timeout=2.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass
        # 2) Новый stop_event + task
        self._player.stop_event = asyncio.Event()
        self._player.track_title = track.title or "?"
        self._player.track_artist = (
            ", ".join(a.name for a in track.artists[:2]) if track.artists else "—"
        )
        self._player.task = asyncio.create_task(
            _stream_and_play(track, self._player.stop_event),
            name=f"music-{track.id}",
        )

    # ── L2 Tool-use ─────────────────────────────────────────────────
    def as_tool(self) -> dict[str, Any]:
        return {
            "name": "music",
            "description": (
                "Control Yandex Music playback through JARVIS audio output "
                "(streams to default device = Boss's FreeBuds). Actions: "
                "play (with optional search query), next, stop, now_playing. "
                "Plus metadata fetch (likes/playlists/recommendations)."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["play", "next", "stop", "now_playing",
                                 "playlists", "likes", "recommendations"],
                    },
                    "query": {
                        "type": "string",
                        "description": "Search query for action='play' (track/artist/album).",
                    },
                },
                "required": ["action"],
            },
        }

    async def run_with_args(self, args: dict[str, Any], request_id: str) -> SkillResult:
        action = (args.get("action") or "").lower().strip()
        query = (args.get("query") or "").strip()
        if action == "stop":
            return self._handle_stop()
        if action == "now_playing":
            return self._handle_now_playing()
        client = self._get_client()
        if client is None:
            return SkillResult(text="No Yandex Music token configured.", speakable=True)
        if action == "play":
            if query:
                return await self._handle_search_and_play(client, query)
            return await self._handle_play_likes_random(client)
        if action == "next":
            return await self._handle_next(client)
        if action == "playlists":
            return await self._handle_my_playlists(client)
        if action == "likes":
            return await self._handle_my_likes(client)
        if action == "recommendations":
            return await self._handle_recommendations(client)
        return SkillResult(text=f"Unknown music action: {action!r}.", speakable=True)
