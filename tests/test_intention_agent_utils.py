#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
IntentionAgent 纯工具逻辑测试

不依赖 LLM / 网络，覆盖本次新增的：
- legacy agent 名称归一化
- 规则回退意图识别
- 规则回退的调度去重
- 关键实体和 rewritten_query 生成
"""
import os
import sys

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from travel_agent.agents.intention_agent import IntentionAgent


def test_legacy_agent_name_normalization():
    agent = IntentionAgent(model=None)

    assert agent._normalize_agent_name("plan-trip") == "itinerary_planning"
    assert agent._normalize_agent_name("ask-question") == "rag_knowledge"
    assert agent._normalize_agent_name("preference") == "preference"

    print("[PASS] test_legacy_agent_name_normalization")


def test_rule_based_fallback_for_trip_query():
    agent = IntentionAgent(model=None)

    result = agent._build_fallback_result(
        "我想从北京去上海出差三天",
        "无历史对话",
        "test"
    )

    assert result["intents"][0]["type"] in {"event_collection", "itinerary_planning"}
    assert any(item["agent_name"] == "event_collection" for item in result["agent_schedule"])
    assert any(item["agent_name"] == "itinerary_planning" for item in result["agent_schedule"])
    assert len([item for item in result["agent_schedule"] if item["agent_name"] == "event_collection"]) == 1
    assert len([item for item in result["agent_schedule"] if item["agent_name"] == "itinerary_planning"]) == 1
    assert result["rewritten_query"].startswith("我想从北京去上海出差三天")
    assert result["key_entities"]["duration"] == "三天" or result["key_entities"]["duration"] == ""

    print("[PASS] test_rule_based_fallback_for_trip_query")


def test_rule_based_memory_query():
    agent = IntentionAgent(model=None)

    result = agent._build_fallback_result(
        "我之前去过哪些地方？",
        "无历史对话",
        "test"
    )

    assert result["intents"][0]["type"] == "memory_query"
    assert any(item["agent_name"] == "memory_query" for item in result["agent_schedule"])

    print("[PASS] test_rule_based_memory_query")


if __name__ == "__main__":
    print("=" * 60)
    print("IntentionAgent 工具测试")
    print("=" * 60)

    test_legacy_agent_name_normalization()
    test_rule_based_fallback_for_trip_query()
    test_rule_based_memory_query()

    print("\n" + "=" * 60)
    print("全部测试完成")
    print("=" * 60)
