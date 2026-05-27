"""
短期记忆 (Short-term Memory)
存储当前会话最近的对话历史，用于理解上下文和消歧
支持 Redis 缓存后端，分布式部署时会话共享
"""
from typing import List, Dict, Any, Optional
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class ShortTermMemory:
    """
    短期记忆：存储最近的对话历史
    - 存储最近 5-10 轮对话
    - 自动淘汰旧消息
    - 用于上下文理解
    - 可选 Redis 后端（分布式部署时会话共享）
    """

    def __init__(self, max_turns: int = 10, redis_cache=None):
        """
        初始化短期记忆

        Args:
            max_turns: 最大保存轮数（一轮 = 一对用户-助手消息）
            redis_cache: RedisCache 实例（可选，传入后启用缓存）
        """
        self.max_turns = max_turns
        self.messages: List[Dict[str, Any]] = []
        self.redis_cache = redis_cache
        self._session_id = None  # 在 load_from_redis / save_to_redis 时设置

    def _load_from_redis(self, session_id: str) -> bool:
        """尝试从 Redis 加载短期记忆"""
        if not self.redis_cache:
            return False
        cached = self.redis_cache.get_short_term_memory(session_id)
        if cached is not None:
            self.messages = cached
            logger.info(f"Loaded short-term memory from Redis for session {session_id}")
            return True
        return False

    def _save_to_redis(self, session_id: str):
        """保存短期记忆到 Redis"""
        if self.redis_cache and session_id:
            self.redis_cache.save_short_term_memory(session_id, self.messages)

    def set_session(self, session_id: str):
        """设置当前会话ID，用于 Redis 缓存"""
        self._session_id = session_id
        self.messages = []
        # 尝试从 Redis 恢复
        self._load_from_redis(session_id)

    def add_message(self, role: str, content: str, metadata: Dict = None):
        """
        添加消息到短期记忆

        Args:
            role: 角色 (user/assistant)
            content: 消息内容
            metadata: 额外的元数据
        """
        message = {
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat(),
            "metadata": metadata or {}
        }

        self.messages.append(message)

        # 自动淘汰旧消息（保持 max_turns 轮对话）
        max_messages = self.max_turns * 2
        if len(self.messages) > max_messages:
            self.messages = self.messages[-max_messages:]

        # 同步写入 Redis
        self._save_to_redis(self._session_id)

        logger.debug(f"Added message to short-term memory: {role}")

    def get_recent_context(self, n_turns: int = None) -> List[Dict[str, Any]]:
        """
        获取最近 n 轮对话

        Args:
            n_turns: 获取轮数，默认为全部

        Returns:
            最近的消息列表
        """
        if n_turns is None:
            return self.messages.copy()

        n_messages = n_turns * 2
        return self.messages[-n_messages:] if len(self.messages) > n_messages else self.messages.copy()

    def get_context_string(self, n_turns: int = 5) -> str:
        """
        获取最近对话的字符串表示

        Args:
            n_turns: 获取轮数

        Returns:
            格式化的对话字符串
        """
        messages = self.get_recent_context(n_turns)
        if not messages:
            return "无历史对话"

        lines = []
        for msg in messages:
            role_name = "用户" if msg["role"] == "user" else "助手"
            lines.append(f"{role_name}: {msg['content']}")

        return "\n".join(lines)

    def clear(self):
        """清空短期记忆"""
        self.messages = []
        if self.redis_cache and self._session_id:
            self.redis_cache.clear_short_term_memory(self._session_id)
        logger.info("Short-term memory cleared")

    def get_statistics(self) -> Dict[str, Any]:
        """获取统计信息"""
        return {
            "total_messages": len(self.messages),
            "max_turns": self.max_turns,
            "oldest_message_time": self.messages[0]["timestamp"] if self.messages else None,
            "newest_message_time": self.messages[-1]["timestamp"] if self.messages else None
        }
