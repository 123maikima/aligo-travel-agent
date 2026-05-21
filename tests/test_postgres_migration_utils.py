#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
PostgreSQL 迁移脚本纯工具测试
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from scripts.migrate_json_to_postgres import iter_snapshot_files, load_snapshot


def test_iter_snapshot_files_and_load_snapshot():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        payload = {
            "user_id": "user_001",
            "preferences": [{"type": "hotel_brands", "value": ["汉庭"]}],
            "chat_history": [],
            "trip_history": [],
            "statistics": {"total_trips": 0, "total_messages": 0, "total_queries": 0, "frequent_destinations": {}},
        }
        sample = root / "user_001.json"
        sample.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

        files = list(iter_snapshot_files(tmpdir))
        assert len(files) == 1
        assert files[0].name == "user_001.json"

        user_id, data = load_snapshot(sample)
        assert user_id == "user_001"
        assert data["preferences"][0]["type"] == "hotel_brands"
        print("[PASS] test_iter_snapshot_files_and_load_snapshot")


if __name__ == "__main__":
    print("=" * 60)
    print("PostgreSQL 迁移脚本工具测试")
    print("=" * 60)

    test_iter_snapshot_files_and_load_snapshot()

    print("\n" + "=" * 60)
    print("全部测试完成")
    print("=" * 60)
