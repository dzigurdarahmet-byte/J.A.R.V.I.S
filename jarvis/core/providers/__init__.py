"""LLM providers - Claude primary, Deepseek/YandexGPT alternates, Ollama offline, Smart facade."""

from pathlib import Path

from .base import LLMProvider, Message
from .claude import ClaudeProvider
from .deepseek import DeepseekProvider
from .fallback import FallbackProvider
from .ollama import OllamaProvider
from .smart import SmartProvider
from .yandex_gpt import YandexGPTProvider

__all__ = [
    "LLMProvider",
    "Message",
    "ClaudeProvider",
    "DeepseekProvider",
    "YandexGPTProvider",
    "OllamaProvider",
    "FallbackProvider",
    "SmartProvider",
    "build_smart_provider",
]


def build_smart_provider(settings) -> SmartProvider:
    """Собрать SmartProvider из core.config.settings.

    Default chain (choice=auto): Claude → Deepseek → YandexGPT → Ollama.
    Если ключ для какого-то провайдера не задан — он не подключается, цепочка
    пересобирается без него.

    Босс может переключить primary через голосовую команду «пользуйся клодом /
    дипсиком / яндексом / ollama / авто» — выбор сохраняется в
    `workspace/llm_choice.txt` и читается `SmartProvider.get_choice()` перед
    каждым `chat()` (с mtime-кэшем, чтобы не дёргать диск зря).
    """
    claude = ClaudeProvider(api_key=settings.anthropic_api_key.get_secret_value())

    deepseek = None
    if settings.deepseek_api_key:
        try:
            deepseek = DeepseekProvider(
                api_key=settings.deepseek_api_key.get_secret_value(),
            )
        except Exception:
            deepseek = None

    fallback = None
    if settings.yandex_api_key and settings.yandex_folder_id:
        try:
            fallback = YandexGPTProvider(
                api_key=settings.yandex_api_key.get_secret_value(),
                folder_id=settings.yandex_folder_id,
            )
        except Exception:
            fallback = None
    # Ollama offline-fallback: модель по умолчанию qwen2.5:7b (см. docs/OFFLINE_MODE.md)
    offline = OllamaProvider()

    # Путь к файлу выбора Босса — единый стейт между перезапусками.
    choice_path = Path(settings.workspace_dir) / "llm_choice.txt"

    return SmartProvider(
        primary=claude,
        fallback=fallback,
        offline=offline,
        deepseek=deepseek,
        choice_path=choice_path,
    )
