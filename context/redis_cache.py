"""
Redis 缓存客户端
用于短期记忆、LLM总结、用户偏好热数据的缓存
"""
import json
import logging
from typing import Optional, Any

logger = logging.getLogger(__name__)

try:
    import redis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False
    logger.warning("redis package not installed, cache will fallback to no-op")


class CacheStats:
    """缓存命中率统计"""

    def __init__(self):
        self.hits = 0
        self.misses = 0

    def record_hit(self):
        self.hits += 1

    def record_miss(self):
        self.misses += 1

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total > 0 else 0.0

    def reset(self):
        self.hits = 0
        self.misses = 0

    def get_stats(self) -> dict:
        total = self.hits + self.misses
        return {
            "hits": self.hits,
            "misses": self.misses,
            "total": total,
            "hit_rate": round(self.hit_rate, 4),
        }


class RedisCache:
    """
    Redis 缓存层

    三类缓存数据：
    1. 短期记忆: stm:{session_id}:messages  (TTL 1h)
    2. LLM总结:  summary:{user_id}           (TTL 30min)
    3. 用户偏好: pref:{user_id}:{pref_type}   (TTL 30min)
    """

    # Key 前缀
    KEY_STM = "stm:{session_id}:messages"
    KEY_SUMMARY = "summary:{user_id}"
    KEY_PREF = "pref:{user_id}:{pref_type}"

    # TTL (秒)
    TTL_STM = 3600        # 1小时
    TTL_SUMMARY = 1800    # 30分钟
    TTL_PREF = 1800       # 30分钟

    def __init__(self, host: str = "localhost", port: int = 6379, db: int = 0,
                 password: Optional[str] = None, enabled: bool = True):
        self.enabled = enabled and REDIS_AVAILABLE
        self._client: Optional[redis.Redis] = None
        self.stats = CacheStats()

        if self.enabled:
            try:
                self._client = redis.Redis(
                    host=host, port=port, db=db,
                    password=password, decode_responses=True,
                    socket_connect_timeout=5,
                    socket_timeout=5,
                )
                self._client.ping()
                logger.info(f"Redis connected at {host}:{port}/{db}")
            except Exception as e:
                logger.warning(f"Redis connection failed, cache disabled: {e}")
                self.enabled = False
                self._client = None

    def _key(self, pattern: str, **kwargs) -> str:
        return pattern.format(**kwargs)

    def _get(self, key: str) -> Optional[Any]:
        """获取缓存（内部方法）"""
        if not self.enabled or not self._client:
            return None
        try:
            data = self._client.get(key)
            if data is not None:
                self.stats.record_hit()
                return json.loads(data)
            self.stats.record_miss()
            return None
        except Exception as e:
            logger.warning(f"Redis get failed: {e}")
            self.stats.record_miss()
            return None

    def _set(self, key: str, value: Any, ttl: int):
        """写入缓存（内部方法）"""
        if not self.enabled or not self._client:
            return
        try:
            self._client.setex(key, ttl, json.dumps(value, ensure_ascii=False))
        except Exception as e:
            logger.warning(f"Redis set failed: {e}")

    def _delete(self, key: str):
        """删除缓存（内部方法）"""
        if not self.enabled or not self._client:
            return
        try:
            self._client.delete(key)
        except Exception:
            pass

    # ========== 短期记忆缓存 ==========

    def get_short_term_memory(self, session_id: str) -> Optional[list]:
        """获取短期记忆缓存"""
        key = self._key(self.KEY_STM, session_id=session_id)
        return self._get(key)

    def save_short_term_memory(self, session_id: str, messages: list):
        """保存短期记忆"""
        key = self._key(self.KEY_STM, session_id=session_id)
        self._set(key, messages, self.TTL_STM)

    def clear_short_term_memory(self, session_id: str):
        """清除短期记忆"""
        key = self._key(self.KEY_STM, session_id=session_id)
        self._delete(key)

    # ========== LLM总结缓存 ==========

    def get_summary(self, user_id: str) -> Optional[str]:
        """获取LLM总结缓存"""
        key = self._key(self.KEY_SUMMARY, user_id=user_id)
        result = self._get(key)
        return result if result is not None else None

    def save_summary(self, user_id: str, summary: str):
        """保存LLM总结"""
        key = self._key(self.KEY_SUMMARY, user_id=user_id)
        self._set(key, {"summary": summary}, self.TTL_SUMMARY)

    # ========== 用户偏好缓存 ==========

    def get_preference(self, user_id: str, pref_type: str) -> Optional[Any]:
        """获取偏好缓存"""
        key = self._key(self.KEY_PREF, user_id=user_id, pref_type=pref_type)
        return self._get(key)

    def save_preference(self, user_id: str, pref_type: str, value: Any):
        """保存偏好"""
        key = self._key(self.KEY_PREF, user_id=user_id, pref_type=pref_type)
        self._set(key, value, self.TTL_PREF)

    def invalidate_preferences(self, user_id: str):
        """使某用户所有偏好缓存失效"""
        if not self.enabled or not self._client:
            return
        try:
            # 扫描并删除该用户的所有偏好key
            pattern = self._key(self.KEY_PREF, user_id=user_id, pref_type="*")
            for key in self._client.scan_iter(match=pattern):
                self._client.delete(key)
        except Exception as e:
            logger.warning(f"Failed to invalidate preferences: {e}")

    # ========== 统计 ==========

    def get_stats(self) -> dict:
        """获取缓存统计"""
        return self.stats.get_stats()

    def reset_stats(self):
        """重置统计"""
        self.stats.reset()

    def close(self):
        """关闭连接"""
        if self._client:
            try:
                self._client.close()
            except Exception:
                pass
