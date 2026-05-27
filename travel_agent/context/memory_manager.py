"""
记忆管理器 (Memory Manager)
统一管理两层记忆，提供简单的API
支持 Redis 缓存层
"""
from typing import Dict, Any, List, Optional
from travel_agent.context.short_term_memory import ShortTermMemory
from travel_agent.context.long_term_memory import LongTermMemory
from travel_agent.context.redis_cache import RedisCache
from travel_agent.config import POSTGRES_CONFIG
from travel_agent.llm.sdk import extract_text
import logging
import json

logger = logging.getLogger(__name__)


class MemoryManager:
    """
    记忆管理器：统一管理两层记忆
    - 短期记忆：最近对话（会话级）
    - 长期记忆：用户偏好和历史（跨会话）
    - Redis 缓存：热数据加速访问
    """

    def __init__(self, user_id: str, session_id: str, storage_path: str = "data/memory",
                 llm_model=None, redis_cache: Optional[RedisCache] = None,
                 postgres_config: Optional[Dict[str, Any]] = None):
        """
        初始化记忆管理器

        Args:
            user_id: 用户ID
            session_id: 会话ID
            storage_path: 长期记忆存储路径
            llm_model: LLM模型实例（用于总结长期记忆）
            redis_cache: RedisCache实例（可选，传入后启用缓存）
            postgres_config: PostgreSQL配置（可选，优先使用显式传入值）
        """
        self.user_id = user_id
        self.session_id = session_id
        self.llm_model = llm_model
        self.redis_cache = redis_cache
        self.postgres_config = dict(postgres_config or POSTGRES_CONFIG)

        # 初始化两层记忆（短期记忆和长期记忆都传入 Redis 后端）
        self.short_term = ShortTermMemory(max_turns=10, redis_cache=redis_cache)
        self.short_term.set_session(session_id)
        self.long_term = LongTermMemory(
            user_id,
            storage_path,
            redis_cache=redis_cache,
            postgres_config=self.postgres_config,
        )

        logger.info(f"Memory manager initialized for user {user_id}, session {session_id}"
                    f"{' (Redis cache enabled)' if redis_cache and redis_cache.enabled else ''}")

    # ========== 短期记忆操作 ==========

    def add_message(self, role: str, content: str, metadata: Dict = None):
        """
        添加消息到短期记忆和长期记忆

        Args:
            role: 角色 (user/assistant)
            content: 消息内容
            metadata: 元数据
        """
        # 添加到短期记忆（当前会话）
        self.short_term.add_message(role, content, metadata)

        # 同时添加到长期记忆（跨会话持久化）
        self.long_term.add_chat_message(role, content, self.session_id)

    # ========== 长期记忆操作 ==========

    def get_preference(self, pref_type: str = None) -> Any:
        """获取用户偏好（优先从 Redis 热缓存读取）"""
        return self.long_term.get_preference(pref_type)

    def save_preference(self, pref_type: str, value: Any):
        """保存用户偏好（同步写入 Redis 热缓存）"""
        self.long_term.save_preference(pref_type, value)

    def invalidate_preferences(self):
        """使某用户所有 Redis 偏好缓存失效"""
        if self.redis_cache:
            self.redis_cache.invalidate_preferences(self.user_id)

    # ========== 综合查询 ==========

    def get_full_context(self) -> Dict[str, Any]:
        """
        获取完整上下文（两层记忆）

        Returns:
            完整上下文字典
        """
        return {
            "short_term": {
                "recent_dialogue": self.short_term.get_recent_context(5),
                "context_string": self.short_term.get_context_string(5),
                "statistics": self.short_term.get_statistics()
            },
            "long_term": {
                "preferences": self.long_term.get_preference(),
                "chat_history": self.long_term.get_chat_history(10),
                "trip_history": self.long_term.get_trip_history(5),
                "frequent_destinations": self.long_term.get_frequent_destinations(3),
                "statistics": self.long_term.get_statistics()
            }
        }

    def get_context_for_agent(self, long_term_summary: str = None) -> str:
        """
        获取用于Agent的上下文字符串

        Args:
            long_term_summary: 长期记忆总结（可选，需提前调用 get_long_term_summary_async）

        Returns:
            格式化的上下文字符串
        """
        lines = []

        # 长期记忆总结（历史会话）
        if long_term_summary:
            lines.append("【历史会话总结】")
            lines.append(long_term_summary)
            lines.append("")

        # 用户偏好
        prefs = self.long_term.get_preference()
        has_prefs = any(v for v in prefs.values() if v)
        if has_prefs:
            lines.append("【用户偏好】")
            for key, value in prefs.items():
                if value:
                    lines.append(f"- {key}: {value}")
            lines.append("")

        # 短期记忆（当前会话）
        context_str = self.short_term.get_context_string(3)
        if context_str != "无历史对话":
            lines.append("【当前会话对话】")
            lines.append(context_str)
            lines.append("")

        return "\n".join(lines) if lines else "无上下文信息"

    # ========== 会话管理 ==========

    def end_session(self):
        """结束会话"""
        self.short_term.clear()
        logger.info(f"Session ended: {self.session_id}")

    async def get_long_term_summary_async(self, max_messages: int = 50) -> str:
        """
        使用LLM总结长期聊天历史（异步版本）
        总结结果缓存到 Redis（TTL 30min），避免重复调用LLM

        Args:
            max_messages: 最多总结的消息数量

        Returns:
            总结后的文本
        """
        # 优先从 Redis 缓存读取
        if self.redis_cache:
            cached = self.redis_cache.get_summary(self.user_id)
            if cached is not None:
                summary = cached.get("summary", "")
                if summary:
                    logger.info(f"Loaded long-term summary from Redis cache for user {self.user_id}")
                    return summary

        if not self.llm_model:
            return ""

        # 获取长期聊天历史（排除当前会话）
        all_history = self.long_term.get_chat_history(limit=max_messages)
        history_from_other_sessions = [
            msg for msg in all_history
            if msg.get("session_id") != self.session_id
        ]

        # 获取行程历史
        trip_history = self.long_term.get_trip_history(limit=20)

        # 如果既没有聊天记录也没有行程记录，直接返回
        if not history_from_other_sessions and not trip_history:
            return ""

        # 构建聊天记录文本
        history_text = []
        for msg in history_from_other_sessions[-max_messages:]:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            timestamp = msg.get("timestamp", "")
            history_text.append(f"[{timestamp}] {role}: {content}")

        history_str = "\n".join(history_text) if history_text else "（无聊天记录）"

        # 构建行程历史文本
        trip_text = []
        for trip in trip_history:
            origin = trip.get("origin", "未知")
            destination = trip.get("destination", "未知")
            start_date = trip.get("start_date", "")
            end_date = trip.get("end_date", "")
            purpose = trip.get("purpose", "旅游")
            timestamp = trip.get("timestamp", "")

            if start_date and end_date:
                trip_text.append(f"[{timestamp}] {origin} → {destination} ({start_date} 至 {end_date}) - {purpose}")
            elif start_date:
                trip_text.append(f"[{timestamp}] {origin} → {destination} ({start_date}) - {purpose}")
            else:
                trip_text.append(f"[{timestamp}] {origin} → {destination} - {purpose}")

        trip_str = "\n".join(trip_text) if trip_text else "（无行程记录）"

        # 使用LLM总结
        summarization_prompt = f"""请总结以下历史信息中的关键内容，包括：
1. 用户的旅行偏好和习惯
2. 用户询问过的重要问题
3. 用户的出行历史和目的地
4. 其他重要的上下文信息

【历史聊天记录】
{history_str}

【历史行程记录】
{trip_str}

请用简洁的语言总结（不超过200字）："""

        try:
            # 调用模型（异步调用）
            response = await self.llm_model([{"role": "user", "content": summarization_prompt}])
            summary = await extract_text(response)

            logger.info(f"Generated long-term memory summary ({len(summary)} chars)")

            # 写入 Redis 缓存
            if self.redis_cache and summary.strip():
                self.redis_cache.save_summary(self.user_id, summary.strip())
                logger.info(f"Cached long-term summary to Redis for user {self.user_id}")

            return summary.strip()

        except Exception as e:
            logger.error(f"Failed to generate long-term summary: {e}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
            return ""

    def get_long_term_summary(self, max_messages: int = 50) -> str:
        """
        使用LLM总结长期聊天历史（同步版本）

        Args:
            max_messages: 最多总结的消息数量

        Returns:
            总结后的文本
        """
        import asyncio

        # 检查是否在事件循环中
        try:
            loop = asyncio.get_running_loop()
            # 已经在事件循环中，不能使用 asyncio.run
            logger.warning("get_long_term_summary called from async context, please use get_long_term_summary_async instead")
            return ""
        except RuntimeError:
            # 没有运行的事件循环，可以使用 asyncio.run
            return asyncio.run(self.get_long_term_summary_async(max_messages))
