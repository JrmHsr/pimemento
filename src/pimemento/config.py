"""Pimemento configuration.

Loads settings from environment variables (with .env support via python-dotenv).
All config is immutable after construction.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv


def _safe_int(env_var: str, default: int) -> int:
    """Read an int from env, falling back to default on parse error."""
    try:
        return int(os.getenv(env_var, str(default)))
    except (ValueError, TypeError):
        return default


def _safe_float(env_var: str, default: float) -> float:
    """Read a float from env, falling back to default on parse error."""
    try:
        return float(os.getenv(env_var, str(default)))
    except (ValueError, TypeError):
        return default


@dataclass(frozen=True)
class PimementoConfig:
    """Immutable configuration loaded from environment variables."""

    # Backend
    backend: Literal["json", "postgres"] = "json"

    # JSON backend
    memory_dir: Path = field(default_factory=lambda: Path("./memory_data"))
    max_entries_per_client: int = 0  # 0 = unlimited
    max_content_len: int = 500

    # Postgres backend
    database_url: str = ""

    # Embeddings
    embedding_provider: Literal["local", "openai", "none"] = "none"
    embedding_model: str = ""
    embedding_dimensions: int = 384
    openai_api_key: str = ""
    semantic_dedup_threshold: float = 0.85

    # Server
    memory_host: str = "127.0.0.1"
    memory_port: int = 8770

    # Authentication (HTTP transport only)
    auth_token: str = ""

    # Rate limiting (0 = disabled)
    save_rate_limit: int = 30  # max save_memory calls per client per minute
    save_rate_window: int = 60  # window in seconds

    def __post_init__(self) -> None:
        # Normalize string-like fields and coerce path-like inputs.
        backend = (self.backend or "json").lower()
        provider = (self.embedding_provider or "none").lower()
        object.__setattr__(self, "backend", backend)
        object.__setattr__(self, "embedding_provider", provider)
        object.__setattr__(self, "memory_dir", Path(self.memory_dir))

        if backend not in {"json", "postgres"}:
            raise ValueError(
                f"Invalid MEMORY_BACKEND='{self.backend}'. Expected: json | postgres"
            )
        if provider not in {"local", "openai", "none"}:
            raise ValueError(
                f"Invalid EMBEDDING_PROVIDER='{self.embedding_provider}'. "
                "Expected: local | openai | none"
            )
        if self.max_entries_per_client < 0:
            raise ValueError("MAX_ENTRIES_PER_CLIENT must be >= 0 (0 = unlimited)")
        if self.max_content_len < 1:
            raise ValueError("MAX_CONTENT_LEN must be >= 1")
        if not (0.0 <= self.semantic_dedup_threshold <= 1.0):
            raise ValueError("SEMANTIC_DEDUP_THRESHOLD must be between 0 and 1")
        if not (1 <= self.memory_port <= 65535):
            raise ValueError("MEMORY_PORT must be between 1 and 65535")
        if backend == "postgres" and not self.database_url:
            raise ValueError("DATABASE_URL is required when MEMORY_BACKEND=postgres")
        if self.save_rate_limit < 0:
            raise ValueError("SAVE_RATE_LIMIT must be >= 0 (0 = disabled)")
        if self.save_rate_window < 1:
            raise ValueError("SAVE_RATE_WINDOW must be >= 1")

    @classmethod
    def from_env(cls, dotenv_path: str | Path | None = None) -> PimementoConfig:
        """Load config from environment, optionally reading a .env file first."""
        if dotenv_path:
            load_dotenv(dotenv_path)
        else:
            load_dotenv()

        backend = os.getenv("MEMORY_BACKEND", "json").lower()

        # Default embedding provider depends on backend
        default_embedding = "local" if backend == "postgres" else "none"
        embedding_provider = os.getenv("EMBEDDING_PROVIDER", default_embedding).lower()

        # Default model/dimensions depend on provider
        if embedding_provider == "local":
            default_model = "all-MiniLM-L6-v2"
            default_dim = 384
        elif embedding_provider == "openai":
            default_model = "text-embedding-3-small"
            default_dim = 1536
        else:
            default_model = ""
            default_dim = 384

        return cls(
            backend=backend,
            memory_dir=Path(os.getenv("MEMORY_DIR", "./memory_data")),
            max_entries_per_client=_safe_int("MAX_ENTRIES_PER_CLIENT", 0),
            max_content_len=_safe_int("MAX_CONTENT_LEN", 500),
            database_url=os.getenv("DATABASE_URL", ""),
            embedding_provider=embedding_provider,
            embedding_model=os.getenv("EMBEDDING_MODEL", default_model),
            embedding_dimensions=_safe_int("EMBEDDING_DIMENSIONS", default_dim),
            openai_api_key=os.getenv("OPENAI_API_KEY", ""),
            semantic_dedup_threshold=_safe_float("SEMANTIC_DEDUP_THRESHOLD", 0.85),
            memory_host=os.getenv("MEMORY_HOST", "127.0.0.1"),
            memory_port=_safe_int("MEMORY_PORT", 8770),
            auth_token=os.getenv("MEMORY_AUTH_TOKEN", ""),
            save_rate_limit=_safe_int("SAVE_RATE_LIMIT", 30),
            save_rate_window=_safe_int("SAVE_RATE_WINDOW", 60),
        )

    _SENSITIVE_FIELDS = frozenset({"database_url", "openai_api_key", "auth_token"})

    def __repr__(self) -> str:
        fields = []
        for f in self.__dataclass_fields__:
            val = getattr(self, f)
            if f in self._SENSITIVE_FIELDS and val:
                fields.append(f"{f}='***'")
            else:
                fields.append(f"{f}={val!r}")
        return f"PimementoConfig({', '.join(fields)})"

    @property
    def embeddings_enabled(self) -> bool:
        """True if an embedding provider is configured."""
        return self.embedding_provider != "none"
