"""Backend abstraction layer.

Defines MemoryEntry (unified data model) and MemoryBackend (ABC).
Both JSON and Postgres backends implement MemoryBackend.
"""

from __future__ import annotations

import logging
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class MemoryEntry:
    """Unified data model for a single memory entry.

    Maps to a dict in JSON files or a row in the Postgres `memories` table.
    """

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    client_id: str = "_default"
    user_id: str = "_anonymous"
    namespace: str = "general"
    content: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    category: str = ""
    type: str = ""
    reason: str = ""
    embedding: list[float] | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime | None = None
    source_mcp: str = ""
    merged_from: list[str] = field(default_factory=list)

    def to_json_dict(self) -> dict[str, Any]:
        """Serialize for JSON file storage (excludes embedding)."""
        d: dict[str, Any] = {
            "id": self.id,
            "client_id": self.client_id,
            "user_id": self.user_id,
            "namespace": self.namespace,
            "content": self.content,
            "category": self.category,
            "type": self.type,
            "reason": self.reason,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }
        if self.metadata:
            d["metadata"] = self.metadata
        if self.expires_at:
            d["expires_at"] = self.expires_at.isoformat()
        if self.source_mcp:
            d["source_mcp"] = self.source_mcp
        if self.merged_from:
            d["merged_from"] = self.merged_from
        return d

    @classmethod
    def from_json_dict(cls, d: dict[str, Any]) -> MemoryEntry:
        """Deserialize from JSON file storage.

        Handles backward compatibility with v2.0 fields:
        - "date" -> "created_at"
        - "ttl_days" -> "expires_at"
        """
        created_raw = d.get("created_at") or d.get("date", "")
        updated_raw = d.get("updated_at") or created_raw

        # Backward compat: convert ttl_days to expires_at
        expires_at = None
        expires_raw = d.get("expires_at")
        if expires_raw:
            expires_at = _parse_dt(expires_raw)
        elif d.get("ttl_days"):
            try:
                ttl = int(d["ttl_days"])
                created = _parse_dt(created_raw) if created_raw else datetime.now(timezone.utc)
                expires_at = created + timedelta(days=ttl)
            except (ValueError, TypeError):
                pass

        return cls(
            id=d.get("id", str(uuid.uuid4())),
            client_id=d.get("client_id", "_default"),
            user_id=d.get("user_id", "_anonymous"),
            namespace=d.get("namespace", "general"),
            content=d.get("content", ""),
            metadata=d.get("metadata", {}),
            category=d.get("category", ""),
            type=d.get("type", ""),
            reason=d.get("reason", ""),
            created_at=_parse_dt(created_raw) if created_raw else datetime.now(timezone.utc),
            updated_at=_parse_dt(updated_raw) if updated_raw else datetime.now(timezone.utc),
            expires_at=expires_at,
            source_mcp=d.get("source_mcp", ""),
            merged_from=d.get("merged_from", []),
        )


def _parse_dt(raw: str | None) -> datetime:
    """Parse an ISO datetime string, handling timezone markers."""
    if not raw:
        return datetime.now(timezone.utc)
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        logger.warning("Failed to parse datetime '%s', falling back to now()", raw)
        return datetime.now(timezone.utc)


class MemoryBackend(ABC):
    """Abstract base class for memory storage backends.

    All methods are async. The JSON backend wraps sync I/O
    in asyncio.to_thread() to maintain a uniform interface.
    """

    @abstractmethod
    async def save(self, entry: MemoryEntry) -> MemoryEntry:
        """Save or update a memory entry. Returns the persisted entry."""
        ...

    @abstractmethod
    async def get(
        self,
        client_id: str,
        *,
        user_id: str = "",
        namespace: str = "",
        category: str = "",
        type: str = "",
        limit: int = 20,
    ) -> list[MemoryEntry]:
        """Retrieve entries matching filters. Prunes expired entries."""
        ...

    @abstractmethod
    async def delete(
        self,
        client_id: str,
        content_match: str,
        *,
        user_id: str = "",
        namespace: str = "",
        category: str = "",
    ) -> MemoryEntry | None:
        """Delete most recent entry matching content_match. Returns deleted entry or None."""
        ...

    @abstractmethod
    async def status(
        self,
        client_id: str,
        *,
        user_id: str = "",
        namespace: str = "",
    ) -> dict[str, Any]:
        """Return metadata: {count, namespaces, categories, oldest, newest, ttl_count}."""
        ...

    @abstractmethod
    async def search(
        self,
        query: str,
        client_id: str,
        *,
        user_id: str = "",
        namespace: str = "",
        limit: int = 10,
        query_embedding: list[float] | None = None,
    ) -> list[tuple[MemoryEntry, float]]:
        """Search by meaning (cosine on Postgres) or substring (JSON fallback).

        Returns list of (entry, score) tuples sorted by relevance desc.
        Score: cosine similarity (Postgres) or 1.0 for substring matches (JSON).
        """
        ...

    @abstractmethod
    async def find_duplicates(
        self,
        entry: MemoryEntry,
        threshold: float,
    ) -> list[tuple[MemoryEntry, float]]:
        """Find entries that are duplicates of the given entry.

        JSON backend: key-based overlap (score=1.0 for match).
        Postgres backend: cosine similarity on embeddings.
        """
        ...

    @abstractmethod
    async def close(self) -> None:
        """Release resources (connection pools, etc.)."""
        ...
