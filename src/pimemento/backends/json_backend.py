"""JSON file-based memory backend.

Zero heavy dependencies. Storage layout: {memory_dir}/{client_id}/memory.json
Each file is a JSON array of entry dicts.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:
    import fcntl

    _HAS_FCNTL = True
except ImportError:
    _HAS_FCNTL = False

from pimemento.backends.base import MemoryBackend, MemoryEntry
from pimemento.config import PimementoConfig
from pimemento.tools import parse_kv as _parse_kv

logger = logging.getLogger(__name__)


class JsonBackend(MemoryBackend):
    """JSON file-based backend. Zero heavy dependencies."""

    def __init__(self, config: PimementoConfig) -> None:
        # Accept both Path and str to keep direct dataclass construction ergonomic.
        self._dir = Path(config.memory_dir)
        self._max_entries = config.max_entries_per_client
        self._max_content = config.max_content_len
        self._locks_lock = threading.Lock()
        self._client_locks: dict[str, threading.Lock] = {}

    def _get_lock(self, client_id: str) -> threading.Lock:
        """Get or create a per-client lock for thread-safe file I/O."""
        with self._locks_lock:
            if client_id not in self._client_locks:
                self._client_locks[client_id] = threading.Lock()
            return self._client_locks[client_id]

    class _FileLock:
        """Context manager for cross-process file locking via fcntl."""

        def __init__(self, lock_path: Path) -> None:
            self._lock_path = lock_path
            self._fd: int | None = None

        def __enter__(self):
            self._lock_path.parent.mkdir(parents=True, exist_ok=True)
            self._fd = os.open(str(self._lock_path), os.O_CREAT | os.O_RDWR)
            if _HAS_FCNTL:
                fcntl.flock(self._fd, fcntl.LOCK_EX)
            return self

        def __exit__(self, *args):
            if self._fd is not None:
                if _HAS_FCNTL:
                    fcntl.flock(self._fd, fcntl.LOCK_UN)
                os.close(self._fd)
                self._fd = None

    def _file_lock(self, client_id: str) -> _FileLock:
        """Get a cross-process file lock for a client."""
        lock_path = self._dir / client_id / ".lock"
        return self._FileLock(lock_path)

    _SAFE_ID_RE = re.compile(r"^[a-zA-Z0-9_][a-zA-Z0-9._-]{0,99}$")

    def _path(self, client_id: str) -> Path:
        if not self._SAFE_ID_RE.match(client_id):
            raise ValueError(
                f"Invalid client_id: must match [a-zA-Z0-9._-], got '{client_id}'"
            )
        p = (self._dir / client_id / "memory.json").resolve()
        if not p.is_relative_to(self._dir.resolve()):
            raise ValueError(f"Invalid client_id: path escape detected for '{client_id}'")
        return p

    def _recover_corrupt_file(self, path: Path) -> None:
        """Backup a corrupt JSON file and reset it to an empty list."""
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        backup_path = path.with_name(f"{path.name}.corrupt-{ts}.bak")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.replace(backup_path)
            path.write_text("[]", encoding="utf-8")
            logger.warning(
                "Recovered corrupt JSON memory file %s -> %s",
                path,
                backup_path,
            )
        except Exception as recover_exc:
            logger.error(
                "Failed to recover corrupt JSON memory file %s: %s",
                path,
                recover_exc,
            )

    def _load_sync(self, path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
            logger.warning(
                "Invalid JSON memory file format %s: expected list, got %s",
                path,
                type(data).__name__,
            )
            self._recover_corrupt_file(path)
            return []
        except Exception as exc:
            logger.warning("Failed to load JSON memory file %s: %s", path, exc)
            self._recover_corrupt_file(path)
            return []

    def _save_sync(self, path: Path, entries: list[dict[str, Any]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(entries, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _prune_expired(self, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Remove entries past their TTL or expires_at."""
        now = datetime.now(timezone.utc)

        def _to_utc(dt: datetime) -> datetime:
            if dt.tzinfo is None:
                return dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)

        kept: list[dict[str, Any]] = []
        for e in entries:
            # Check expires_at (new format)
            expires_raw = e.get("expires_at")
            if expires_raw:
                try:
                    exp = datetime.fromisoformat(expires_raw.replace("Z", "+00:00"))
                    if now > _to_utc(exp):
                        continue
                except (ValueError, TypeError):
                    pass
            # Check ttl_days (legacy format)
            ttl = e.get("ttl_days")
            if ttl and isinstance(ttl, (int, float)):
                try:
                    created_raw = e.get("created_at") or e.get("date", "")
                    created = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
                    if now - _to_utc(created) > timedelta(days=int(ttl)):
                        continue
                except (ValueError, TypeError):
                    pass
            kept.append(e)
        return kept

    def _sort_key(self, e: dict[str, Any]) -> str:
        return e.get("updated_at") or e.get("created_at") or e.get("date", "")

    async def save(self, entry: MemoryEntry) -> MemoryEntry:
        def _do() -> MemoryEntry:
            lock = self._get_lock(entry.client_id)
            with lock, self._file_lock(entry.client_id):
                path = self._path(entry.client_id)
                raw = self._load_sync(path)
                raw = self._prune_expired(raw)

                # Check for ID-based update (re-save after merge)
                for i, existing in enumerate(raw):
                    if existing.get("id") == entry.id:
                        raw[i] = entry.to_json_dict()
                        self._save_sync(path, raw)
                        return entry

                # Append new entry
                raw.append(entry.to_json_dict())

                # Cap entries (keep most recent); 0 = unlimited
                if self._max_entries and len(raw) > self._max_entries:
                    raw.sort(key=self._sort_key, reverse=True)
                    raw = raw[: self._max_entries]

                self._save_sync(path, raw)
                return entry

        return await asyncio.to_thread(_do)

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
        def _do() -> list[MemoryEntry]:
            path = self._path(client_id)
            raw = self._load_sync(path)
            raw = self._prune_expired(raw)

            if user_id:
                raw = [e for e in raw if e.get("user_id", "_anonymous") == user_id]
            if namespace:
                raw = [e for e in raw if e.get("namespace", "general") == namespace]
            if category:
                raw = [e for e in raw if e.get("category", "") == category]
            if type:
                raw = [e for e in raw if e.get("type", "") == type]

            raw.sort(key=self._sort_key, reverse=True)
            raw = raw[: max(1, min(limit, 100))]
            return [MemoryEntry.from_json_dict(e) for e in raw]

        return await asyncio.to_thread(_do)

    async def delete(
        self,
        client_id: str,
        content_match: str,
        *,
        user_id: str = "",
        namespace: str = "",
        category: str = "",
    ) -> MemoryEntry | None:
        def _do() -> MemoryEntry | None:
            lock = self._get_lock(client_id)
            with lock, self._file_lock(client_id):
                path = self._path(client_id)
                raw = self._load_sync(path)
                if not raw:
                    return None

                cm = content_match.lower()
                raw.sort(key=self._sort_key, reverse=True)

                for i, e in enumerate(raw):
                    if user_id and e.get("user_id", "_anonymous") != user_id:
                        continue
                    if namespace and e.get("namespace", "general") != namespace:
                        continue
                    if category and e.get("category", "") != category:
                        continue
                    if cm in e.get("content", "").lower():
                        removed = raw.pop(i)
                        self._save_sync(path, raw)
                        return MemoryEntry.from_json_dict(removed)
                return None

        return await asyncio.to_thread(_do)

    async def status(
        self,
        client_id: str,
        *,
        user_id: str = "",
        namespace: str = "",
    ) -> dict[str, Any]:
        def _do() -> dict[str, Any]:
            path = self._path(client_id)
            raw = self._load_sync(path)
            raw = self._prune_expired(raw)

            if user_id:
                raw = [e for e in raw if e.get("user_id", "_anonymous") == user_id]
            if namespace:
                raw = [e for e in raw if e.get("namespace", "general") == namespace]

            if not raw:
                return {"count": 0}

            namespaces = sorted({e.get("namespace", "general") for e in raw})
            categories = sorted({e.get("category", "?") for e in raw})
            dates = [
                (e.get("updated_at") or e.get("created_at") or e.get("date", ""))[:10]
                for e in raw
                if e.get("updated_at") or e.get("created_at") or e.get("date")
            ]
            ttl_count = sum(
                1 for e in raw if e.get("expires_at") or e.get("ttl_days")
            )

            return {
                "count": len(raw),
                "namespaces": namespaces,
                "categories": categories,
                "oldest": min(dates) if dates else "?",
                "newest": max(dates) if dates else "?",
                "ttl_count": ttl_count,
            }

        return await asyncio.to_thread(_do)

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
        """Substring fallback search for JSON backend."""

        def _do() -> list[tuple[MemoryEntry, float]]:
            path = self._path(client_id)
            raw = self._load_sync(path)
            raw = self._prune_expired(raw)

            if user_id:
                raw = [e for e in raw if e.get("user_id", "_anonymous") == user_id]
            if namespace:
                raw = [e for e in raw if e.get("namespace", "general") == namespace]

            q = query.lower()
            matches: list[tuple[MemoryEntry, float]] = []
            for e in raw:
                searchable = " ".join([
                    e.get("content", ""),
                    e.get("reason", ""),
                    e.get("category", ""),
                ]).lower()
                if q in searchable:
                    matches.append((MemoryEntry.from_json_dict(e), 1.0))

            # Sort by date (newest first) since all scores are equal
            matches.sort(key=lambda t: t[0].updated_at, reverse=True)
            return matches[:limit]

        return await asyncio.to_thread(_do)

    async def find_duplicates(
        self,
        entry: MemoryEntry,
        threshold: float,
    ) -> list[tuple[MemoryEntry, float]]:
        """Key-based dedup: find entries with overlapping keys."""

        def _do() -> list[tuple[MemoryEntry, float]]:
            new_kv = _parse_kv(entry.content)
            if not new_kv:
                return []

            path = self._path(entry.client_id)
            raw = self._load_sync(path)
            raw = self._prune_expired(raw)

            results: list[tuple[MemoryEntry, float]] = []
            for e in raw:
                if e.get("namespace", "general") != entry.namespace:
                    continue
                if e.get("user_id", "_anonymous") != entry.user_id:
                    continue
                if e.get("category", "") != entry.category:
                    continue
                existing_kv = _parse_kv(e.get("content", ""))
                shared_keys = set(new_kv.keys()) & set(existing_kv.keys())
                if shared_keys:
                    results.append((MemoryEntry.from_json_dict(e), 1.0))
            return results

        return await asyncio.to_thread(_do)

    async def close(self) -> None:
        pass
