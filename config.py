"""
Configuration for the Aligo Multi-Agent System
"""

# LLM Configuration
LLM_CONFIG = {
    "api_key": "82df119f-41c2-4f44-bc20-f5ed0f540e0e",
    "model_name": "doubao-seed-1-6-251015",
    "base_url": "https://ark.cn-beijing.volces.com/api/v3",
    "temperature": 0.7,
    "max_tokens": 8192,
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
    "host": "localhost",
    "port": 6379,
    "db": 0,
    "password": None,
    "enabled": True,  # 设为 False 则禁用 Redis 缓存
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
