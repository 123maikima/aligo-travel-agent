"""
Web API entrypoint for the travel agent.

Provides the full request chain:
- JWT auth
- session management
- synchronous chat endpoint
- SSE streaming chat endpoint
- health check
"""
from __future__ import annotations

import json
import logging
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Type, TypeVar

from pydantic import BaseModel, Field
from rich.console import Console
from sse_starlette.sse import EventSourceResponse
from starlette.middleware.cors import CORSMiddleware

try:
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse
    FASTAPI_AVAILABLE = True
except ImportError:
    from starlette.applications import Starlette as FastAPI
    from starlette.responses import JSONResponse
    FASTAPI_AVAILABLE = False

from starlette.requests import Request

from travel_agent.config import (
    API_CONFIG,
    LLM_CONFIG,
    MAX_MESSAGE_LENGTH,
    RATE_LIMIT_CONFIG,
    REDIS_CONFIG,
    RESILIENCE_CONFIG,
    SYSTEM_CONFIG,
    validate_secrets,
)
from travel_agent.config_agentscope import init_agentscope
from travel_agent.context.redis_cache import RedisCache
from travel_agent.llm import create_model_factory
from travel_agent.services.chat_pipeline import ChatPipeline
from travel_agent.services.session_store import SessionStore
from travel_agent.utils.circuit_breaker import CircuitBreaker, CircuitOpenError
from travel_agent.utils.llm_resilience import run_health_check as check_llm_health
from travel_agent.web_auth import (
    AuthError,
    SessionPrincipal,
    decode_token,
    extract_bearer_token,
    issue_token,
)

logger = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)


class AuthTokenRequest(BaseModel):
    user_id: str = Field(..., min_length=1)
    session_id: Optional[str] = None
    display_name: Optional[str] = None
    email: Optional[str] = None
    scopes: list[str] = Field(default_factory=lambda: ["chat"])
    ttl_minutes: Optional[int] = None


class AuthTokenResponse(BaseModel):
    access_token: str
    token_type: str
    expires_in: int
    principal: Dict[str, Any]


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=MAX_MESSAGE_LENGTH)
    user_id: Optional[str] = None
    session_id: Optional[str] = None
    stream: bool = True


class SessionRotateRequest(BaseModel):
    keep_user_id: bool = True


class ApiErrorResponse(BaseModel):
    error: str
    detail: Optional[str] = None


def _model_validate(model_cls: Type[T], payload: Dict[str, Any]) -> T:
    if hasattr(model_cls, "model_validate"):
        return model_cls.model_validate(payload)  # type: ignore[attr-defined]
    return model_cls.parse_obj(payload)  # pragma: no cover


def _json_response(data: Any, status_code: int = 200):
    return JSONResponse(data, status_code=status_code)


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",", 1)[0].strip()
    return request.client.host if request.client else "unknown"


def _parse_rate_limit(value: str) -> tuple[int, int]:
    amount, _, period = value.partition("/")
    count = int(amount.strip())
    normalized = period.strip().lower()
    windows = {
        "second": 1,
        "sec": 1,
        "s": 1,
        "minute": 60,
        "min": 60,
        "m": 60,
        "hour": 3600,
        "h": 3600,
    }
    if normalized not in windows:
        raise ValueError(f"Unsupported rate limit period: {period}")
    return count, windows[normalized]


class InMemoryRateLimiter:
    """Small fixed-window limiter used when no external limiter is configured."""

    def __init__(self, enabled: bool = True):
        self.enabled = enabled
        self._buckets: Dict[str, tuple[float, int]] = {}

    def check(self, namespace: str, key: str, limit: str) -> tuple[bool, int]:
        if not self.enabled:
            return True, 0
        count, window_seconds = _parse_rate_limit(limit)
        now = time.monotonic()
        bucket_key = f"{namespace}:{key}"
        window_started, used = self._buckets.get(bucket_key, (now, 0))
        elapsed = now - window_started
        if elapsed >= window_seconds:
            self._buckets[bucket_key] = (now, 1)
            return True, 0
        if used >= count:
            return False, max(1, int(window_seconds - elapsed))
        self._buckets[bucket_key] = (window_started, used + 1)
        return True, 0


def _rate_limit_response(retry_after: int):
    return JSONResponse(
        {"error": "Too Many Requests", "detail": "Rate limit exceeded"},
        status_code=429,
        headers={"Retry-After": str(retry_after)},
    )


def _create_chat_pipeline() -> ChatPipeline:
    init_agentscope()
    timeout_sec = SYSTEM_CONFIG.get("timeout", 60)
    model_factory = create_model_factory(timeout=float(timeout_sec))
    model = model_factory(tier="default")
    redis_cache = RedisCache(**REDIS_CONFIG)
    circuit_breaker = CircuitBreaker(
        failure_threshold=RESILIENCE_CONFIG.get("circuit_failure_threshold", 5),
        recovery_timeout_sec=RESILIENCE_CONFIG.get("circuit_recovery_timeout_sec", 60.0),
        half_open_successes=RESILIENCE_CONFIG.get("circuit_half_open_successes", 2),
    )
    return ChatPipeline(
        model=model,
        redis_cache=redis_cache,
        circuit_breaker=circuit_breaker,
        model_factory=model_factory,
    )


def create_app():
    console = Console()
    session_store = SessionStore()

    @asynccontextmanager
    async def lifespan(app):
        validate_secrets()
        app.state.chat_pipeline = _create_chat_pipeline()
        app.state.console = console
        app.state.session_store = session_store
        app.state.api_config = API_CONFIG
        app.state.rate_limiter = InMemoryRateLimiter(enabled=bool(RATE_LIMIT_CONFIG.get("enabled", True)))
        logger.info("Travel agent web API started")
        yield

    async def _get_payload(request: Request) -> Dict[str, Any]:
        try:
            payload = await request.json()
        except (json.JSONDecodeError, TypeError) as exc:
            logger.warning("Invalid JSON request payload: %s", exc)
            payload = {}
        return payload if isinstance(payload, dict) else {}

    def _register_session(principal: SessionPrincipal):
        session_store.touch(principal)
        return principal

    def _session_record_dict(session_id: str):
        record = session_store.get(session_id)
        return record.__dict__ if record else None

    def _issue_and_register(request_model: AuthTokenRequest) -> Dict[str, Any]:
        token_bundle = issue_token(
            user_id=request_model.user_id,
            session_id=request_model.session_id,
            ttl_minutes=request_model.ttl_minutes,
            scopes=request_model.scopes,
            display_name=request_model.display_name,
            email=request_model.email,
        )
        principal: SessionPrincipal = token_bundle["principal"]
        _register_session(principal)
        return token_bundle

    def _authenticate_request(request: Request) -> SessionPrincipal:
        if not API_CONFIG.get("require_auth", True):
            payload = request.scope.get("auth_principal")
            if isinstance(payload, SessionPrincipal):
                return payload
            fallback = issue_token(user_id="default_user")
            principal = fallback["principal"]
            _register_session(principal)
            return principal

        auth_header = request.headers.get("authorization")
        token = extract_bearer_token(auth_header)
        principal = decode_token(token)
        if session_store.is_revoked(principal.token_id):
            raise AuthError("Token has been revoked")
        record = session_store.get(principal.session_id)
        if record and record.token_id != principal.token_id and record.active:
            raise AuthError("Session token is no longer current")
        return principal

    async def health(request: Request):
        pipeline: ChatPipeline = request.app.state.chat_pipeline
        circuit_breaker: Optional[CircuitBreaker] = pipeline.circuit_breaker
        breaker_state = circuit_breaker.get_status() if circuit_breaker else {"state": "unknown"}
        ok, msg = await check_llm_health(
            config=LLM_CONFIG,
            timeout_sec=RESILIENCE_CONFIG.get("health_check_timeout_sec", 10.0),
        )
        return _json_response({
            "ok": ok,
            "message": msg,
            "circuit_breaker": breaker_state,
            "model": LLM_CONFIG["model_name"],
        })

    async def auth_token(request: Request):
        limiter: InMemoryRateLimiter = request.app.state.rate_limiter
        allowed, retry_after = limiter.check(
            "auth_token",
            _client_ip(request),
            str(RATE_LIMIT_CONFIG.get("token", "10/minute")),
        )
        if not allowed:
            return _rate_limit_response(retry_after)

        payload = await _get_payload(request)
        try:
            request_model = _model_validate(AuthTokenRequest, payload)
            token_bundle = _issue_and_register(request_model)
            principal = token_bundle["principal"]
            return _json_response(
                AuthTokenResponse(
                    access_token=token_bundle["access_token"],
                    token_type=token_bundle["token_type"],
                    expires_in=token_bundle["expires_in"],
                    principal=principal.to_dict(),
                ).model_dump()
            )
        except Exception as exc:
            return _json_response({"error": str(exc)}, status_code=400)

    async def auth_me(request: Request):
        try:
            principal = _authenticate_request(request)
            return _json_response(
                {
                    "authenticated": True,
                    "principal": principal.to_dict(),
                    "session": _session_record_dict(principal.session_id),
                }
            )
        except AuthError as exc:
            return _json_response({"error": str(exc)}, status_code=401)

    async def auth_refresh(request: Request):
        try:
            principal = _authenticate_request(request)
            token_bundle = issue_token(
                user_id=principal.user_id,
                session_id=principal.session_id,
                scopes=principal.scopes,
                display_name=principal.display_name,
                email=principal.email,
            )
            new_principal: SessionPrincipal = token_bundle["principal"]
            _register_session(new_principal)
            return _json_response(
                {
                    "access_token": token_bundle["access_token"],
                    "token_type": token_bundle["token_type"],
                    "expires_in": token_bundle["expires_in"],
                    "principal": new_principal.to_dict(),
                }
            )
        except AuthError as exc:
            return _json_response({"error": str(exc)}, status_code=401)

    async def sessions_current(request: Request):
        try:
            principal = _authenticate_request(request)
            return _json_response(
                {
                    "principal": principal.to_dict(),
                    "session": _session_record_dict(principal.session_id),
                }
            )
        except AuthError as exc:
            return _json_response({"error": str(exc)}, status_code=401)

    async def sessions_new(request: Request):
        try:
            principal = _authenticate_request(request)
            token_bundle = issue_token(
                user_id=principal.user_id,
                scopes=principal.scopes,
                display_name=principal.display_name,
                email=principal.email,
            )
            new_principal: SessionPrincipal = token_bundle["principal"]
            _register_session(new_principal)
            return _json_response(
                {
                    "access_token": token_bundle["access_token"],
                    "token_type": token_bundle["token_type"],
                    "expires_in": token_bundle["expires_in"],
                    "principal": new_principal.to_dict(),
                }
            )
        except AuthError as exc:
            return _json_response({"error": str(exc)}, status_code=401)

    async def sessions_close(request: Request):
        try:
            principal = _authenticate_request(request)
            session_store.revoke(principal.token_id)
            return _json_response(
                {
                    "status": "closed",
                    "principal": principal.to_dict(),
                }
            )
        except AuthError as exc:
            return _json_response({"error": str(exc)}, status_code=401)

    async def sessions_list(request: Request):
        try:
            principal = _authenticate_request(request)
            sessions = [record.__dict__ for record in session_store.list_user_sessions(principal.user_id)]
            return _json_response(
                {
                    "user_id": principal.user_id,
                    "sessions": sessions,
                }
            )
        except AuthError as exc:
            return _json_response({"error": str(exc)}, status_code=401)

    async def _run_chat(request: Request, stream: bool = True):
        payload = await _get_payload(request)
        try:
            request_model = _model_validate(ChatRequest, payload)
        except Exception as exc:
            return _json_response({"error": str(exc)}, status_code=400)

        try:
            principal = _authenticate_request(request)
        except AuthError as exc:
            return _json_response({"error": str(exc)}, status_code=401)

        user_id = request_model.user_id or principal.user_id
        session_id = request_model.session_id or principal.session_id

        if request_model.user_id and request_model.user_id != principal.user_id:
            return _json_response({"error": "user_id does not match token principal"}, status_code=403)
        if request_model.session_id and request_model.session_id != principal.session_id:
            return _json_response({"error": "session_id does not match token principal"}, status_code=403)

        limiter: InMemoryRateLimiter = request.app.state.rate_limiter
        allowed, retry_after = limiter.check(
            "chat",
            principal.user_id or _client_ip(request),
            str(RATE_LIMIT_CONFIG.get("chat", "60/minute")),
        )
        if not allowed:
            return _rate_limit_response(retry_after)

        pipeline: ChatPipeline = request.app.state.chat_pipeline

        if stream:
            async def event_generator():
                try:
                    yield {
                        "event": "session",
                        "data": json.dumps(
                            {
                                "user_id": user_id,
                                "session_id": session_id,
                                "principal": principal.to_dict(),
                            },
                            ensure_ascii=False,
                        ),
                    }
                    async for event in pipeline.stream_execute(request_model.message, user_id=user_id, session_id=session_id):
                        yield {
                            "event": event.get("event", "message"),
                            "data": json.dumps(event.get("data", {}), ensure_ascii=False),
                        }
                except CircuitOpenError as exc:
                    yield {"event": "error", "data": json.dumps({"error": str(exc)}, ensure_ascii=False)}
                except Exception as exc:
                    yield {"event": "error", "data": json.dumps({"error": str(exc)}, ensure_ascii=False)}

            return EventSourceResponse(event_generator())

        try:
            result = await pipeline.execute(request_model.message, user_id=user_id, session_id=session_id)
            result["principal"] = principal.to_dict()
            return _json_response(result)
        except CircuitOpenError as exc:
            return _json_response({"error": str(exc)}, status_code=503)
        except Exception as exc:
            return _json_response({"error": str(exc)}, status_code=500)

    async def chat(request: Request):
        return await _run_chat(request, stream=True)

    async def chat_sync(request: Request):
        return await _run_chat(request, stream=False)

    async def root(request: Request):
        return _json_response(
            {
                "name": "Aligo Travel Agent API",
                "version": "1.0.0",
                "auth": {
                    "scheme": "Bearer JWT",
                    "token_endpoint": "/api/v1/auth/token",
                },
                "endpoints": [
                    "/health",
                    "/api/v1/auth/token",
                    "/api/v1/auth/me",
                    "/api/v1/auth/refresh",
                    "/api/v1/sessions/current",
                    "/api/v1/sessions/new",
                    "/api/v1/sessions/close",
                    "/api/v1/sessions",
                    "/api/v1/chat",
                    "/api/v1/chat/sync",
                ],
            }
        )

    if FASTAPI_AVAILABLE:
        app = FastAPI(title="Aligo Travel Agent API", version="1.0.0", lifespan=lifespan)
    else:
        app = FastAPI(lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:3000",
            "http://127.0.0.1:3000",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.add_route("/", root, methods=["GET"])
    app.add_route("/health", health, methods=["GET"])
    app.add_route("/api/v1/auth/token", auth_token, methods=["POST"])
    app.add_route("/api/v1/auth/me", auth_me, methods=["GET"])
    app.add_route("/api/v1/auth/refresh", auth_refresh, methods=["POST"])
    app.add_route("/api/v1/sessions/current", sessions_current, methods=["GET"])
    app.add_route("/api/v1/sessions/new", sessions_new, methods=["POST"])
    app.add_route("/api/v1/sessions/close", sessions_close, methods=["POST"])
    app.add_route("/api/v1/sessions", sessions_list, methods=["GET"])
    app.add_route("/api/v1/chat", chat, methods=["POST"])
    app.add_route("/api/v1/chat/sync", chat_sync, methods=["POST"])

    return app


try:
    app = create_app()
except Exception as exc:  # pragma: no cover - import-time guard for missing deps
    app = None
    BUILD_ERROR = exc
