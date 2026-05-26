"""
Configuration for the Aligo Multi-Agent System
"""
import os


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

# LLM Configuration
LLM_CONFIG = {
    "api_key": _env_str("LLM_API_KEY", "82df119f-41c2-4f44-bc20-f5ed0f540e0e"),
    "model_name": _env_str("LLM_MODEL_NAME", "doubao-seed-1-6-251015"),
    "base_url": _env_str("LLM_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3"),
    "temperature": float(os.getenv("LLM_TEMPERATURE", "0.7")),
    "max_tokens": _env_int("LLM_MAX_TOKENS", 8192),
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
    "embedding_model": "data/models/bge-small-zh-v1.5",
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
