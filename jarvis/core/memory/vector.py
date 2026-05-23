"""Tier 3: векторная память (ChromaDB + multilingual embeddings).

Хранит ВСЕ обмены (user→assistant) с возможностью поиска по смыслу:
«Что я говорил про подписку на Spotify?» — найдёт реплики даже если прошёл
месяц и точной фразы там не было.

Архитектура:
    EmbeddingProvider (Protocol)
        ├── LocalSTProvider    — sentence-transformers, paraphrase-multilingual-MiniLM-L12-v2 (offline, бесплатно)
        └── YandexEmbeddings   — Yandex AI Studio embeddings (0,0101 ₽/1k токенов, точнее)

    VectorMemory
        ├── ChromaDB persistent client
        ├── 1 collection per workspace
        └── add() / search() / size()
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Protocol

from core.logging import get_logger

logger = get_logger(__name__)

# Размер эмбеддинга у paraphrase-multilingual-MiniLM-L12-v2 = 384
# YandexGPT embeddings = 256 (doc) / 256 (query)
# Используем разные collections если хочется переключаться между провайдерами.

DEFAULT_TOP_K = 5
COLLECTION_NAME = "jarvis_memory"


class EmbeddingProvider(Protocol):
    name: str
    dim: int

    async def embed(self, texts: list[str]) -> list[list[float]]: ...


# ──────────────────────────────────────────────────────────────────────
# Local provider (sentence-transformers)
# ──────────────────────────────────────────────────────────────────────


class LocalSTProvider:
    """sentence-transformers, multilingual, CPU.
    Модель paraphrase-multilingual-MiniLM-L12-v2 (~118MB), 50+ языков — хорошо ловит русский.
    """

    name = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    dim = 384

    def __init__(self) -> None:
        self._model = None

    def _ensure_model(self) -> None:
        if self._model is not None:
            return
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as e:
            raise RuntimeError(
                "sentence-transformers не установлен; нужен 'uv pip install sentence-transformers'"
            ) from e
        logger.info("vector_loading_st_model", model=self.name)
        self._model = SentenceTransformer(self.name, device="cpu")
        logger.info("vector_st_model_ready")

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self._ensure_model()
        # encode синхронный → пускаем в thread executor чтобы не блокировать event loop
        return await asyncio.to_thread(
            lambda: self._model.encode(texts, normalize_embeddings=True).tolist()
        )


# ──────────────────────────────────────────────────────────────────────
# Yandex AI Studio embeddings (опционально)
# ──────────────────────────────────────────────────────────────────────


class YandexEmbeddings:
    """YandexGPT embeddings.

    https://aistudio.yandex.ru/docs/ru/foundation-models/concepts/embeddings
    Тариф 0,0101 ₽ / 1000 токенов.
    """

    name = "yandex/text-search-doc"
    dim = 256

    def __init__(self, api_key: str, folder_id: str) -> None:
        if not api_key or not folder_id:
            raise ValueError("YandexEmbeddings: нужны api_key и folder_id")
        self._api_key = api_key
        self._folder_id = folder_id

    async def embed(self, texts: list[str]) -> list[list[float]]:
        import httpx

        url = "https://llm.api.cloud.yandex.net/foundationModels/v1/textEmbedding"
        headers = {"Authorization": f"Api-Key {self._api_key}"}
        out: list[list[float]] = []
        async with httpx.AsyncClient(timeout=15.0) as client:
            for text in texts:
                payload = {
                    "modelUri": f"emb://{self._folder_id}/text-search-doc/latest",
                    "text": text,
                }
                r = await client.post(url, json=payload, headers=headers)
                r.raise_for_status()
                data = r.json()
                out.append([float(x) for x in data["embedding"]])
        return out


# ──────────────────────────────────────────────────────────────────────
# VectorMemory (ChromaDB)
# ──────────────────────────────────────────────────────────────────────


class VectorMemory:
    """Persistent векторное хранилище.

    Backend:
        - ChromaDB если установлен (предпочтительно).
        - In-memory + pickle persistence иначе (fallback).
    """

    def __init__(
        self,
        db_dir: Path,
        provider: EmbeddingProvider | None = None,
    ) -> None:
        self._db_dir = Path(db_dir)
        self._db_dir.mkdir(parents=True, exist_ok=True)
        self._provider = provider or LocalSTProvider()
        self._client = None
        self._collection = None
        self._fallback = None  # InMemoryStore если chromadb не подгрузился

    def _ensure_client(self) -> None:
        if self._client is not None or self._fallback is not None:
            return
        try:
            import chromadb
            from chromadb.config import Settings
        except ImportError:
            logger.info("chromadb_missing_using_inmemory_fallback")
            self._fallback = InMemoryStore(self._db_dir / "inmem.pkl")
            return
        self._client = chromadb.PersistentClient(
            path=str(self._db_dir),
            settings=Settings(anonymized_telemetry=False),
        )
        # Один коллекция на всю память. Метаданные дают per-channel/role фильтрацию.
        coll_name = f"{COLLECTION_NAME}__{self._provider.name.replace('/', '_')}"
        self._collection = self._client.get_or_create_collection(
            name=coll_name,
            metadata={"hnsw:space": "cosine"},
        )
        logger.info(
            "vector_memory_ready",
            path=str(self._db_dir),
            collection=coll_name,
            provider=self._provider.name,
            count=self._collection.count(),
        )

    async def add(
        self,
        text: str,
        role: str = "user",
        channel: str = "",
        timestamp: float | None = None,
        extra: dict[str, Any] | None = None,
    ) -> str:
        """Добавить запись. Returns id."""
        if not text or len(text.strip()) < 3:
            return ""
        self._ensure_client()
        try:
            emb = await self._provider.embed([text])
        except Exception as e:
            logger.warning("vector_embed_failed", error=str(e))
            return ""
        rec_id = uuid.uuid4().hex
        meta = {
            "role": role,
            "channel": channel,
            "ts": float(timestamp or time.time()),
        }
        if extra:
            meta.update({k: str(v) for k, v in extra.items()})
        if self._fallback is not None:
            self._fallback.add(rec_id, emb[0], text, meta)
            return rec_id
        # Chroma .add синхронный — в executor
        await asyncio.to_thread(
            self._collection.add,
            ids=[rec_id],
            embeddings=emb,
            documents=[text],
            metadatas=[meta],
        )
        return rec_id

    async def search(
        self,
        query: str,
        limit: int = DEFAULT_TOP_K,
        where: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Семантический поиск. Returns list[{text, role, channel, ts, score}]."""
        if not query or len(query.strip()) < 2:
            return []
        self._ensure_client()
        try:
            emb = await self._provider.embed([query])
        except Exception as e:
            logger.warning("vector_query_embed_failed", error=str(e))
            return []
        if self._fallback is not None:
            return self._fallback.search(emb[0], limit=limit)
        result = await asyncio.to_thread(
            self._collection.query,
            query_embeddings=emb,
            n_results=limit,
            where=where,
        )
        # Chroma возвращает списки списков (для batch query). У нас 1 query → берём [0].
        docs = (result.get("documents") or [[]])[0]
        metas = (result.get("metadatas") or [[]])[0]
        dists = (result.get("distances") or [[]])[0]
        out: list[dict[str, Any]] = []
        for doc, meta, dist in zip(docs, metas, dists, strict=False):
            out.append({
                "text": doc,
                "role": (meta or {}).get("role", ""),
                "channel": (meta or {}).get("channel", ""),
                "ts": (meta or {}).get("ts", 0.0),
                "score": round(1.0 - float(dist), 3),  # cosine distance -> similarity
            })
        return out

    def size(self) -> int:
        self._ensure_client()
        if self._fallback is not None:
            return self._fallback.size()
        return int(self._collection.count())


# ──────────────────────────────────────────────────────────────────────
# In-memory fallback (если ChromaDB не установлен)
# ──────────────────────────────────────────────────────────────────────


class InMemoryStore:
    """Простой in-memory векторный store с pickle persistence.

    Структура: list[(id, embedding_np, text, meta)]. Cosine similarity через
    numpy. Подходит до ~10000 записей; дальше ChromaDB обязателен.
    """

    def __init__(self, persist_path: Path) -> None:
        import pickle

        import numpy as np

        self._np = np
        self._pickle = pickle
        self._path = Path(persist_path)
        self._records: list[tuple[str, Any, str, dict]] = []
        if self._path.exists():
            try:
                with open(self._path, "rb") as f:
                    self._records = pickle.load(f)
                logger.info("inmem_vector_loaded", count=len(self._records), path=str(self._path))
            except Exception as e:
                logger.warning("inmem_vector_load_failed", error=str(e))

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._path, "wb") as f:
                self._pickle.dump(self._records, f)
        except Exception as e:
            logger.warning("inmem_vector_save_failed", error=str(e))

    def add(self, rec_id: str, embedding: list[float], text: str, meta: dict) -> None:
        emb = self._np.asarray(embedding, dtype=self._np.float32)
        norm = float(self._np.linalg.norm(emb) or 1.0)
        emb = emb / norm  # store normalized, cosine ↔ dot product
        self._records.append((rec_id, emb, text, meta))
        if len(self._records) % 10 == 0:
            self._save()

    def search(self, query_emb: list[float], limit: int = DEFAULT_TOP_K) -> list[dict]:
        if not self._records:
            return []
        q = self._np.asarray(query_emb, dtype=self._np.float32)
        q = q / float(self._np.linalg.norm(q) or 1.0)
        embs = self._np.stack([r[1] for r in self._records])
        scores = embs @ q  # cosine similarity (всё нормализовано)
        idx = self._np.argsort(-scores)[:limit]
        out: list[dict] = []
        for i in idx:
            i = int(i)
            _id, _emb, text, meta = self._records[i]
            out.append({
                "text": text,
                "role": (meta or {}).get("role", ""),
                "channel": (meta or {}).get("channel", ""),
                "ts": (meta or {}).get("ts", 0.0),
                "score": round(float(scores[i]), 3),
            })
        return out

    def size(self) -> int:
        return len(self._records)


# ──────────────────────────────────────────────────────────────────────
# Factory
# ──────────────────────────────────────────────────────────────────────


def make_vector_memory(workspace_dir: Path) -> VectorMemory | None:
    """Создать VectorMemory с auto-выбором провайдера.

    Если в .env есть YANDEX_API_KEY + YANDEX_FOLDER_ID — Yandex. Иначе локальный.
    Returns None если ничего не получилось (нет deps).
    """
    from core.config import settings

    db_dir = Path(workspace_dir) / "vector_db"
    provider: EmbeddingProvider | None = None

    try:
        yk = getattr(settings, "yandex_api_key", None)
        yf = getattr(settings, "yandex_folder_id", None)
        if yk and yf and yk.get_secret_value() and yf:
            provider = YandexEmbeddings(yk.get_secret_value(), str(yf))
            logger.info("vector_provider_chosen", provider="yandex")
    except Exception:
        pass

    if provider is None:
        try:
            provider = LocalSTProvider()
            logger.info("vector_provider_chosen", provider="local-st")
        except Exception as e:
            logger.warning("vector_provider_init_failed", error=str(e))
            return None

    try:
        return VectorMemory(db_dir=db_dir, provider=provider)
    except Exception as e:
        logger.warning("vector_memory_init_failed", error=str(e))
        return None
