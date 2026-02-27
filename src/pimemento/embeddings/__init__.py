"""Embedder factory.

Lazy-imports embedder implementations to avoid loading heavy dependencies
(torch, sentence-transformers) when not needed.
"""

from __future__ import annotations

from pimemento.config import PimementoConfig
from pimemento.embeddings.base import Embedder

__all__ = ["get_embedder", "Embedder"]


def get_embedder(config: PimementoConfig) -> Embedder | None:
    """Return the configured Embedder, or None if disabled."""
    if config.embedding_provider == "local":
        from pimemento.embeddings.local_embedder import LocalEmbedder

        return LocalEmbedder(
            model_name=config.embedding_model,
            dimensions=config.embedding_dimensions,
        )
    elif config.embedding_provider == "openai":
        if not config.openai_api_key:
            raise ValueError(
                "OPENAI_API_KEY is required when EMBEDDING_PROVIDER=openai"
            )
        from pimemento.embeddings.openai_embedder import OpenAIEmbedder

        return OpenAIEmbedder(
            api_key=config.openai_api_key,
            model=config.embedding_model,
            dimensions=config.embedding_dimensions,
        )
    return None
