"""
In-memory session registry for web authentication.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set

from travel_agent.web_auth import SessionPrincipal


@dataclass
class SessionRecord:
    user_id: str
    session_id: str
    token_id: str
    issued_at: str
    expires_at: str
    display_name: Optional[str] = None
    email: Optional[str] = None
    active: bool = True


class SessionStore:
    """Track active sessions and revoked tokens within a single process."""

    def __init__(self):
        self._sessions: Dict[str, SessionRecord] = {}
        self._revoked_token_ids: Set[str] = set()

    def register(self, principal: SessionPrincipal) -> SessionRecord:
        existing = self._sessions.get(principal.session_id)
        if existing and existing.token_id != principal.token_id:
            self._revoked_token_ids.add(existing.token_id)
            existing.active = False
        record = SessionRecord(
            user_id=principal.user_id,
            session_id=principal.session_id,
            token_id=principal.token_id,
            issued_at=principal.issued_at.isoformat(),
            expires_at=principal.expires_at.isoformat(),
            display_name=principal.display_name,
            email=principal.email,
            active=True,
        )
        self._sessions[principal.session_id] = record
        return record

    def revoke(self, token_id: str) -> None:
        self._revoked_token_ids.add(token_id)
        for record in self._sessions.values():
            if record.token_id == token_id:
                record.active = False

    def is_revoked(self, token_id: str) -> bool:
        return token_id in self._revoked_token_ids

    def get(self, session_id: str) -> Optional[SessionRecord]:
        return self._sessions.get(session_id)

    def list_user_sessions(self, user_id: str) -> List[SessionRecord]:
        return [record for record in self._sessions.values() if record.user_id == user_id]

    def touch(self, principal: SessionPrincipal) -> SessionRecord:
        record = self._sessions.get(principal.session_id)
        if record is None:
            record = self.register(principal)
        else:
            if record.token_id != principal.token_id:
                self._revoked_token_ids.add(record.token_id)
            record.token_id = principal.token_id
            record.issued_at = principal.issued_at.isoformat()
            record.expires_at = principal.expires_at.isoformat()
            record.display_name = principal.display_name
            record.email = principal.email
            record.active = True
        return record
