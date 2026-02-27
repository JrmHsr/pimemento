"""Local embedder using sentence-transformers.

Requires: pip install pimemento[embeddings-local]
Uses all-MiniLM-L6-v2 by default (384 dimensions, fast, multilingual-capable).
"""

from __future__ import annotations

import asyncio
import threading

from pimemento.embeddings.base import Embedder


class LocalEmbedder(Embedder):
    """sentence-transformers based local embedder.

    Lazy-loads the model on first use (thread-safe singleton).
    """

    def __init__(
        self,
        model_name: str = "all-MiniLM-L6-v2",
        dimensions: int = 384,
    ) -> None:
        self._model_name = model_name
        self._dimensions = dimensions
        self._model = None
        self._lock = threading.Lock()

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def _get_model(self):
        """Thread-safe lazy model loading."""
        if self._model is not None:
            return self._model
        with self._lock:
            if self._model is not None:
                return self._model
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self._model_name)
            return self._model

    def _encode_sync(self, texts: list[str]) -> list[list[float]]:
        model = self._get_model()
        vectors = model.encode(
            texts,
            normalize_embeddings=True,
            convert_to_numpy=True,
        )
        return vectors.tolist()

    async def embed(self, text: str) -> list[float]:
        results = await asyncio.to_thread(self._encode_sync, [text])
        return results[0]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return await asyncio.to_thread(self._encode_sync, texts)
