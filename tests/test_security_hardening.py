#!/usr/bin/env python
"""Security hardening unit tests."""
from __future__ import annotations

import pytest
import os
import sys

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from travel_agent import config
from travel_agent.observability import mask_pii
from travel_agent.web_api import ChatRequest, InMemoryRateLimiter, _model_validate


def test_validate_secrets_blocks_short_values(monkeypatch):
    monkeypatch.setitem(config.API_CONFIG, "jwt_secret", "short")
    monkeypatch.setitem(config.LLM_CONFIG, "api_key", "also-short")
    monkeypatch.setitem(config.POSTGRES_CONFIG, "enabled", False)
    monkeypatch.delenv("ALLOW_INSECURE_STARTUP", raising=False)

    with pytest.raises(RuntimeError) as exc_info:
        config.validate_secrets()

    message = str(exc_info.value)
    assert "API_JWT_SECRET" in message
    assert "LLM_API_KEY" in message
    assert "short" not in message


def test_validate_secrets_allows_explicit_insecure_startup(monkeypatch):
    monkeypatch.setitem(config.API_CONFIG, "jwt_secret", "short")
    monkeypatch.setitem(config.LLM_CONFIG, "api_key", "also-short")
    monkeypatch.setitem(config.POSTGRES_CONFIG, "enabled", False)
    monkeypatch.setenv("ALLOW_INSECURE_STARTUP", "true")

    config.validate_secrets()


def test_chat_request_rejects_overlong_message():
    payload = {"message": "x" * (config.MAX_MESSAGE_LENGTH + 1)}

    with pytest.raises(Exception):
        _model_validate(ChatRequest, payload)


def test_in_memory_rate_limiter_returns_retry_after():
    limiter = InMemoryRateLimiter()

    assert limiter.check("chat", "user-1", "2/minute") == (True, 0)
    assert limiter.check("chat", "user-1", "2/minute") == (True, 0)
    allowed, retry_after = limiter.check("chat", "user-1", "2/minute")

    assert allowed is False
    assert retry_after > 0


def test_observability_masks_query_and_error_tokens():
    payload = {
        "query": "我明天从北京去上海出差，帮我规划行程并记住手机号 13800000000",
        "nested": {
            "error": "request failed with key sk-abcdefghijklmnopqrstuvwxyz123456"
        },
    }

    masked = mask_pii(payload)

    assert masked["query"] != payload["query"]
    assert "sha256=" in masked["query"]
    assert "sk-abcdefghijklmnopqrstuvwxyz123456" not in masked["nested"]["error"]
    assert "***" in masked["nested"]["error"]
