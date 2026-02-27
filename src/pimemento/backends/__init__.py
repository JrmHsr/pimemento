"""Backend factory.

Lazy-imports the concrete backend to avoid loading heavy dependencies
(asyncpg, torch) when using JSON mode.
"""

from __future__ import annotations

from pimemento.backends.base import MemoryBackend, MemoryEntry
from pimemento.config import PimementoConfig

__all__ = ["get_backend", "MemoryBackend", "MemoryEntry"]


async def get_backend(config: PimementoConfig) -> MemoryBackend:
    """Instantiate the correct backend from config."""
    if config.backend == "postgres":
        from pimemento.backends.postgres_backend import PostgresBackend

        backend = PostgresBackend(config)
        await backend.initialize()
        return backend
    else:
        from pimemento.backends.json_backend import JsonBackend

        return JsonBackend(config)
