#!/usr/bin/env python
"""
初始化 PostgreSQL 长期记忆表结构。

用途：
- 在新环境中一键创建长期记忆相关表
- 也可在迁移前先跑一遍，确保 schema 已就绪
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(project_root))

from travel_agent.config import POSTGRES_CONFIG
from travel_agent.context.postgres_storage import PostgresLongTermStore


def main() -> int:
    if not POSTGRES_CONFIG.get("enabled", False):
        print("POSTGRES_ENABLED is false. Set it to true before initializing schema.")
        return 1

    store = PostgresLongTermStore(POSTGRES_CONFIG)
    if not store.available:
        print("psycopg is not installed or PostgreSQL backend is unavailable.")
        return 1

    store.ensure_schema()
    print("PostgreSQL schema initialized successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
