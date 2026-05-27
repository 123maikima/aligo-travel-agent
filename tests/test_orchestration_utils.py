#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
OrchestrationAgent 纯工具测试
"""
import os
import sys

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from travel_agent.agents.orchestration_agent import OrchestrationAgent


def test_group_tasks_by_priority():
    agent = OrchestrationAgent()
    tasks = [
        {"agent_name": "information_query", "priority": 1},
        {"agent_name": "event_collection", "priority": 1},
        {"agent_name": "itinerary_planning", "priority": 2},
    ]

    grouped = agent._group_tasks_by_priority(tasks)
    assert grouped[0][0] == 1
    assert [t["agent_name"] for t in grouped[0][1]] == ["information_query", "event_collection"]
    assert grouped[1][0] == 2
    assert [t["agent_name"] for t in grouped[1][1]] == ["itinerary_planning"]

    print("[PASS] test_group_tasks_by_priority")


def test_execution_summary_format():
    agent = OrchestrationAgent()
    results = [
        {"agent_name": "event_collection", "priority": 1, "result": {"status": "success"}},
        {"agent_name": "itinerary_planning", "priority": 2, "result": {"status": "error"}},
    ]
    batches = [
        {"priority": 1, "agents": ["event_collection"], "count": 1},
        {"priority": 2, "agents": ["itinerary_planning"], "count": 1},
    ]

    summary = agent._build_execution_summary(results, batches)
    assert summary["total"] == 2
    assert summary["success"] == 1
    assert summary["error"] == 1
    assert summary["text"] == "P1: event_collection | P2: itinerary_planning"

    print("[PASS] test_execution_summary_format")


if __name__ == "__main__":
    print("=" * 60)
    print("OrchestrationAgent 工具测试")
    print("=" * 60)

    test_group_tasks_by_priority()
    test_execution_summary_format()

    print("\n" + "=" * 60)
    print("全部测试完成")
    print("=" * 60)
