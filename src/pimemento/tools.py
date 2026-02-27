"""Core business logic for Pimemento's 5 memory tools.

Backend-agnostic: receives MemoryBackend and optional Embedder as parameters.
Never imports concrete backends or embedders.
"""

from __future__ import annotations

import re
import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from pimemento.backends.base import MemoryBackend, MemoryEntry
from pimemento.config import PimementoConfig
from pimemento.embeddings.base import Embedder

# ── Constants ──

VALID_TYPES = ("exclusion", "decision", "anomaly", "insight", "action")

RECOMMENDED_CATEGORIES = (
    "business_context",
    "project_config",
    "user_preference",
    "domain_context",
    "analysis_context",
    "content_strategy",
)

CATEGORY_ALIASES: dict[str, str] = {}

# Identifiers: alphanumeric/underscore start + dots, underscores, hyphens. 1-100 chars.
# Allows _default, _anonymous, etc. while blocking ../ and path traversal.
_SAFE_ID_RE = re.compile(r"^[a-zA-Z0-9_][a-zA-Z0-9._-]{0,99}$")

# Field length limits
MAX_REASON_LEN = 300
MAX_ID_LEN = 100
MAX_NAMESPACE_LEN = 50
MAX_QUERY_LEN = 500
MAX_METADATA_BYTES = 5000


# ── Rate limiter ──


class RateLimiter:
    """In-memory sliding window rate limiter, per-client.

    Thread-safe. Tracks timestamps of recent calls and rejects when
    the count within the window exceeds the limit.
    """

    def __init__(self, max_calls: int, window_seconds: int) -> None:
        self._max_calls = max_calls
        self._window = window_seconds
        self._calls: dict[str, list[float]] = defaultdict(list)
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return self._max_calls > 0

    def check(self, client_id: str) -> str:
        """Check if a call is allowed. Returns empty string if OK, error message if blocked."""
        if not self.enabled:
            return ""
        now = time.monotonic()
        cutoff = now - self._window
        with self._lock:
            timestamps = self._calls[client_id]
            # Prune old entries
            self._calls[client_id] = [t for t in timestamps if t > cutoff]
            if len(self._calls[client_id]) >= self._max_calls:
                return (
                    f"Error: rate limit exceeded for '{client_id}' "
                    f"({self._max_calls} saves per {self._window}s). "
                    f"Try again shortly."
                )
            self._calls[client_id].append(now)
            return ""


# Global rate limiter instance (initialized lazily from config)
_rate_limiter: RateLimiter | None = None


def get_rate_limiter(config: PimementoConfig) -> RateLimiter:
    """Get or create the global rate limiter from config."""
    global _rate_limiter
    if _rate_limiter is None:
        _rate_limiter = RateLimiter(config.save_rate_limit, config.save_rate_window)
    return _rate_limiter


def reset_rate_limiter() -> None:
    """Reset the global rate limiter (for testing)."""
    global _rate_limiter
    _rate_limiter = None


# ── Shared utilities ──


def validate_identifier(name: str, value: str, allow_default: str = "") -> str:
    """Validate and sanitize an identifier (client_id, user_id, namespace).

    Raises ValueError on invalid input.
    """
    v = (value or allow_default).strip()
    if not v:
        return allow_default
    if not _SAFE_ID_RE.match(v):
        raise ValueError(
            f"Invalid {name}: must start with alphanumeric and contain only "
            f"[a-zA-Z0-9._-] (max 100 chars), got '{v}'"
        )
    return v


def parse_metadata(raw: str) -> tuple[dict[str, Any], str]:
    """Parse and validate a JSON metadata string.

    Returns (parsed_dict, error_message). error_message is empty on success.
    """
    if not raw or not raw.strip():
        return {}, ""
    if len(raw) > MAX_METADATA_BYTES:
        return {}, f"Error: metadata too large ({len(raw)} bytes, max {MAX_METADATA_BYTES})"
    try:
        parsed = __import__("json").loads(raw)
    except (ValueError, TypeError):
        return {}, "Error: invalid metadata JSON"
    if not isinstance(parsed, dict):
        return {}, f"Error: metadata must be a JSON object, got {type(parsed).__name__}"
    return parsed, ""


def parse_kv(content: str) -> dict[str, str]:
    """Parse 'key=value | key=value' into a dict."""
    result: dict[str, str] = {}
    for pair in content.split("|"):
        pair = pair.strip()
        if "=" in pair:
            k, _, v = pair.partition("=")
            result[k.strip().lower()] = v.strip()
    return result


def _metadata_with_kv(
    metadata: dict[str, Any] | None,
    kv_pairs: dict[str, str],
) -> dict[str, Any]:
    """Return metadata enriched with parsed key=value pairs under metadata['kv']."""
    out: dict[str, Any] = dict(metadata or {})
    if not kv_pairs:
        return out

    existing_kv = out.get("kv")
    if not isinstance(existing_kv, dict):
        existing_kv = {}
    out["kv"] = {**existing_kv, **kv_pairs}
    return out


def normalize_category(raw: str, aliases: dict[str, str] | None = None) -> str:
    """Normalize and alias a category name."""
    cat = (raw or "").strip().lower()
    lookup = aliases if aliases is not None else CATEGORY_ALIASES
    return lookup.get(cat, cat)


def _detect_conflicts(entries: list[MemoryEntry]) -> list[str]:
    """Detect key-value conflicts across entries.

    Scans all entries for overlapping keys with different values.
    Returns human-readable conflict annotations (empty list = no conflicts).
    """
    key_records: dict[str, list[tuple[str, datetime, str]]] = {}
    for entry in entries:
        kv = parse_kv(entry.content)
        for k, v in kv.items():
            key_records.setdefault(k, []).append(
                (v, entry.updated_at, entry.user_id or "_anonymous")
            )

    conflicts: list[str] = []
    for key, records in key_records.items():
        unique_vals = {v for v, _, _ in records}
        if len(unique_vals) < 2:
            continue
        records.sort(key=lambda x: x[1], reverse=True)
        latest_val, latest_date, _ = records[0]
        older = [(v, d, u) for v, d, u in records[1:] if v != latest_val]
        if older:
            old_val, old_date, old_user = older[0]
            user_info = f" @{old_user}" if old_user != "_anonymous" else ""
            conflicts.append(
                f"CONFLICT {key}: current={latest_val} ({latest_date:%Y-%m-%d}), "
                f"previous={old_val} ({old_date:%Y-%m-%d}{user_info})"
            )
    return conflicts


# ── Tool implementations ──


async def save_memory(
    backend: MemoryBackend,
    config: PimementoConfig,
    embedder: Embedder | None,
    *,
    category: str,
    type: str,
    content: str,
    reason: str,
    client_id: str = "_default",
    user_id: str = "_anonymous",
    namespace: str = "general",
    source_mcp: str = "",
    ttl_days: int = 0,
    metadata: dict[str, Any] | None = None,
    category_aliases: dict[str, str] | None = None,
) -> str:
    """Persist context. Key dedup + semantic dedup (Postgres)."""
    # ── Validate identifiers ──
    try:
        client_id = validate_identifier("client_id", client_id, allow_default="_default")
        user_id = validate_identifier("user_id", user_id, allow_default="_anonymous")
        namespace = validate_identifier("namespace", namespace, allow_default="general").lower()
    except ValueError as exc:
        return f"Error: {exc}"

    # ── Rate limiting ──
    limiter = get_rate_limiter(config)
    rate_err = limiter.check(client_id)
    if rate_err:
        return rate_err

    t = (type or "").strip().lower()
    if t not in VALID_TYPES:
        return f"Error: type '{type}' invalid. Accepted: {', '.join(VALID_TYPES)}"

    cat = normalize_category(category, category_aliases)
    content = (content or "").strip()
    reason = (reason or "").strip()

    if not cat:
        return f"Error: category required. Recommended: {', '.join(RECOMMENDED_CATEGORIES)}"
    if not content:
        return "Error: content required."
    if not reason:
        return "Error: reason required."

    if len(reason) > MAX_REASON_LEN:
        reason = reason[:MAX_REASON_LEN]

    # Warnings (not rejections)
    warnings = ""
    if cat not in RECOMMENDED_CATEGORIES and not cat.startswith("x_"):
        warnings += f" (category '{cat}' non-standard -- use x_ prefix for custom)"
    if "=" not in content:
        warnings += " (no key=value detected -- recommended format: key=value | key=value)"

    if len(content) > config.max_content_len:
        warnings += f" (truncated from {len(content)} to {config.max_content_len} chars)"
        content = content[: config.max_content_len]

    parsed_kv = parse_kv(content)
    enriched_metadata = _metadata_with_kv(metadata, parsed_kv)

    # ── Build entry ──
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(days=ttl_days) if ttl_days and ttl_days > 0 else None

    entry = MemoryEntry(
        client_id=client_id,
        user_id=user_id,
        namespace=namespace,
        content=content,
        metadata=enriched_metadata,
        category=cat,
        type=t,
        reason=reason,
        created_at=now,
        updated_at=now,
        expires_at=expires_at,
        source_mcp=source_mcp,
    )

    # ── Compute embedding (if available) ──
    if embedder:
        entry.embedding = await embedder.embed(content)

    # ── Dedup: find duplicates ──
    duplicates = await backend.find_duplicates(entry, config.semantic_dedup_threshold)

    if duplicates:
        existing, score = duplicates[0]

        # Merge logic
        new_kv = parsed_kv
        existing_kv = parse_kv(existing.content)

        shared_keys: set[str] = set()
        changed_values: dict[str, tuple[str, str]] = {}
        if new_kv and existing_kv:
            shared_keys = set(new_kv.keys()) & set(existing_kv.keys())
            for k in shared_keys:
                old_v = existing_kv.get(k, "")
                new_v = new_kv.get(k, "")
                if old_v != new_v:
                    changed_values[k] = (old_v, new_v)
            existing_kv.update(new_kv)
            merged_content = " | ".join(f"{k}={v}" for k, v in existing_kv.items())
            if len(merged_content) > config.max_content_len:
                merged_content = merged_content[: config.max_content_len]
            existing.content = merged_content
        elif new_kv and not existing_kv:
            # New has kv, existing doesn't: replace
            existing.content = entry.content
        else:
            # Semantic match or no kv on either side: newer content wins
            existing.content = entry.content

        existing.category = cat
        existing.type = t
        existing.reason = reason
        existing.updated_at = now
        existing.expires_at = expires_at
        old_meta = dict(existing.metadata or {})
        existing.metadata = {**old_meta, **entry.metadata}
        old_meta_kv = old_meta.get("kv")
        new_meta_kv = entry.metadata.get("kv")
        if isinstance(old_meta_kv, dict) or isinstance(new_meta_kv, dict):
            existing.metadata["kv"] = {
                **(old_meta_kv if isinstance(old_meta_kv, dict) else {}),
                **(new_meta_kv if isinstance(new_meta_kv, dict) else {}),
            }
        existing.merged_from = list(
            dict.fromkeys([*(existing.merged_from or []), entry.id])
        )
        if source_mcp:
            existing.source_mcp = source_mcp
        if entry.embedding:
            existing.embedding = entry.embedding

        await backend.save(existing)

        if shared_keys:
            merge_info = f"keys: {', '.join(sorted(shared_keys))}"
            if changed_values:
                changes = ", ".join(
                    f"{k} {old}->{new}" for k, (old, new) in changed_values.items()
                )
                prev_user = (
                    f" @{existing.user_id}"
                    if existing.user_id and existing.user_id != "_anonymous"
                    else ""
                )
                prev_date = existing.updated_at.strftime("%Y-%m-%d")
                merge_info += f" | changed: {changes} (was{prev_user} {prev_date})"
        else:
            merge_info = f"semantic: {score:.2f}"
        return f"Updated ({merge_info}).{warnings}\n{cat} | {t}"

    # ── No duplicate: save new ──
    await backend.save(entry)
    return f"Saved.{warnings}\n{cat} | {t}"


async def get_memory(
    backend: MemoryBackend,
    *,
    client_id: str = "_default",
    user_id: str = "",
    namespace: str = "",
    category: str = "",
    type: str = "",
    limit: int = 20,
    category_aliases: dict[str, str] | None = None,
) -> str:
    """Read memory with filters."""
    try:
        client_id = validate_identifier("client_id", client_id, allow_default="_default")
        user_id = validate_identifier("user_id", user_id, allow_default="")
        namespace = validate_identifier("namespace", namespace, allow_default="").lower()
    except ValueError as exc:
        return f"Error: {exc}"
    cat = normalize_category(category, category_aliases)
    tp = (type or "").strip().lower()

    entries = await backend.get(
        client_id,
        user_id=user_id,
        namespace=namespace,
        category=cat,
        type=tp,
        limit=max(1, min(int(limit), 100)),
    )

    if not entries:
        return f"No memory for '{client_id}'."

    lines = [f"Memory '{client_id}' ({len(entries)}):"]
    for e in entries:
        d = e.updated_at.strftime("%Y-%m-%d")
        ns_label = e.namespace or "general"
        ttl_mark = ""
        if e.expires_at:
            remaining = (e.expires_at - datetime.now(timezone.utc)).days
            ttl_mark = f" [{remaining}d]"
        user_mark = f" @{e.user_id}" if e.user_id and e.user_id != "_anonymous" else ""
        lines.append(
            f"{d} {e.type.upper()} "
            f"| {ns_label}/{e.category} "
            f"| {e.content}{ttl_mark}{user_mark}"
        )

    conflicts = _detect_conflicts(entries)
    if conflicts:
        lines.append("---")
        lines.extend(conflicts)

    return "\n".join(lines)


async def delete_memory(
    backend: MemoryBackend,
    *,
    content_match: str,
    client_id: str = "_default",
    user_id: str = "",
    namespace: str = "",
    category: str = "",
    category_aliases: dict[str, str] | None = None,
) -> str:
    """Delete most recent entry matching content_match."""
    try:
        client_id = validate_identifier("client_id", client_id, allow_default="_default")
    except ValueError as exc:
        return f"Error: {exc}"
    cm = (content_match or "").strip()
    if not cm:
        return "Error: content_match required."

    cat = normalize_category(category, category_aliases)
    removed = await backend.delete(
        client_id,
        cm,
        user_id=(user_id or "").strip(),
        namespace=(namespace or "").strip().lower(),
        category=cat,
    )

    if not removed:
        return f"No entry containing '{content_match}' found."

    return f"Deleted: {removed.content}"


async def memory_status(
    backend: MemoryBackend,
    *,
    client_id: str = "_default",
    user_id: str = "",
    namespace: str = "",
) -> str:
    """Lightweight metadata (~20 tokens)."""
    try:
        client_id = validate_identifier("client_id", client_id, allow_default="_default")
        user_id = validate_identifier("user_id", user_id, allow_default="")
        namespace = validate_identifier("namespace", namespace, allow_default="").lower()
    except ValueError as exc:
        return f"Error: {exc}"

    info = await backend.status(client_id, user_id=user_id, namespace=namespace)

    if info.get("count", 0) == 0:
        return f"No memory for '{client_id}'."

    ttl_str = f" | {info['ttl_count']} with TTL" if info.get("ttl_count") else ""
    return (
        f"'{client_id}': {info['count']} entries "
        f"| ns: {', '.join(info.get('namespaces', []))} "
        f"| cat: {', '.join(info.get('categories', []))} "
        f"| {info.get('oldest', '?')} -> {info.get('newest', '?')}"
        f"{ttl_str}"
    )


async def search_memory(
    backend: MemoryBackend,
    embedder: Embedder | None,
    *,
    query: str,
    client_id: str = "_default",
    user_id: str = "",
    namespace: str = "",
    limit: int = 10,
) -> str:
    """Semantic search (pgvector cosine) or substring fallback (JSON)."""
    try:
        client_id = validate_identifier("client_id", client_id, allow_default="_default")
    except ValueError as exc:
        return f"Error: {exc}"
    q = (query or "").strip()
    if not q:
        return "Error: query required."
    if len(q) > MAX_QUERY_LEN:
        q = q[:MAX_QUERY_LEN]

    # Compute query embedding if embedder available
    query_embedding = None
    if embedder:
        query_embedding = await embedder.embed(q)

    results = await backend.search(
        q,
        client_id,
        user_id=(user_id or "").strip(),
        namespace=(namespace or "").strip().lower(),
        limit=max(1, min(int(limit), 50)),
        query_embedding=query_embedding,
    )

    if not results:
        return f"No results for '{query}'."

    lines = [f"Search '{query}' ({len(results)} results):"]
    all_entries = []
    for entry, score in results:
        all_entries.append(entry)
        d = entry.updated_at.strftime("%Y-%m-%d")
        score_str = f"{score:.2f}" if score < 1.0 else "match"
        user_mark = f" @{entry.user_id}" if entry.user_id and entry.user_id != "_anonymous" else ""
        lines.append(
            f"[{score_str}] {d} {entry.type.upper()} "
            f"| {entry.namespace}/{entry.category} "
            f"| {entry.content}{user_mark}"
        )

    conflicts = _detect_conflicts(all_entries)
    if conflicts:
        lines.append("---")
        lines.extend(conflicts)

    return "\n".join(lines)
