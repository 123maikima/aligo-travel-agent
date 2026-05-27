"""
Configuration for the Aligo Multi-Agent System
"""
import os
import logging
from pathlib import Path
from typing import Any, Dict


logger = logging.getLogger(__name__)


def _load_dotenv_file():
    """Load project .env values without overriding existing environment variables."""
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


_load_dotenv_file()


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    return int(value)


def _env_str(name: str, default: str) -> str:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip() or default


def _env_optional_str(name: str) -> str | None:
    value = os.getenv(name)
    if value is None:
        return None
    value = value.strip()
    return value or None

# LLM Configuration
LLM_CONFIG = {
    "provider": _env_str("LLM_PROVIDER", "doubao"),
    "api_key": _env_str("LLM_API_KEY", "replace-me"),
    "model_name": _env_str("LLM_MODEL_NAME", "doubao-seed-1-6-251015"),
    "base_url": _env_str("LLM_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3"),
    "temperature": float(os.getenv("LLM_TEMPERATURE", "0.7")),
    "max_tokens": _env_int("LLM_MAX_TOKENS", 8192),
}


def _llm_profile(prefix: str, fallback: Dict[str, Any]) -> Dict[str, Any]:
    """Build a model profile from env vars with fallback to default LLM_CONFIG."""
    upper = prefix.upper()
    return {
        "provider": _env_str(f"LLM_{upper}_PROVIDER", fallback["provider"]),
        "api_key": _env_str(f"LLM_{upper}_API_KEY", fallback["api_key"]),
        "model_name": _env_str(f"LLM_{upper}_MODEL_NAME", fallback["model_name"]),
        "base_url": _env_str(f"LLM_{upper}_BASE_URL", fallback["base_url"]),
        "temperature": float(os.getenv(f"LLM_{upper}_TEMPERATURE", str(fallback["temperature"]))),
        "max_tokens": _env_int(f"LLM_{upper}_MAX_TOKENS", int(fallback["max_tokens"])),
    }


LLM_MODEL_PROFILES = {
    "default": LLM_CONFIG,
    "fast": _llm_profile("FAST", LLM_CONFIG),
    "reasoning": _llm_profile("REASONING", LLM_CONFIG),
}

AGENT_MODEL_TIERS = {
    "default": _env_str("LLM_TIER_DEFAULT", "default"),
    "intention_agent": _env_str("LLM_TIER_INTENTION_AGENT", "reasoning"),
    "intention": _env_str("LLM_TIER_INTENTION", "reasoning"),
    "itinerary_planning": _env_str("LLM_TIER_ITINERARY_PLANNING", "reasoning"),
    "rag_knowledge": _env_str("LLM_TIER_RAG_KNOWLEDGE", "reasoning"),
    "event_collection": _env_str("LLM_TIER_EVENT_COLLECTION", "fast"),
    "preference": _env_str("LLM_TIER_PREFERENCE", "fast"),
    "memory_query": _env_str("LLM_TIER_MEMORY_QUERY", "fast"),
    "information_query": _env_str("LLM_TIER_INFORMATION_QUERY", "fast"),
    "memory_summary": _env_str("LLM_TIER_MEMORY_SUMMARY", "fast"),
}

# System Configuration
SYSTEM_CONFIG = {
    "enable_llm": True,  # Set to True to use LLM (recommended), False for rule-based
    "log_level": "INFO",
    "max_retries": 3,
    "timeout": 60,  # Increased timeout for better stability
}

# RAG 知识库：嵌入模型（本地路径，无需连 HuggingFace）
RAG_CONFIG = {
    "embedding_model": _env_str("RAG_EMBEDDING_MODEL", "data/models/bge-m3"),
    "documents_dir": _env_str("RAG_DOCUMENTS_DIR", "data/documents"),
    "retrieval_mode": _env_str("RAG_RETRIEVAL_MODE", "hybrid"),  # dense / sparse / hybrid
    "dense_top_k": _env_int("RAG_DENSE_TOP_K", 10),
    "sparse_top_k": _env_int("RAG_SPARSE_TOP_K", 10),
    "final_top_k": _env_int("RAG_FINAL_TOP_K", 3),
    "rrf_k": _env_int("RAG_RRF_K", 60),
}

# Redis 缓存配置
REDIS_CONFIG = {
    # 环境变量优先，便于本地 Redis、CI 和容器环境切换。
    "host": os.getenv("REDIS_HOST", "localhost"),
    "port": _env_int("REDIS_PORT", 6379),
    "db": _env_int("REDIS_DB", 0),
    "password": os.getenv("REDIS_PASSWORD") or None,
    "enabled": _env_bool("REDIS_ENABLED", True),  # 设为 False 则禁用 Redis 缓存
}

# PostgreSQL 持久化配置
POSTGRES_CONFIG = {
    "host": os.getenv("POSTGRES_HOST", "localhost"),
    "port": _env_int("POSTGRES_PORT", 5432),
    "dbname": _env_str("POSTGRES_DB", "travel_agent"),
    "user": _env_str("POSTGRES_USER", "postgres"),
    "password": os.getenv("POSTGRES_PASSWORD") or None,
    "sslmode": _env_str("POSTGRES_SSLMODE", "prefer"),
    "connect_timeout": _env_int("POSTGRES_CONNECT_TIMEOUT", 5),
    "enabled": _env_bool("POSTGRES_ENABLED", False),
}

# Request safety limits
MAX_MESSAGE_LENGTH = _env_int("MAX_MESSAGE_LENGTH", 4096)

# API rate limits. The in-process fallback is per worker; Redis-backed storage is
# recommended for multi-container deployments.
RATE_LIMIT_CONFIG = {
    "enabled": _env_bool("RATE_LIMIT_ENABLED", True),
    "chat": _env_str("RATE_LIMIT_CHAT", "60/minute"),
    "token": _env_str("RATE_LIMIT_TOKEN", "10/minute"),
    "redis_url": _env_optional_str("RATE_LIMIT_REDIS_URL"),
}

# 连接与可用性：重试、熔断、健康检查
RESILIENCE_CONFIG = {
    "max_retries": 3,              # 单次请求最大重试次数（与 SYSTEM_CONFIG 对齐）
    "retry_base_delay_sec": 1.0,   # 重试退避基数（秒）
    "retry_max_delay_sec": 30.0,   # 重试退避上限（秒）
    "circuit_failure_threshold": 5, # 连续失败多少次后熔断
    "circuit_recovery_timeout_sec": 60.0,  # 熔断后多少秒进入半开
    "circuit_half_open_successes": 2,      # 半开状态下连续成功多少次后关闭
    "health_check_timeout_sec": 10.0,      # 健康检查请求超时（秒）
}

# Observability: JSONL trace sink and lightweight metrics
OBSERVABILITY_CONFIG = {
    "enabled": _env_bool("OBSERVABILITY_ENABLED", True),
    "mask_pii": _env_bool("OBSERVABILITY_MASK_PII", True),
    "trace_dir": _env_str("OBSERVABILITY_TRACE_DIR", "data/traces"),
    "event_log": _env_str("OBSERVABILITY_EVENT_LOG", "data/traces/events.jsonl"),
    "metrics_log": _env_str("OBSERVABILITY_METRICS_LOG", "data/traces/metrics.jsonl"),
    "max_field_chars": _env_int("OBSERVABILITY_MAX_FIELD_CHARS", 1200),
}

# Web API 配置
API_CONFIG = {
    "jwt_secret": _env_str("API_JWT_SECRET", "replace-me"),
    "jwt_algorithm": _env_str("API_JWT_ALGORITHM", "HS256"),
    "issuer": _env_str("API_JWT_ISSUER", "travel-agent"),
    "access_token_ttl_minutes": _env_int("API_ACCESS_TOKEN_TTL_MINUTES", 720),
    "require_auth": _env_bool("API_REQUIRE_AUTH", True),
}


def validate_secrets() -> None:
    """Fail fast when required runtime secrets are missing or weak."""
    allow_insecure = _env_bool("ALLOW_INSECURE_STARTUP", False)
    checks = [
        ("API_JWT_SECRET", API_CONFIG.get("jwt_secret")),
        ("LLM_API_KEY", LLM_CONFIG.get("api_key")),
    ]
    if POSTGRES_CONFIG.get("enabled"):
        checks.append(("POSTGRES_PASSWORD", POSTGRES_CONFIG.get("password")))

    invalid = []
    for name, secret in checks:
        if not isinstance(secret, str) or len(secret.strip()) < 16:
            invalid.append(name)

    if not invalid:
        return

    message = (
        "Insecure startup blocked: configure strong values for "
        + ", ".join(invalid)
        + " (minimum 16 characters). Set ALLOW_INSECURE_STARTUP=true only for local development or CI."
    )
    if allow_insecure:
        logger.warning("ALLOW_INSECURE_STARTUP=true: %s", message)
        return
    raise RuntimeError(message)
