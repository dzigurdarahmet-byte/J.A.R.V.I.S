"""Skill: управление умным домом через Yandex Smart Home API.

Босс уже имеет Алису с Zigbee-хабом и подключённые устройства (лампочки,
розетки, датчики). Yandex IoT API — публичный REST поверх его аккаунта.
JARVIS отправляет команды через HTTPS, Алиса передаёт через Zigbee →
устройства реагируют.

API:
  GET  https://api.iot.yandex.net/v1.0/user/info
       → структура: rooms[], devices[], groups[]
       → device: id, name, type, capabilities[], state{}
  POST https://api.iot.yandex.net/v1.0/devices/actions
       → body: {"devices": [{"id": "...", "actions": [{...}]}]}

Auth: OAuth token в Authorization: Bearer <token>.
Получение токена — см. config.py:yandex_iot_token + scripts/get_yandex_iot_token.py.

Триггеры:
  - «включи свет [на кухне]», «выключи свет [в спальне]»
  - «выключи розетку [у телевизора]»
  - «приглуши свет / свет на максимум / свет на 30 процентов»
  - «выключи всё» (group action — all lights off)
  - «какие у меня устройства» / «что подключено»
  - «температура [в комнате]» (если есть climate sensor)

L1 keyword + L2 tool-use.

Если токен не настроен — skill отвечает инструкцией как его получить.
"""
from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from core.config import settings
from core.logging import get_logger
from core.router import SkillResult
from core.skills.base import KeywordSkill

logger = get_logger(__name__)


_BASE_URL = "https://api.iot.yandex.net/v1.0"
_DEVICES_CACHE_TTL_SEC = 300.0  # 5 минут — устройства не меняются часто


# ── Device model (упрощённый) ───────────────────────────────────────
@dataclass(slots=True)
class IoTDevice:
    id: str
    name: str
    type: str           # devices.types.light, .socket, .thermostat, .sensor, ...
    room: str = ""      # имя комнаты (для fuzzy match «свет на кухне»)
    capabilities: list[dict] = field(default_factory=list)
    state: dict = field(default_factory=dict)

    @property
    def is_light(self) -> bool:
        return "light" in self.type

    @property
    def is_socket(self) -> bool:
        return "socket" in self.type

    @property
    def is_switchable(self) -> bool:
        return any(c.get("type", "").endswith(".on_off") for c in self.capabilities)


# ── Cache ───────────────────────────────────────────────────────────
@dataclass
class _DeviceCache:
    devices: list[IoTDevice] = field(default_factory=list)
    rooms_by_id: dict[str, str] = field(default_factory=dict)
    fetched_at: float = 0.0

    def is_fresh(self) -> bool:
        return (time.time() - self.fetched_at) < _DEVICES_CACHE_TTL_SEC


# ── API client ──────────────────────────────────────────────────────
async def _fetch_user_info(token: str) -> dict | None:
    """GET /user/info. Возвращает raw dict или None при ошибке."""
    try:
        async with httpx.AsyncClient(timeout=10.0, trust_env=False) as client:
            r = await client.get(
                f"{_BASE_URL}/user/info",
                headers={"Authorization": f"Bearer {token}"},
            )
            r.raise_for_status()
            return r.json()
    except httpx.HTTPStatusError as e:
        logger.warning(
            "yandex_iot_http_error",
            status=e.response.status_code,
            body=e.response.text[:200],
        )
        return None
    except (httpx.HTTPError, ValueError) as e:
        logger.warning("yandex_iot_fetch_failed", error=str(e)[:200])
        return None


async def _send_actions(token: str, devices: list[dict]) -> dict | None:
    """POST /devices/actions с массивом устройств и действий."""
    try:
        async with httpx.AsyncClient(timeout=10.0, trust_env=False) as client:
            r = await client.post(
                f"{_BASE_URL}/devices/actions",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                json={"devices": devices},
            )
            r.raise_for_status()
            return r.json()
    except httpx.HTTPStatusError as e:
        logger.warning(
            "yandex_iot_action_failed",
            status=e.response.status_code,
            body=e.response.text[:200],
        )
        return None
    except (httpx.HTTPError, ValueError) as e:
        logger.warning("yandex_iot_action_error", error=str(e)[:200])
        return None


def _parse_user_info(data: dict) -> tuple[list[IoTDevice], dict[str, str]]:
    """Распарсить /user/info → (devices, rooms_by_id)."""
    rooms_by_id: dict[str, str] = {}
    for room in data.get("rooms", []):
        rid = room.get("id")
        rname = (room.get("name") or "").strip()
        if rid:
            rooms_by_id[rid] = rname
    devices: list[IoTDevice] = []
    for d in data.get("devices", []):
        room_id = d.get("room")
        devices.append(IoTDevice(
            id=d.get("id", ""),
            name=(d.get("name") or "").strip(),
            type=d.get("type", ""),
            room=rooms_by_id.get(room_id, ""),
            capabilities=d.get("capabilities", []) or [],
            state=d.get("state", {}) or {},
        ))
    return devices, rooms_by_id


# ── Fuzzy matching ──────────────────────────────────────────────────
def _normalize(s: str) -> str:
    """Lowercase + collapse spaces — для substring match."""
    return re.sub(r"\s+", " ", (s or "").lower()).strip()


def _find_devices(
    devices: list[IoTDevice],
    name_query: str | None,
    room_query: str | None,
    only_type: str | None = None,
) -> list[IoTDevice]:
    """Найти устройства по name/room (substring, case-insensitive).

    only_type='light' / 'socket' — фильтр по типу.
    """
    name_q = _normalize(name_query) if name_query else None
    room_q = _normalize(room_query) if room_query else None
    out: list[IoTDevice] = []
    for d in devices:
        if only_type == "light" and not d.is_light:
            continue
        if only_type == "socket" and not d.is_socket:
            continue
        name_ok = name_q is None or name_q in _normalize(d.name)
        room_ok = room_q is None or room_q in _normalize(d.room)
        if name_ok and room_ok:
            out.append(d)
    return out


# ── Action builders ─────────────────────────────────────────────────
def _action_on_off(turn_on: bool) -> dict:
    """Capability действие включения/выключения."""
    return {
        "type": "devices.capabilities.on_off",
        "state": {"instance": "on", "value": turn_on},
    }


def _action_brightness(percent: int) -> dict:
    """Установить яркость 0-100%."""
    percent = max(0, min(100, percent))
    return {
        "type": "devices.capabilities.range",
        "state": {"instance": "brightness", "value": percent},
    }


def _action_backlight(turn_on: bool) -> dict:
    """Подсветка (toggle/backlight) — для Яндекс Станций."""
    return {
        "type": "devices.capabilities.toggle",
        "state": {"instance": "backlight", "value": turn_on},
    }


# Имена цветов → HSV (h: 0-360, s: 0-100, v: 0-100).
# Станция 3 использует color_model='hsv' (RGB её API не принимает).
_COLOR_HSV: dict[str, tuple[int, int, int]] = {
    "белый":      (0,   0, 100),
    "красный":    (0,  100, 100),
    "оранжевый":  (30, 100, 100),
    "жёлтый":     (60, 100, 100),
    "желтый":     (60, 100, 100),
    "зелёный":    (120, 100, 100),
    "зеленый":    (120, 100, 100),
    "голубой":    (180, 100, 100),
    "синий":      (240, 100, 100),
    "фиолетовый": (270, 100, 100),
    "пурпурный":  (300, 100, 100),
    "розовый":    (330,  60, 100),
    "малиновый":  (348, 100,  86),
    "бирюзовый":  (174,  72,  88),
    "лиловый":    (300,  20, 100),
}


def _action_color(color_name: str) -> dict | None:
    """Установить цвет подсветки через HSV. None если цвет неизвестен."""
    hsv = _COLOR_HSV.get(color_name.lower())
    if hsv is None:
        return None
    h, s, v = hsv
    return {
        "type": "devices.capabilities.color_setting",
        "state": {"instance": "hsv", "value": {"h": h, "s": s, "v": v}},
    }


# Сцены подсветки (только для Станции 3).
_COLOR_SCENES: dict[str, str] = {
    "вечеринка":     "party",
    "закат":         "sunset",
    "рассвет":       "sunrise",
    "ночь":          "night",
    "лава лампа":    "lava_lamp",
    "лава":          "lava_lamp",
    "лето":          "summer",
    "романтика":     "romance",
    "светомузыка":   "color_music",
    "свеча":         "candle",
    "северное сияние": "polar_lights",
    "стробоскоп":    "stroboscope",
    "переливы":      "color_shine",
    "циркадный":     "circadian",
    "чудо":          "miracle",
    "игра":          "gaming",
    "гейминг":       "gaming",
    "времена года":  "seasons",
}


def _action_scene(scene_id: str) -> dict:
    """Включить сценарий подсветки (party/candle/night/...)."""
    return {
        "type": "devices.capabilities.color_setting",
        "state": {"instance": "scene", "value": scene_id},
    }


# ── Query parsing ───────────────────────────────────────────────────
# «включи/выключи [свет/розетку] [в/на/у комнате]»
_ON_RE = re.compile(
    r"\b(?:включи|зажги|открой|активируй)\s+"
    r"(?P<target>(?:свет|лампу?|розетку|телевизор|вентилятор|обогреватель|"
    r"увлажнитель|кондиционер|плиту|вытяжку)?)"
    r"(?:\s+(?:в|на|у)\s+(?P<room>[А-Яа-яёЁ\-\s]+?))?(?:[.!?]|$)",
    re.IGNORECASE | re.UNICODE,
)
_OFF_RE = re.compile(
    r"\b(?:выключи|погаси|закрой|вырубай?|отключи|деактивируй)\s+"
    r"(?P<target>(?:свет|лампу?|розетку|телевизор|вентилятор|обогреватель|"
    r"увлажнитель|кондиционер|плиту|вытяжку|всё|все|весь\s+свет)?)"
    r"(?:\s+(?:в|на|у)\s+(?P<room>[А-Яа-яёЁ\-\s]+?))?(?:[.!?]|$)",
    re.IGNORECASE | re.UNICODE,
)
_BRIGHTNESS_RE = re.compile(
    r"\b(?:свет\s+)?(?:на\s+)?(\d+)\s*(?:%|процент\w*)\b"
    r"|\b(?:яркость|свет)\s+на\s+(?P<word>максимум|минимум|половин\w+)\b"
    r"|\bприглуши\s+свет\b",
    re.IGNORECASE | re.UNICODE,
)
_LIST_DEVICES_RE = re.compile(
    r"\bкакие\s+(?:у\s+меня\s+)?(?:устройств\w*|приборы|лампы|розетки)\b"
    r"|\bчто\s+(?:у\s+меня\s+)?подключен\w*\b"
    r"|\bсписок\s+устройств\w*\b"
    r"|\bпокажи\s+(?:мои\s+)?устройств\w*\b",
    re.IGNORECASE | re.UNICODE,
)

# Подсветка Станции: «зажги синий», «синяя подсветка», «мигни», «выключи подсветку»
_BACKLIGHT_COLOR_RE = re.compile(
    r"\b(?:зажги|зажечь|включи|поставь|сделай|переключи)\s+"
    r"(?:на\s+)?(?:станции?\s+|колонке\s+)?"
    r"(?P<color>белый|красный|оранжевый|жёлтый|желтый|зелёный|зеленый|"
    r"голубой|синий|фиолетовый|пурпурный|розовый|малиновый|бирюзовый|лиловый)"
    r"(?:\s+(?:свет|цвет|подсветк\w*))?\b",
    re.IGNORECASE | re.UNICODE,
)
_BACKLIGHT_ON_RE = re.compile(
    r"\b(?:включи|зажги)\s+подсветк\w*\b",
    re.IGNORECASE | re.UNICODE,
)
_BACKLIGHT_OFF_RE = re.compile(
    r"\b(?:выключи|погаси|отключи)\s+подсветк\w*\b",
    re.IGNORECASE | re.UNICODE,
)
_SCENE_RE = re.compile(
    r"\b(?:включи|зажги|поставь|сделай)\s+"
    r"(?:сцен\w+\s+|режим\s+|подсветк\w+\s+)?"
    r"(?P<scene>вечеринк\w+|закат|рассвет|ночь|лава\s+лампу?|лава|лето|"
    r"романтик\w+|светомузык\w+|свеч\w*|северное\s+сияние|стробоскоп|"
    r"переливы|циркадн\w+|чудо|игра|гейминг|времена\s+года)\b",
    re.IGNORECASE | re.UNICODE,
)


def _parse_brightness_target(text: str) -> int | None:
    """Извлечь желаемую яркость 0-100. None если не указана."""
    m = _BRIGHTNESS_RE.search(text)
    if not m:
        return None
    if m.group(1):
        return int(m.group(1))
    word = (m.group("word") or "").lower()
    if "максимум" in word:
        return 100
    if "минимум" in word:
        return 10
    if "половин" in word:
        return 50
    if "приглуши" in text.lower():
        return 30
    return None


# ── Skill ───────────────────────────────────────────────────────────
class YandexSmartHomeSkill(KeywordSkill):
    """L1+L2 skill: управление умным домом через Yandex IoT API."""

    name = "yandex_smart_home"
    keywords = [
        r"\b(?:включи|зажги|открой|активируй)\s+"
        r"(?:свет|лампу?|розетку|телевизор|вентилятор|обогреватель|"
        r"увлажнитель|кондиционер|плиту|вытяжку)\b",
        r"\b(?:выключи|погаси|закрой|вырубай?|отключи|деактивируй)\s+"
        r"(?:свет|лампу?|розетку|телевизор|вентилятор|обогреватель|"
        r"увлажнитель|кондиционер|плиту|вытяжку|всё|весь\s+свет)\b",
        r"\b(?:яркость|свет)\s+на\s+(?:максимум|минимум|половин\w+|\d+)\b",
        r"\bприглуши\s+свет\b",
        r"\bкакие\s+(?:у\s+меня\s+)?(?:устройств\w*|приборы|лампы|розетки)\b",
        r"\bчто\s+(?:у\s+меня\s+)?подключен\w*\b",
        r"\bсписок\s+устройств\w*\b",
        r"\bпокажи\s+(?:мои\s+)?устройств\w*\b",
        # Подсветка Станции
        r"\b(?:зажги|включи|поставь)\s+(?:на\s+)?(?:станции?\s+|колонке\s+)?"
        r"(?:белый|красный|оранжевый|жёлтый|желтый|зелёный|зеленый|"
        r"голубой|синий|фиолетовый|пурпурный|розовый|малиновый|бирюзовый|лиловый)\b",
        r"\b(?:включи|зажги|выключи|погаси|отключи)\s+подсветк\w*\b",
        # Сцены Станции
        r"\b(?:включи|зажги|поставь|сделай)\s+(?:сцен\w+\s+|режим\s+|подсветк\w+\s+)?"
        r"(?:вечеринк\w+|закат|рассвет|ночь|лава\s+лампа|лава\s+лампу|лето|"
        r"романтик\w+|светомузык\w+|свеч\w+|северное\s+сияние|стробоскоп|"
        r"переливы|циркадн\w+|чудо|игра|гейминг|времена\s+года)\b",
    ]

    def __init__(self) -> None:
        super().__init__()
        self._cache = _DeviceCache()

    def _token(self) -> str | None:
        t = settings.yandex_iot_token
        if t is None:
            return None
        value = t.get_secret_value() if hasattr(t, "get_secret_value") else str(t)
        return value.strip() or None

    async def _get_devices(self, force: bool = False) -> list[IoTDevice]:
        if not force and self._cache.is_fresh():
            return self._cache.devices
        token = self._token()
        if not token:
            return []
        data = await _fetch_user_info(token)
        if data is None:
            return self._cache.devices  # вернём что было
        devices, rooms = _parse_user_info(data)
        self._cache.devices = devices
        self._cache.rooms_by_id = rooms
        self._cache.fetched_at = time.time()
        logger.info(
            "yandex_iot_devices_fetched",
            count=len(devices),
            rooms=len(rooms),
        )
        return devices

    async def run(self, text: str, request_id: str) -> SkillResult:
        token = self._token()
        if not token:
            return SkillResult(
                text=(
                    "Босс, для управления умным домом нужен Yandex IoT-токен. "
                    "Запусти `python jarvis/scripts/get_yandex_iot_token.py` "
                    "и положи токен в `.env` как `YANDEX_IOT_TOKEN=...`."
                ),
                speakable=True,
            )

        # 1) Список устройств
        if _LIST_DEVICES_RE.search(text):
            return await self._handle_list()

        # 1.5) Подсветка Станции
        if m := _BACKLIGHT_COLOR_RE.search(text):
            return await self._handle_speaker_color(m.group("color"))
        if m := _SCENE_RE.search(text):
            scene_query = m.group("scene").lower().strip()
            # нормализуем «лава лампа» → «лава лампа», ловим обе записи
            scene_id = _COLOR_SCENES.get(scene_query) or _COLOR_SCENES.get(
                re.sub(r"\s+", " ", scene_query)
            )
            # Также пробуем по подстроке (для падежей: «вечеринку» → "вечеринк")
            if scene_id is None:
                for kw, sid in _COLOR_SCENES.items():
                    if kw.startswith(scene_query[:5]):
                        scene_id = sid
                        break
            if scene_id:
                return await self._handle_speaker_scene(scene_id, scene_query)
        if _BACKLIGHT_ON_RE.search(text):
            return await self._handle_speaker_backlight(turn_on=True)
        if _BACKLIGHT_OFF_RE.search(text):
            return await self._handle_speaker_backlight(turn_on=False)

        # 2) Brightness change
        brightness = _parse_brightness_target(text)
        # 3) Включение
        if m := _ON_RE.search(text):
            target = (m.group("target") or "").lower().strip()
            room = (m.group("room") or "").strip()
            return await self._handle_switch(
                turn_on=True, target=target, room=room, brightness=brightness,
            )
        # 4) Выключение (включая «выключи всё»)
        if m := _OFF_RE.search(text):
            target = (m.group("target") or "").lower().strip()
            room = (m.group("room") or "").strip()
            if target in ("всё", "все", "весь свет"):
                return await self._handle_switch_all_lights(turn_on=False)
            return await self._handle_switch(
                turn_on=False, target=target, room=room,
            )
        # 5) Чисто яркость без включения («приглуши свет», «свет на 50 процентов»)
        if brightness is not None:
            return await self._handle_switch(
                turn_on=True, target="свет", room="", brightness=brightness,
            )

        return SkillResult(
            text=(
                "Не понял команду умному дому. Скажи «включи свет в кухне», "
                "«выключи розетку», «свет на 30 процентов» или «список устройств»."
            ),
            speakable=True,
        )

    # ── Handlers ────────────────────────────────────────────────────
    async def _handle_list(self) -> SkillResult:
        devices = await self._get_devices()
        if not devices:
            return SkillResult(
                text="Не нашёл устройств — проверь токен и подключение к Yandex IoT.",
                speakable=True,
            )
        lines = []
        for d in devices[:15]:
            mark = "✓" if d.state.get("online") is not False else "✗"
            room = f" ({d.room})" if d.room else ""
            lines.append(f"  {mark} {d.name}{room} — {d.type.split('.')[-1]}")
        return SkillResult(
            text=f"Подключено устройств: {len(devices)}.\n" + "\n".join(lines),
            speakable=True,
            data={"count": len(devices)},
        )

    def _target_to_type_filter(self, target: str) -> str | None:
        """Сопоставить русское слово с device type filter."""
        if not target:
            return None
        if target in ("свет", "лампа", "лампу"):
            return "light"
        if target == "розетку":
            return "socket"
        # Для остальных (телевизор, кондиционер) — None: ищем по name substring
        return None

    async def _handle_switch(
        self,
        turn_on: bool,
        target: str,
        room: str,
        brightness: int | None = None,
    ) -> SkillResult:
        devices = await self._get_devices()
        if not devices:
            return SkillResult(
                text="Нет устройств в кэше — проверь токен и API доступ.",
                speakable=True,
            )

        type_filter = self._target_to_type_filter(target)
        # Если target не light/socket — ищем по name substring
        name_query = None if type_filter else (target or None)
        room_query = room or None

        matches = _find_devices(devices, name_query, room_query, only_type=type_filter)
        if not matches:
            human_target = target or "устройство"
            human_room = f" в комнате «{room}»" if room else ""
            return SkillResult(
                text=f"Не нашёл «{human_target}»{human_room} в твоих устройствах.",
                speakable=True,
            )

        # Build action payload
        action = _action_on_off(turn_on)
        actions = [action]
        if brightness is not None and turn_on:
            actions.append(_action_brightness(brightness))

        payload = [{"id": d.id, "actions": actions} for d in matches]
        token = self._token()
        result = await _send_actions(token, payload)  # type: ignore[arg-type]
        if result is None:
            return SkillResult(
                text="Не получилось отправить команду — проверь логи.",
                speakable=True,
            )

        verb = "Включил" if turn_on else "Выключил"
        if len(matches) == 1:
            d = matches[0]
            room_suffix = f" ({d.room})" if d.room else ""
            extra = f", яркость {brightness}%" if brightness is not None and turn_on else ""
            return SkillResult(
                text=f"{verb} {d.name}{room_suffix}{extra}.",
                speakable=True,
                data={"device_ids": [d.id]},
            )
        return SkillResult(
            text=f"{verb} {len(matches)} устройств: {', '.join(d.name for d in matches[:5])}",
            speakable=True,
            data={"device_ids": [d.id for d in matches]},
        )

    async def _find_smart_speakers(self) -> list[IoTDevice]:
        """Все smart_speaker в аккаунте (обычно одна Алиса)."""
        devices = await self._get_devices()
        return [d for d in devices if "smart_speaker" in d.type]

    async def _handle_speaker_color(self, color: str) -> SkillResult:
        """Изменить цвет подсветки на Станции (через HSV)."""
        speakers = await self._find_smart_speakers()
        if not speakers:
            return SkillResult(text="Не нашёл умных колонок.", speakable=True)
        action = _action_color(color)
        if action is None:
            return SkillResult(text=f"Цвет «{color}» не знаю.", speakable=True)
        token = self._token()
        payload = [{"id": d.id, "actions": [action]} for d in speakers]
        result = await _send_actions(token, payload)  # type: ignore[arg-type]
        if result is None:
            return SkillResult(text="Не получилось отправить команду.", speakable=True)
        # Проверим reach
        devs = (result.get("devices") or [])
        if devs:
            for c in devs[0].get("capabilities", []):
                ar = c.get("state", {}).get("action_result", {})
                if ar.get("status") == "ERROR" and ar.get("error_code") == "DEVICE_UNREACHABLE":
                    return SkillResult(
                        text="Станция оффлайн — проверь питание и Wi-Fi.",
                        speakable=True,
                    )
        name = speakers[0].name if len(speakers) == 1 else f"{len(speakers)} колонках"
        return SkillResult(
            text=f"Зажёг {color} цвет на {name}.",
            speakable=True,
            data={"color": color, "devices": [d.id for d in speakers]},
        )

    async def _handle_speaker_backlight(self, turn_on: bool) -> SkillResult:
        """Вкл/выкл подсветку Станции."""
        speakers = await self._find_smart_speakers()
        if not speakers:
            return SkillResult(text="Не нашёл умных колонок.", speakable=True)
        token = self._token()
        action = _action_backlight(turn_on)
        payload = [{"id": d.id, "actions": [action]} for d in speakers]
        result = await _send_actions(token, payload)  # type: ignore[arg-type]
        if result is None:
            return SkillResult(text="Не получилось отправить команду.", speakable=True)
        verb = "Включил" if turn_on else "Выключил"
        return SkillResult(
            text=f"{verb} подсветку колонки.",
            speakable=True,
            data={"backlight": turn_on},
        )

    async def _handle_speaker_scene(self, scene_id: str, scene_name: str) -> SkillResult:
        """Включить сценарий подсветки (party/candle/lava_lamp/...)."""
        speakers = await self._find_smart_speakers()
        if not speakers:
            return SkillResult(text="Не нашёл умных колонок.", speakable=True)
        token = self._token()
        action = _action_scene(scene_id)
        payload = [{"id": d.id, "actions": [action]} for d in speakers]
        result = await _send_actions(token, payload)  # type: ignore[arg-type]
        if result is None:
            return SkillResult(text="Не получилось отправить команду.", speakable=True)
        # Проверим что reach ok
        devs = (result.get("devices") or [])
        if devs:
            caps = devs[0].get("capabilities", [])
            for c in caps:
                ar = c.get("state", {}).get("action_result", {})
                if ar.get("status") == "ERROR":
                    err = ar.get("error_code", "UNKNOWN")
                    if err == "DEVICE_UNREACHABLE":
                        return SkillResult(
                            text="Станция оффлайн — проверь питание и Wi-Fi.",
                            speakable=True,
                        )
                    return SkillResult(text=f"Yandex отказал: {err}.", speakable=True)
        return SkillResult(
            text=f"Включил сцену «{scene_name}» на колонке.",
            speakable=True,
            data={"scene_id": scene_id},
        )

    async def _handle_switch_all_lights(self, turn_on: bool) -> SkillResult:
        """Выключить/включить все источники света сразу."""
        devices = await self._get_devices()
        lights = [d for d in devices if d.is_light and d.is_switchable]
        if not lights:
            return SkillResult(text="Не нашёл ни одной лампы.", speakable=True)
        action = _action_on_off(turn_on)
        payload = [{"id": d.id, "actions": [action]} for d in lights]
        token = self._token()
        result = await _send_actions(token, payload)  # type: ignore[arg-type]
        if result is None:
            return SkillResult(text="Команда не прошла.", speakable=True)
        verb = "Включил" if turn_on else "Погасил"
        return SkillResult(
            text=f"{verb} весь свет ({len(lights)} ламп).",
            speakable=True,
            data={"count": len(lights)},
        )

    # ── L2 Tool-use ─────────────────────────────────────────────────
    def as_tool(self) -> dict[str, Any]:
        return {
            "name": "yandex_smart_home",
            "description": (
                "Control Boss's smart home devices via Yandex IoT API "
                "(lights, sockets, climate). Actions: turn on/off, set "
                "brightness 0-100%, list devices. Device search by name "
                "substring + optional room name. Yandex Alice acts as "
                "Zigbee hub — devices already paired in Boss's account."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["on", "off", "brightness", "list", "off_all_lights"],
                    },
                    "target": {
                        "type": "string",
                        "description": (
                            "Device name substring (e.g. 'свет', 'розетка', "
                            "'торшер'). Empty for off_all_lights/list."
                        ),
                    },
                    "room": {
                        "type": "string",
                        "description": "Optional room name substring (e.g. 'кухня').",
                    },
                    "brightness": {
                        "type": "integer",
                        "description": "0-100, only for action='brightness' or with 'on'.",
                    },
                },
                "required": ["action"],
            },
        }

    async def run_with_args(
        self, args: dict[str, Any], request_id: str
    ) -> SkillResult:
        action = (args.get("action") or "").lower().strip()
        target = (args.get("target") or "").strip()
        room = (args.get("room") or "").strip()
        brightness = args.get("brightness")

        if action == "list":
            return await self._handle_list()
        if action == "off_all_lights":
            return await self._handle_switch_all_lights(turn_on=False)
        if action == "on":
            return await self._handle_switch(
                turn_on=True, target=target, room=room,
                brightness=int(brightness) if brightness is not None else None,
            )
        if action == "off":
            return await self._handle_switch(
                turn_on=False, target=target, room=room,
            )
        if action == "brightness":
            if brightness is None:
                return SkillResult(text="Нужно указать brightness.", speakable=True)
            return await self._handle_switch(
                turn_on=True, target=target or "свет", room=room,
                brightness=int(brightness),
            )
        return SkillResult(
            text=f"Неизвестное действие smart_home: {action!r}.",
            speakable=True,
        )
