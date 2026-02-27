"""Security tests for Pimemento hardening."""

from __future__ import annotations

import pytest

from pimemento.backends.json_backend import JsonBackend
from pimemento.config import PimementoConfig
from pimemento.tools import (
    RateLimiter,
    save_memory,
    get_memory,
    delete_memory,
    search_memory,
    parse_metadata,
    validate_identifier,
    reset_rate_limiter,
)


# ── Path traversal ──


class TestPathTraversal:
    """Ensure client_id cannot escape the memory directory."""

    def test_dotdot_in_client_id_raises(self, config):
        backend = JsonBackend(config)
        with pytest.raises(ValueError, match="Invalid client_id"):
            backend._path("../../etc")

    def test_slash_in_client_id_raises(self, config):
        backend = JsonBackend(config)
        with pytest.raises(ValueError, match="Invalid client_id"):
            backend._path("foo/../bar")

    @pytest.mark.asyncio
    async def test_save_rejects_traversal_client_id(self, config, json_backend):
        result = await save_memory(
            json_backend,
            config,
            None,
            category="business_context",
            type="insight",
            content="test=value",
            reason="test",
            client_id="../../etc",
        )
        assert "Error" in result

    @pytest.mark.asyncio
    async def test_save_rejects_special_chars_client_id(self, config, json_backend):
        result = await save_memory(
            json_backend,
            config,
            None,
            category="business_context",
            type="insight",
            content="test=value",
            reason="test",
            client_id="foo bar",
        )
        assert "Error" in result

    @pytest.mark.asyncio
    async def test_valid_client_id_accepted(self, config, json_backend):
        result = await save_memory(
            json_backend,
            config,
            None,
            category="business_context",
            type="insight",
            content="test=value",
            reason="test",
            client_id="my_client-123.test",
        )
        assert "Saved" in result


# ── Identifier validation ──


class TestIdentifierValidation:

    def test_valid_identifiers(self):
        assert validate_identifier("test", "abc123") == "abc123"
        assert validate_identifier("test", "my_client-123.com") == "my_client-123.com"
        assert validate_identifier("test", "A") == "A"

    def test_empty_with_default(self):
        assert validate_identifier("test", "", allow_default="_default") == "_default"
        assert validate_identifier("test", None, allow_default="_default") == "_default"

    def test_rejects_path_traversal(self):
        with pytest.raises(ValueError, match="Invalid"):
            validate_identifier("test", "../../etc")

    def test_rejects_spaces(self):
        with pytest.raises(ValueError, match="Invalid"):
            validate_identifier("test", "has spaces")

    def test_rejects_too_long(self):
        with pytest.raises(ValueError, match="Invalid"):
            validate_identifier("test", "a" * 101)

    def test_rejects_starts_with_dot(self):
        with pytest.raises(ValueError, match="Invalid"):
            validate_identifier("test", ".hidden")

    def test_rejects_starts_with_dash(self):
        with pytest.raises(ValueError, match="Invalid"):
            validate_identifier("test", "-flag")

    def test_allows_underscore_prefix(self):
        assert validate_identifier("test", "_default") == "_default"
        assert validate_identifier("test", "_anonymous") == "_anonymous"


# ── Metadata validation ──


class TestMetadataValidation:

    def test_empty_string(self):
        meta, err = parse_metadata("")
        assert meta == {}
        assert err == ""

    def test_valid_json_object(self):
        meta, err = parse_metadata('{"key": "value"}')
        assert meta == {"key": "value"}
        assert err == ""

    def test_rejects_json_array(self):
        meta, err = parse_metadata("[1, 2, 3]")
        assert meta == {}
        assert "JSON object" in err

    def test_rejects_json_string(self):
        meta, err = parse_metadata('"just a string"')
        assert meta == {}
        assert "JSON object" in err

    def test_rejects_invalid_json(self):
        meta, err = parse_metadata("{invalid json}")
        assert meta == {}
        assert "invalid" in err

    def test_rejects_oversized_metadata(self):
        large = '{"k": "' + "x" * 6000 + '"}'
        meta, err = parse_metadata(large)
        assert meta == {}
        assert "too large" in err


# ── Input length limits ──


class TestInputLimits:

    @pytest.mark.asyncio
    async def test_reason_truncated(self, config, json_backend):
        long_reason = "x" * 500
        result = await save_memory(
            json_backend,
            config,
            None,
            category="business_context",
            type="insight",
            content="key=value",
            reason=long_reason,
        )
        assert "Saved" in result

    @pytest.mark.asyncio
    async def test_query_truncated(self, config, json_backend):
        long_query = "a" * 1000
        result = await search_memory(
            json_backend,
            None,
            query=long_query,
        )
        # Should not error, just truncate and search
        assert "No results" in result or "Error" not in result


# ── Config repr masking ──


class TestConfigRepr:

    def test_repr_masks_database_url(self):
        cfg = PimementoConfig(
            backend="json",
            database_url="postgresql://user:secret@host/db",
        )
        r = repr(cfg)
        assert "secret" not in r
        assert "***" in r

    def test_repr_masks_openai_key(self):
        cfg = PimementoConfig(
            openai_api_key="sk-1234567890abcdef",
        )
        r = repr(cfg)
        assert "sk-1234567890" not in r
        assert "***" in r

    def test_repr_masks_auth_token(self):
        cfg = PimementoConfig(
            auth_token="my-secret-token",
        )
        r = repr(cfg)
        assert "my-secret-token" not in r
        assert "***" in r

    def test_repr_no_mask_when_empty(self):
        cfg = PimementoConfig()
        r = repr(cfg)
        assert "***" not in r


# ── Default binding ──


class TestDefaultBinding:

    def test_default_host_is_localhost(self):
        cfg = PimementoConfig()
        assert cfg.memory_host == "127.0.0.1"


# ── File locking ──


class TestFileLocking:

    def test_file_lock_creates_lock_file(self, config):
        backend = JsonBackend(config)
        client_id = "testclient"
        lock = backend._file_lock(client_id)
        with lock:
            lock_path = config.memory_dir / client_id / ".lock"
            assert lock_path.exists()


# ── Rate limiting ──


class TestRateLimiter:

    def test_allows_under_limit(self):
        limiter = RateLimiter(max_calls=5, window_seconds=60)
        for _ in range(5):
            assert limiter.check("client_a") == ""

    def test_blocks_over_limit(self):
        limiter = RateLimiter(max_calls=3, window_seconds=60)
        for _ in range(3):
            assert limiter.check("client_a") == ""
        err = limiter.check("client_a")
        assert "rate limit exceeded" in err
        assert "client_a" in err

    def test_per_client_isolation(self):
        limiter = RateLimiter(max_calls=2, window_seconds=60)
        assert limiter.check("client_a") == ""
        assert limiter.check("client_a") == ""
        assert limiter.check("client_a") != ""  # blocked
        assert limiter.check("client_b") == ""  # different client, still OK

    def test_disabled_when_zero(self):
        limiter = RateLimiter(max_calls=0, window_seconds=60)
        assert not limiter.enabled
        for _ in range(100):
            assert limiter.check("client_a") == ""

    @pytest.mark.asyncio
    async def test_save_memory_rate_limited(self, tmp_path):
        reset_rate_limiter()
        cfg = PimementoConfig(
            memory_dir=tmp_path,
            save_rate_limit=2,
            save_rate_window=60,
        )
        backend = JsonBackend(cfg)

        for i in range(2):
            result = await save_memory(
                backend, cfg, None,
                category="business_context", type="insight",
                content=f"key{i}=val{i}", reason="test",
            )
            assert "Saved" in result or "Updated" in result

        result = await save_memory(
            backend, cfg, None,
            category="business_context", type="insight",
            content="key3=val3", reason="test",
        )
        assert "rate limit exceeded" in result

        # Cleanup global state
        reset_rate_limiter()

    @pytest.mark.asyncio
    async def test_rate_limit_disabled(self, tmp_path):
        reset_rate_limiter()
        cfg = PimementoConfig(
            memory_dir=tmp_path,
            save_rate_limit=0,
        )
        backend = JsonBackend(cfg)

        for i in range(10):
            result = await save_memory(
                backend, cfg, None,
                category="business_context", type="insight",
                content=f"key{i}=val{i}", reason="test",
            )
            assert "Error" not in result or "rate limit" not in result

        reset_rate_limiter()
