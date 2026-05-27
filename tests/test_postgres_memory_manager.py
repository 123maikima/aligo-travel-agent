#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
PostgreSQL 配置传递测试
"""
from __future__ import annotations

import os
import sys
from unittest.mock import patch

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from travel_agent.context.memory_manager import MemoryManager


class _FakeLongTermMemory:
    def __init__(self, user_id, storage_path="data/memory", redis_cache=None, postgres_config=None):
        self.user_id = user_id
        self.storage_path = storage_path
        self.redis_cache = redis_cache
        self.postgres_config = postgres_config

    def add_chat_message(self, *args, **kwargs):
        pass

    def get_preference(self, pref_type=None):
        return {}

    def get_chat_history(self, limit=None, session_id=None):
        return []

    def get_trip_history(self, limit=10):
        return []

    def get_frequent_destinations(self, top_n=5):
        return []

    def get_statistics(self):
        return {"total_trips": 0, "total_messages": 0, "total_queries": 0, "frequent_destinations": {}}


def test_memory_manager_passes_postgres_config():
    custom_config = {
        "host": "127.0.0.1",
        "port": 5432,
        "dbname": "travel_agent_test",
        "user": "postgres",
        "password": "secret",
        "sslmode": "disable",
        "connect_timeout": 3,
        "enabled": True,
    }

    with patch("context.memory_manager.LongTermMemory", _FakeLongTermMemory):
        manager = MemoryManager(
            user_id="user_001",
            session_id="session_001",
            storage_path="data/test_memory",
            postgres_config=custom_config,
        )
        assert manager.postgres_config["dbname"] == "travel_agent_test"
        assert manager.long_term.postgres_config["enabled"] is True
        assert manager.long_term.postgres_config["host"] == "127.0.0.1"

    print("[PASS] test_memory_manager_passes_postgres_config")


if __name__ == "__main__":
    print("=" * 60)
    print("PostgreSQL 配置传递测试")
    print("=" * 60)

    test_memory_manager_passes_postgres_config()

    print("\n" + "=" * 60)
    print("全部测试完成")
    print("=" * 60)
