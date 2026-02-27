"""Abstract embedder interface."""

from __future__ import annotations

from abc import ABC, abstractmethod


class Embedder(ABC):
    """Turns text into a float vector for semantic operations."""

    @property
    @abstractmethod
    def dimensions(self) -> int:
        """Vector dimensionality."""
        ...

    @abstractmethod
    async def embed(self, text: str) -> list[float]:
        """Embed a single text string."""
        ...

    @abstractmethod
    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed multiple texts. Returns list of float vectors."""
        ...
