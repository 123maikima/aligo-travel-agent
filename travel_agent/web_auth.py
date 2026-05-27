"""
JWT utilities for the Web API.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional
import secrets
import uuid

import jwt

from travel_agent.config import API_CONFIG


class AuthError(Exception):
    """Raised when authentication or token validation fails."""


@dataclass
class SessionPrincipal:
    user_id: str
    session_id: str
    token_id: str
    issued_at: datetime
    expires_at: datetime
    scopes: List[str]
    display_name: Optional[str] = None
    email: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["issued_at"] = self.issued_at.isoformat()
        payload["expires_at"] = self.expires_at.isoformat()
        return payload


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_scopes(scopes: Optional[Iterable[str]]) -> List[str]:
    if not scopes:
        return []
    result: List[str] = []
    for scope in scopes:
        scope_str = str(scope).strip()
        if scope_str and scope_str not in result:
            result.append(scope_str)
    return result


def issue_token(
    user_id: str,
    session_id: Optional[str] = None,
    ttl_minutes: Optional[int] = None,
    scopes: Optional[Iterable[str]] = None,
    display_name: Optional[str] = None,
    email: Optional[str] = None,
) -> Dict[str, Any]:
    """Issue a signed JWT access token."""
    now = _now_utc()
    ttl_minutes = ttl_minutes or int(API_CONFIG.get("access_token_ttl_minutes", 720))
    expires_at = now + timedelta(minutes=ttl_minutes)
    session_id = session_id or secrets.token_urlsafe(12)
    token_id = uuid.uuid4().hex
    normalized_scopes = _normalize_scopes(scopes)

    claims = {
        "iss": API_CONFIG.get("issuer", "travel-agent"),
        "sub": user_id,
        "uid": user_id,
        "sid": session_id,
        "jti": token_id,
        "iat": int(now.timestamp()),
        "nbf": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
        "scope": " ".join(normalized_scopes),
    }
    if display_name:
        claims["display_name"] = display_name
    if email:
        claims["email"] = email

    token = jwt.encode(
        claims,
        API_CONFIG.get("jwt_secret", "replace-me"),
        algorithm=API_CONFIG.get("jwt_algorithm", "HS256"),
    )

    principal = SessionPrincipal(
        user_id=user_id,
        session_id=session_id,
        token_id=token_id,
        issued_at=now,
        expires_at=expires_at,
        scopes=normalized_scopes,
        display_name=display_name,
        email=email,
    )

    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_in": ttl_minutes * 60,
        "principal": principal,
    }


def decode_token(token: str) -> SessionPrincipal:
    """Validate a token and return the authenticated principal."""
    try:
        claims = jwt.decode(
            token,
            API_CONFIG.get("jwt_secret", "replace-me"),
            algorithms=[API_CONFIG.get("jwt_algorithm", "HS256")],
            issuer=API_CONFIG.get("issuer", "travel-agent"),
            options={"require": ["exp", "iat", "sub", "sid", "jti"]},
        )
    except Exception as exc:  # pragma: no cover - depends on runtime token state
        raise AuthError(str(exc)) from exc

    scopes = str(claims.get("scope", "")).split()
    issued_at = datetime.fromtimestamp(int(claims["iat"]), tz=timezone.utc)
    expires_at = datetime.fromtimestamp(int(claims["exp"]), tz=timezone.utc)
    return SessionPrincipal(
        user_id=str(claims.get("uid") or claims.get("sub")),
        session_id=str(claims.get("sid")),
        token_id=str(claims.get("jti")),
        issued_at=issued_at,
        expires_at=expires_at,
        scopes=[scope for scope in scopes if scope],
        display_name=claims.get("display_name"),
        email=claims.get("email"),
    )


def extract_bearer_token(authorization: Optional[str]) -> str:
    """Extract Bearer token from Authorization header."""
    if not authorization:
        raise AuthError("Authorization header is required")
    prefix = "bearer "
    if not authorization.lower().startswith(prefix):
        raise AuthError("Authorization header must use Bearer scheme")
    token = authorization[len(prefix):].strip()
    if not token:
        raise AuthError("Bearer token is empty")
    return token

