"""Centralized configuration via pydantic-settings.

All env vars from .env are validated here. Import `settings` from this module
anywhere you need config — never re-read os.environ directly.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """JARVIS runtime configuration."""

    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[2] / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        env_ignore_empty=True,  # пустые "KEY=" → field default (None), не "" → ValidationError
    )

    # === LLM Providers ===
    anthropic_api_key: SecretStr = Field(default=SecretStr(""))
    groq_api_key: SecretStr | None = None
    gigachat_auth_key: SecretStr | None = None
    # Deepseek — OpenAI-compatible (api.deepseek.com/v1). Используется как
    # альтернатива Claude через voice command «пользуйся дипсиком».
    deepseek_api_key: SecretStr | None = None

    # === Telegram ===
    telegram_bot_token: SecretStr = Field(default=SecretStr(""))
    telegram_bot_username: str = ""
    telegram_owner_chat_id: int | None = None
    telegram_proxy_url: str | None = None  # socks5://host:port или http://host:port

    # === External APIs ===
    openweather_api_key: SecretStr | None = None
    newsapi_key: SecretStr | None = None

    # === Yandex AI Studio (Tier 3 embeddings, опционально TTS Алёна/Захар) ===
    yandex_api_key: SecretStr | None = None
    yandex_folder_id: str | None = None

    # === Yandex.XML (поиск; legacy через yandex.ru/dev/xml — отдельный от Cloud) ===
    # Регистрация: yandex.ru/dev/xml → получить login + key (free 100 req/day)
    # Если оба не заданы — WebSearchSkill использует DuckDuckGo fallback.
    yandex_xml_user: str | None = None
    yandex_xml_key: SecretStr | None = None

    # === Default location ===
    # Город по умолчанию для briefings / weather / погодных триггеров.
    # Используется когда Босс не указывает явно ("какая погода?").
    jarvis_default_city: str = "Сыктывкар"

    # === Voice input tuning (для BT-наушников типа FreeBuds Pro 4) ===
    # input_gain — multiplier на mic-chunk перед VAD. Для BT Hands-Free
    # ставить 6-10 (чтобы тихая речь -76 dB поднималась до -56…-60).
    # min_rms_db — порог anti_hallucination gate. При gain 8x речь Босса
    # приходит уже на нормальном уровне, дефолт -55 хватает.
    # speaker_similarity_threshold — Resemblyzer GE2E порог owner_voice.
    # Если поменял микрофон vs тем что embedding writing, понизить с 0.65.
    jarvis_audio_input_gain: float = 1.0
    jarvis_min_rms_db: float = -55.0
    jarvis_speaker_similarity_threshold: float = 0.65

    # === Alice (Яндекс Диалоги, входной канал) ===
    # Регистрация навыка: dialogs.yandex.ru → создать новый Навык типа Алиса,
    # тип Webhook, URL = https://YOUR-TUNNEL/api/alice/webhook.
    # alice_skill_id — id навыка (из URL панели разработчика), сравниваем
    # в webhook чтобы не принимать запросы от чужих навыков.
    # alice_response_timeout_sec — Алиса ждёт ответ ≤2 сек, иначе пользователю
    # покажется "Навык не отвечает".
    alice_skill_id: str | None = None
    alice_response_timeout_sec: float = 1.5

    # === JARVIS tone polish ===
    # Какие каналы пропускают ответы skill'ов через Claude для перефразировки
    # в Marvel JARVIS-стиле. По умолчанию — HUD и Telegram (там latency не критична).
    # Голосовой канал и Алиса не полируются — там жёсткие тайминги.
    jarvis_polish_channels: str = "web_hud,telegram"

    # === Geo (поиск мест и геокодинг) ===
    twogis_api_key: SecretStr | None = None       # 2GIS Catalog API (POI поиск)
    yandex_geocoder_key: SecretStr | None = None  # Yandex Geocoder JS API key (адреса)

    # === Yandex Music ===
    yandex_music_token: SecretStr | None = None   # Music OAuth token из браузера

    # === Yandex Smart Home (Алиса как Zigbee/Wi-Fi хаб) ===
    # OAuth token для api.iot.yandex.net/v1.0. Получение:
    #   1) https://oauth.yandex.ru/client/new — зарегистрировать app
    #   2) Включить permissions "Yandex IoT: управление устройствами"
    #   3) Открыть https://oauth.yandex.ru/authorize?response_type=token&client_id=<ID>
    #   4) Скопировать access_token из URL после авторизации
    yandex_iot_token: SecretStr | None = None

    # === Selectel ===
    selectel_api_key: SecretStr | None = None
    selectel_project_id: str | None = None

    # === Runtime ===
    jarvis_env: str = "development"
    jarvis_log_level: str = "INFO"
    jarvis_owner_name: str = "Boss"
    jarvis_owner_addr: str = "Boss"

    # === Infrastructure ===
    redis_url: str = "redis://localhost:6379"
    sqlite_path: str = "./workspace/jarvis.db"
    workspace_dir: str = "./workspace"

    # === Computed ===
    @property
    def workspace_path(self) -> Path:
        return Path(self.workspace_dir).resolve()

    @property
    def is_production(self) -> bool:
        return self.jarvis_env == "production"


settings = Settings()
