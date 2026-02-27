"""Shared test fixtures."""

from __future__ import annotations

import os

import pytest

from pimemento.backends.json_backend import JsonBackend
from pimemento.config import PimementoConfig
from pimemento.tools import reset_rate_limiter

DATABASE_URL = os.getenv("DATABASE_URL", "")


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """Reset the global rate limiter before each test."""
    reset_rate_limiter()
    yield
    reset_rate_limiter()


@pytest.fixture
def config(tmp_path):
    """Config with a temp directory for JSON storage."""
    return PimementoConfig(memory_dir=tmp_path)


@pytest.fixture
def json_backend(config):
    """Fresh JsonBackend using a temp directory."""
    return JsonBackend(config)


@pytest.fixture(params=["json", "postgres"])
async def backend(request, tmp_path):
    """Parametrized fixture: yields both JSON and Postgres backends.

    JSON is always available. Postgres is skipped when DATABASE_URL is not set.
    Contract tests use client_id prefix 'contract_' for isolation.
    """
    if request.param == "json":
        cfg = PimementoConfig(memory_dir=tmp_path)
        yield JsonBackend(cfg)
    else:
        if not DATABASE_URL:
            pytest.skip("DATABASE_URL not set — skipping Postgres")
        from pimemento.backends.postgres_backend import PostgresBackend

        cfg = PimementoConfig(backend="postgres", database_url=DATABASE_URL)
        pg = PostgresBackend(cfg)
        await pg.initialize()
        async with pg._pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM memories WHERE client_id LIKE 'contract_%'"
            )
        yield pg
        await pg.close()
