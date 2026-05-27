#!/usr/bin/env python
"""Summarize local observability JSONL metrics."""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

project_root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(project_root))

from travel_agent.config import OBSERVABILITY_CONFIG


def load_jsonl(path: Path):
    if not path.exists():
        return []
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def main() -> int:
    metrics_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(OBSERVABILITY_CONFIG["metrics_log"])
    rows = load_jsonl(metrics_path)
    if not rows:
        print(f"No metrics found: {metrics_path}")
        return 1

    by_agent = defaultdict(lambda: {"calls": 0, "success": 0, "errors": 0, "duration": 0})
    by_session = defaultdict(lambda: {"traces": 0, "errors": 0, "duration": 0})
    by_user = defaultdict(lambda: {"traces": 0, "errors": 0, "duration": 0})

    for row in rows:
        duration = int(row.get("duration_ms") or 0)
        errored = row.get("status") != "success"
        session = row.get("session_id") or "unknown"
        user = row.get("user_id") or "unknown"
        by_session[session]["traces"] += 1
        by_session[session]["errors"] += int(errored)
        by_session[session]["duration"] += duration
        by_user[user]["traces"] += 1
        by_user[user]["errors"] += int(errored)
        by_user[user]["duration"] += duration

        for agent, stats in (row.get("agent_metrics") or {}).items():
            by_agent[agent]["calls"] += int(stats.get("calls") or 0)
            by_agent[agent]["success"] += int(stats.get("success") or 0)
            by_agent[agent]["errors"] += int(stats.get("errors") or 0)
            by_agent[agent]["duration"] += int(stats.get("total_duration_ms") or 0)

    print(f"Metrics: {metrics_path}")
    print(f"Traces: {len(rows)}")
    print("\nAgent metrics")
    for agent, stats in sorted(by_agent.items()):
        calls = stats["calls"] or 1
        print(
            f"- {agent}: calls={stats['calls']} success={stats['success']} "
            f"errors={stats['errors']} avg_ms={round(stats['duration'] / calls, 2)}"
        )

    print("\nSession metrics")
    for session, stats in sorted(by_session.items()):
        traces = stats["traces"] or 1
        print(
            f"- {session}: traces={stats['traces']} errors={stats['errors']} "
            f"avg_ms={round(stats['duration'] / traces, 2)}"
        )

    print("\nUser metrics")
    for user, stats in sorted(by_user.items()):
        traces = stats["traces"] or 1
        print(
            f"- {user}: traces={stats['traces']} errors={stats['errors']} "
            f"avg_ms={round(stats['duration'] / traces, 2)}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
