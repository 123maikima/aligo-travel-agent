#!/usr/bin/env python
"""
Web API auth/session tests.
"""
from __future__ import annotations

import os
import sys

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from travel_agent.services.session_store import SessionStore
from travel_agent.web_auth import decode_token, issue_token


def test_jwt_roundtrip_and_refresh():
    token_bundle = issue_token(user_id="alice", session_id="sess-1", scopes=["chat"])
    principal = decode_token(token_bundle["access_token"])

    assert principal.user_id == "alice"
    assert principal.session_id == "sess-1"
    assert principal.scopes == ["chat"]


def test_session_store_revocation_and_rotation():
    store = SessionStore()
    first = issue_token(user_id="alice", session_id="sess-1")
    first_principal = first["principal"]
    store.touch(first_principal)

    second = issue_token(user_id="alice", session_id="sess-1")
    second_principal = second["principal"]
    store.touch(second_principal)

    assert store.is_revoked(first_principal.token_id)
    assert not store.is_revoked(second_principal.token_id)
    record = store.get("sess-1")
    assert record is not None
    assert record.token_id == second_principal.token_id
    assert record.active is True
