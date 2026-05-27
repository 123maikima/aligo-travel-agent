#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
新 Agent 纯工具测试
"""
import os
import sys

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from travel_agent.agents.information_query_agent import InformationQueryAgent
from travel_agent.agents.preference_agent import PreferenceAgent
from travel_agent.agents.itinerary_planning_agent import ItineraryPlanningAgent


def test_information_query_helpers():
    agent = InformationQueryAgent()
    assert agent._is_weather_query("杭州明天天气怎么样")
    assert not agent._is_weather_query("差旅标准是多少")
    assert agent._extract_location("杭州明天天气怎么样") == "杭州"
    print("[PASS] test_information_query_helpers")


def test_preference_extraction():
    agent = PreferenceAgent()
    prefs = agent._extract_preferences("我还喜欢汉庭酒店和东航，座位要靠窗", {})
    types = [item["type"] for item in prefs]
    assert "hotel_brands" in types
    assert "airlines" in types
    assert "seat_preference" in types
    print("[PASS] test_preference_extraction")


def test_itinerary_builder():
    agent = ItineraryPlanningAgent()
    itinerary = agent._build_itinerary(
        "我要去杭州出差3天",
        {"origin": "上海", "destination": "杭州", "start_date": "2026-05-20", "duration_days": 3, "trip_purpose": "出差"},
        {"hotel_brands": ["汉庭"]},
    )
    assert itinerary["route"] == "上海 -> 杭州"
    assert itinerary["duration"] == "3天"
    assert len(itinerary["daily_plans"]) == 3
    fallback = agent._build_itinerary("安排一个行程", {}, {})
    assert fallback["route"].endswith("目的地")
    print("[PASS] test_itinerary_builder")


if __name__ == "__main__":
    print("=" * 60)
    print("新 Agent 工具测试")
    print("=" * 60)

    test_information_query_helpers()
    test_preference_extraction()
    test_itinerary_builder()

    print("\n" + "=" * 60)
    print("全部测试完成")
    print("=" * 60)
