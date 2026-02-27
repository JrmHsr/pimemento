"""OpenAI embedder using text-embedding-3-small.

Requires: pip install pimemento[embeddings-openai]
"""

from __future__ import annotations

from pimemento.embeddings.base import Embedder


class OpenAIEmbedder(Embedder):
    """OpenAI API-based embedder."""

    def __init__(
        self,
        api_key: str,
        model: str = "text-embedding-3-small",
        dimensions: int = 1536,
    ) -> None:
        self._model = model
        self._dimensions = dimensions
        from openai import AsyncOpenAI

        self._client = AsyncOpenAI(api_key=api_key)

    @property
    def dimensions(self) -> int:
        return self._dimensions

    async def embed(self, text: str) -> list[float]:
        resp = await self._client.embeddings.create(
            input=[text],
            model=self._model,
            dimensions=self._dimensions,
        )
        return resp.data[0].embedding

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        resp = await self._client.embeddings.create(
            input=texts,
            model=self._model,
            dimensions=self._dimensions,
        )
        return [item.embedding for item in resp.data]
