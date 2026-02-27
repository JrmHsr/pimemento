"""Tests for configuration validation and normalization."""

from __future__ import annotations

from pathlib import Path

import pytest

from pimemento.config import PimementoConfig


def test_memory_dir_is_normalized_to_path(tmp_path):
    cfg = PimementoConfig(memory_dir=str(tmp_path))
    assert isinstance(cfg.memory_dir, Path)
    assert cfg.memory_dir == tmp_path


def test_postgres_requires_database_url():
    with pytest.raises(ValueError, match="DATABASE_URL is required"):
        PimementoConfig(backend="postgres", database_url="")


def test_from_env_invalid_backend_raises(monkeypatch):
    monkeypatch.setenv("MEMORY_BACKEND", "postgress")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ValueError, match="Invalid MEMORY_BACKEND"):
        PimementoConfig.from_env()


def test_from_env_invalid_embedding_provider_raises(monkeypatch):
    monkeypatch.setenv("MEMORY_BACKEND", "json")
    monkeypatch.setenv("EMBEDDING_PROVIDER", "foobar")
    with pytest.raises(ValueError, match="Invalid EMBEDDING_PROVIDER"):
        PimementoConfig.from_env()
