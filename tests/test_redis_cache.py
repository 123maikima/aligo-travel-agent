#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Redis 缓存层测试
测试命中率统计、短期记忆缓存、LLM总结缓存、偏好缓存
"""
import sys
import os
import time

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from travel_agent.context.redis_cache import RedisCache, CacheStats
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def test_cache_stats():
    """测试命中率统计"""
    stats = CacheStats()

    assert stats.hits == 0
    assert stats.misses == 0
    assert stats.hit_rate == 0.0

    stats.record_hit()
    stats.record_hit()
    stats.record_miss()

    assert stats.hits == 2
    assert stats.misses == 1
    assert abs(stats.hit_rate - 2/3) < 0.001

    stats.reset()
    assert stats.hits == 0
    assert stats.misses == 0

    print("[PASS] test_cache_stats")


def test_redis_short_term_memory():
    """测试短期记忆 Redis 缓存"""
    cache = RedisCache()
    if not cache.enabled:
        print("[SKIP] test_redis_short_term_memory (Redis not available)")
        return

    session_id = "test_session_001"
    messages = [
        {"role": "user", "content": "你好", "timestamp": "2026-05-18T10:00:00"},
        {"role": "assistant", "content": "你好！有什么可以帮您？", "timestamp": "2026-05-18T10:00:01"},
    ]

    # 写入
    cache.save_short_term_memory(session_id, messages)

    # 读取
    cached = cache.get_short_term_memory(session_id)
    assert cached is not None
    assert len(cached) == 2
    assert cached[0]["content"] == "你好"

    # 清除
    cache.clear_short_term_memory(session_id)
    assert cache.get_short_term_memory(session_id) is None

    print("[PASS] test_redis_short_term_memory")


def test_redis_summary_cache():
    """测试LLM总结 Redis 缓存"""
    cache = RedisCache()
    if not cache.enabled:
        print("[SKIP] test_redis_summary_cache (Redis not available)")
        return

    user_id = "test_user_001"
    summary = "用户偏好住汉庭酒店，常坐东航，家在上海。"

    # 写入
    cache.save_summary(user_id, summary)

    # 读取
    cached = cache.get_summary(user_id)
    assert cached is not None
    assert cached["summary"] == summary

    # 验证命中率统计
    stats = cache.get_stats()
    assert stats["hits"] >= 1  # 至少一次命中

    print("[PASS] test_redis_summary_cache")


def test_redis_preference_cache():
    """测试偏好 Redis 缓存"""
    cache = RedisCache()
    if not cache.enabled:
        print("[SKIP] test_redis_preference_cache (Redis not available)")
        return

    user_id = "test_user_002"

    # 写入多个偏好
    cache.save_preference(user_id, "hotel_brands", ["汉庭", "如家"])
    cache.save_preference(user_id, "seat_pref", "靠窗")

    # 读取
    hotels = cache.get_preference(user_id, "hotel_brands")
    assert hotels == ["汉庭", "如家"]

    seat = cache.get_preference(user_id, "seat_pref")
    assert seat == "靠窗"

    # 不存在的偏好
    assert cache.get_preference(user_id, "airlines") is None

    # 使所有偏好失效
    cache.invalidate_preferences(user_id)
    assert cache.get_preference(user_id, "hotel_brands") is None

    print("[PASS] test_redis_preference_cache")


def test_redis_hit_rate():
    """测试命中率计算"""
    cache = RedisCache()
    if not cache.enabled:
        print("[SKIP] test_redis_hit_rate (Redis not available)")
        return

    cache.reset_stats()

    # 3次 miss（key不存在）
    cache.get_summary("nonexistent_user_1")
    cache.get_summary("nonexistent_user_2")
    cache.get_summary("nonexistent_user_3")

    # 先写入再读取 = 命中
    cache.save_summary("hit_user", "test summary")
    cache.get_summary("hit_user")

    stats = cache.get_stats()
    assert stats["hits"] == 1
    assert stats["misses"] == 3
    assert abs(stats["hit_rate"] - 0.25) < 0.001

    print(f"[PASS] test_redis_hit_rate (hits={stats['hits']}, misses={stats['misses']}, rate={stats['hit_rate']:.2f})")


def test_short_term_memory_with_redis():
    """测试 ShortTermMemory + Redis 集成"""
    from travel_agent.context.short_term_memory import ShortTermMemory

    cache = RedisCache()
    if not cache.enabled:
        print("[SKIP] test_short_term_memory_with_redis (Redis not available)")
        return

    # 创建带 Redis 的短期记忆
    stm = ShortTermMemory(max_turns=3, redis_cache=cache)
    session_id = "test_stm_session"
    stm.set_session(session_id)

    # 添加消息
    stm.add_message("user", "我想去杭州")
    stm.add_message("assistant", "好的，请问您计划去几天？")

    # 验证 Redis 中有缓存
    cached = cache.get_short_term_memory(session_id)
    assert cached is not None
    assert len(cached) == 2
    assert cached[0]["content"] == "我想去杭州"

    # 测试从 Redis 恢复
    stm2 = ShortTermMemory(max_turns=3, redis_cache=cache)
    stm2.set_session(session_id)
    assert len(stm2.messages) == 2
    assert stm2.messages[0]["content"] == "我想去杭州"

    # 测试滑动窗口
    for i in range(10):
        stm2.add_message("user", f"message_{i}")
        stm2.add_message("assistant", f"reply_{i}")

    # max_turns=3 -> max_messages=6
    assert len(stm2.messages) == 6

    # 清除
    stm2.clear()
    assert cache.get_short_term_memory(session_id) is None

    print("[PASS] test_short_term_memory_with_redis")


def test_long_term_memory_preference_redis():
    """测试 LongTermMemory + Redis 偏好缓存集成"""
    from travel_agent.context.long_term_memory import LongTermMemory

    cache = RedisCache()
    if not cache.enabled:
        print("[SKIP] test_long_term_memory_preference_redis (Redis not available)")
        return

    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        # 创建带 Redis 的长期记忆
        ltm = LongTermMemory("test_user", storage_path=tmpdir, redis_cache=cache)

        # 保存偏好
        ltm.save_preference("hotel_brands", ["汉庭", "如家"])
        ltm.save_preference("airlines", ["东航"])

        # 验证 Redis 中有缓存
        assert cache.get_preference("test_user", "hotel_brands") == ["汉庭", "如家"]
        assert cache.get_preference("test_user", "airlines") == ["东航"]

        # 验证从 LongTermMemory 读取也走 Redis 缓存
        assert ltm.get_preference("hotel_brands") == ["汉庭", "如家"]

        # 添加酒店品牌
        ltm.add_hotel_brand("全季")
        assert ltm.get_preference("hotel_brands") == ["汉庭", "如家", "全季"]
        assert cache.get_preference("test_user", "hotel_brands") == ["汉庭", "如家", "全季"]

        # 添加航空公司
        ltm.add_airline("南航")
        assert ltm.get_preference("airlines") == ["东航", "南航"]
        assert cache.get_preference("test_user", "airlines") == ["东航", "南航"]

        # 验证读穿：删除 Redis 后仍可从磁盘读取并回填
        cache.invalidate_preference("test_user", "hotel_brands")
        assert ltm.get_preference("hotel_brands") == ["汉庭", "如家", "全季"]
        assert cache.get_preference("test_user", "hotel_brands") == ["汉庭", "如家", "全季"]

        # 清空历史，偏好缓存失效
        ltm.clear_history()
        # 使失效后再次读取应该 miss
        cache.get_preference("test_user", "hotel_brands")
        assert cache.stats.misses > 0

    print("[PASS] test_long_term_memory_preference_redis")


def test_long_term_memory_summary_invalidation():
    """测试长期记忆变更会失效 Redis 总结缓存"""
    from travel_agent.context.long_term_memory import LongTermMemory

    cache = RedisCache()
    if not cache.enabled:
        print("[SKIP] test_long_term_memory_summary_invalidation (Redis not available)")
        return

    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        ltm = LongTermMemory("summary_user", storage_path=tmpdir, redis_cache=cache)
        cache.save_summary("summary_user", "旧摘要")
        assert cache.get_summary("summary_user")["summary"] == "旧摘要"

        ltm.add_chat_message("user", "我想去上海")
        assert cache.get_summary("summary_user") is None

        cache.save_summary("summary_user", "旧摘要2")
        ltm.save_trip_history({
            "origin": "北京",
            "destination": "上海",
            "purpose": "出差"
        })
        assert cache.get_summary("summary_user") is None

    print("[PASS] test_long_term_memory_summary_invalidation")


def test_short_term_session_reset():
    """测试短期记忆切换会话时不会残留旧消息"""
    from travel_agent.context.short_term_memory import ShortTermMemory

    stm = ShortTermMemory(max_turns=3)
    stm.add_message("user", "session_a_1")
    stm.add_message("assistant", "session_a_2")
    assert len(stm.messages) == 2

    stm.set_session("session_b")
    assert stm.messages == []

    print("[PASS] test_short_term_session_reset")


if __name__ == "__main__":
    print("=" * 50)
    print("Redis 缓存层测试")
    print("=" * 50)

    test_cache_stats()
    test_redis_short_term_memory()
    test_redis_summary_cache()
    test_redis_preference_cache()
    test_redis_hit_rate()
    test_short_term_memory_with_redis()
    test_long_term_memory_preference_redis()
    test_long_term_memory_summary_invalidation()
    test_short_term_session_reset()

    print("\n" + "=" * 50)
    print("全部测试完成")
    print("=" * 50)
