#!/usr/bin/env python
"""
将 data/memory 下的 JSON 长期记忆批量迁移到 PostgreSQL。

特性：
- 幂等：以用户完整快照为单位覆盖写入
- 支持 dry-run，先查看会迁移哪些用户
- 支持指定 storage_path
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Iterable, Tuple

project_root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(project_root))

from travel_agent.config import POSTGRES_CONFIG
from travel_agent.context.postgres_storage import PostgresLongTermStore


def iter_snapshot_files(storage_path: str) -> Iterable[Path]:
    root = Path(storage_path)
    if not root.exists():
        return []
    return sorted(root.glob("*.json"))


def load_snapshot(path: Path) -> Tuple[str, dict]:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    user_id = str(data.get("user_id") or path.stem).strip()
    if not user_id:
        user_id = path.stem
    return user_id, data


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Migrate JSON long-term memory snapshots to PostgreSQL.")
    parser.add_argument("--storage-path", default="data/memory", help="JSON snapshot directory")
    parser.add_argument("--dry-run", action="store_true", help="Only print the files that would be migrated")
    parser.add_argument("--limit", type=int, default=0, help="Limit number of files to migrate")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not POSTGRES_CONFIG.get("enabled", False):
        print("POSTGRES_ENABLED is false. Set it to true before running migration.")
        return 1

    store = PostgresLongTermStore(POSTGRES_CONFIG)
    if not store.available:
        print("psycopg is not installed or PostgreSQL backend is unavailable.")
        return 1

    store.ensure_schema()

    files = list(iter_snapshot_files(args.storage_path))
    if args.limit and args.limit > 0:
        files = files[: args.limit]

    if not files:
        print(f"No JSON snapshots found in {args.storage_path}")
        return 0

    migrated = 0
    skipped = 0
    for path in files:
        try:
            user_id, data = load_snapshot(path)
        except Exception as e:
            skipped += 1
            print(f"[SKIP] {path.name}: failed to load JSON ({e})")
            continue

        if args.dry_run:
            print(f"[DRY-RUN] would migrate {user_id} from {path}")
            migrated += 1
            continue

        try:
            store.save_snapshot(user_id, data)
            migrated += 1
            print(f"[OK] migrated {user_id} from {path.name}")
        except Exception as e:
            skipped += 1
            print(f"[FAIL] {user_id} ({path.name}): {e}")

    print(f"Finished. migrated={migrated}, skipped={skipped}")
    return 0 if skipped == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
