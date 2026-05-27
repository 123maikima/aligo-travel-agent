"""
偏好管理智能体 PreferenceAgent
"""
from __future__ import annotations

from agentscope.agent import AgentBase
from agentscope.message import Msg
from typing import Optional, Union, List, Dict, Any
import json
import logging
import re

logger = logging.getLogger(__name__)


class PreferenceAgent(AgentBase):
    """识别用户偏好并输出结构化增量。"""

    HOTEL_BRANDS = [
        "汉庭", "如家", "全季", "亚朵", "万豪", "希尔顿", "洲际", "锦江之星",
        "7天", "维也纳", "格林豪泰", "桔子", "华住", "香格里拉", "凯悦",
    ]
    AIRLINES = ["国航", "东航", "南航", "海航", "厦航", "川航", "深航", "吉祥", "春秋", "首航"]
    SEAT_PREFS = ["靠窗", "靠过道", "中间座", "前排", "后排", "商务舱", "经济舱"]
    MEAL_PREFS = ["清淡", "素食", "低脂", "不吃辣", "少盐", "素餐", "清真"]
    TRANSPORT_PREFS = ["高铁", "飞机", "火车", "自驾", "大巴", "地铁"]
    BUDGET_PREFS = ["经济型", "舒适型", "商务型", "高端型", "预算有限", "预算充足"]

    def __init__(self, name: str = "PreferenceAgent", model=None, memory_manager=None, **kwargs):
        super().__init__()
        self.name = name
        self.model = model
        self.memory_manager = memory_manager

    async def reply(self, x: Optional[Union[Msg, List[Msg]]] = None) -> Msg:
        if x is None:
            return Msg(name=self.name, content=json.dumps({"preferences": [], "has_preferences": False}, ensure_ascii=False), role="assistant")

        input_content = x[-1].content if isinstance(x, list) else x.content
        query, context = self._extract_query_and_context(input_content)
        current_preferences = self._extract_current_preferences(context)

        preferences = self._extract_preferences(query, current_preferences)
        result = {
            "status": "success",
            "query": query,
            "preferences": preferences,
            "has_preferences": bool(preferences),
            "summary": self._build_summary(preferences),
        }
        return Msg(name=self.name, content=json.dumps(result, ensure_ascii=False), role="assistant")

    def _extract_query_and_context(self, content: Any) -> tuple[str, Dict[str, Any]]:
        if isinstance(content, dict):
            context = content.get("context", {})
            query = context.get("rewritten_query") or context.get("query") or ""
            return str(query).strip(), context if isinstance(context, dict) else {}

        if isinstance(content, str):
            try:
                parsed = json.loads(content)
                return self._extract_query_and_context(parsed)
            except Exception:
                return content.strip(), {}

        return str(content).strip(), {}

    def _extract_current_preferences(self, context: Dict[str, Any]) -> Dict[str, Any]:
        prefs = context.get("user_preferences", {})
        return prefs if isinstance(prefs, dict) else {}

    def _is_append_mode(self, query: str) -> bool:
        return any(key in query for key in ["还", "也", "另外", "以及", "再", "继续", "顺便"])

    def _is_replace_mode(self, query: str) -> bool:
        return any(key in query for key in ["搬家到", "改成", "换成", "现在住", "现在是", "变成", "更新为"])

    def _detect_pref_values(self, query: str, candidates: List[str]) -> List[str]:
        matched = []
        for item in candidates:
            if item and item in query and item not in matched:
                matched.append(item)
        return matched

    def _detect_home_location(self, query: str) -> str:
        patterns = [
            r"(?:家在|住在|搬家到|现在住在|现在住|常住在|常住|住到)([^，。,.!?\s]{2,24})",
            r"(?:我家在|我住在)([^，。,.!?\s]{2,24})",
        ]
        for pattern in patterns:
            match = re.search(pattern, query)
            if match:
                return match.group(1).strip()
        return ""

    def _build_pref_item(self, pref_type: str, value: Any, action: str) -> Dict[str, Any]:
        return {"type": pref_type, "value": value, "action": action}

    def _extract_preferences(self, query: str, current_preferences: Dict[str, Any]) -> List[Dict[str, Any]]:
        preferences: List[Dict[str, Any]] = []
        if not query:
            return preferences

        append_mode = self._is_append_mode(query) and not self._is_replace_mode(query)
        action = "append" if append_mode else "replace"

        hotel_values = self._detect_pref_values(query, self.HOTEL_BRANDS)
        if hotel_values:
            if len(hotel_values) == 1:
                preferences.append(self._build_pref_item("hotel_brands", hotel_values[0], action))
            else:
                preferences.append(self._build_pref_item("hotel_brands", hotel_values, action))

        airline_values = self._detect_pref_values(query, self.AIRLINES)
        if airline_values:
            if len(airline_values) == 1:
                preferences.append(self._build_pref_item("airlines", airline_values[0], action))
            else:
                preferences.append(self._build_pref_item("airlines", airline_values, action))

        seat_values = self._detect_pref_values(query, self.SEAT_PREFS)
        if seat_values:
            value = seat_values[0] if len(seat_values) == 1 else seat_values
            preferences.append(self._build_pref_item("seat_preference", value, action))

        meal_values = self._detect_pref_values(query, self.MEAL_PREFS)
        if meal_values:
            value = meal_values[0] if len(meal_values) == 1 else meal_values
            preferences.append(self._build_pref_item("meal_preference", value, action))

        transport_values = self._detect_pref_values(query, self.TRANSPORT_PREFS)
        if transport_values:
            value = transport_values[0] if len(transport_values) == 1 else transport_values
            preferences.append(self._build_pref_item("transportation_preference", value, action))

        budget_values = self._detect_pref_values(query, self.BUDGET_PREFS)
        if budget_values:
            value = budget_values[0] if len(budget_values) == 1 else budget_values
            preferences.append(self._build_pref_item("budget_level", value, action))

        home_location = self._detect_home_location(query)
        if home_location:
            preferences.append(self._build_pref_item("home_location", home_location, "replace"))

        # 如果用户明确在询问“偏好”，但没有命中关键词，则尝试从原句中抽取“喜欢/偏好 XX”
        if not preferences and any(key in query for key in ["喜欢", "偏好", "常坐", "常住", "想要"]):
            tail = re.sub(r".*(喜欢|偏好|常坐|常住|想要|想住|想坐)", "", query)
            tail = tail.strip("：:，,。.!? ")
            if tail:
                preferences.append(self._build_pref_item("other", tail, action))

        # 若上下文里已有偏好，但句子是“还/也...”之类，保持 append 语义。
        if preferences and append_mode:
            for item in preferences:
                if item["type"] == "home_location":
                    item["action"] = "replace"

        return preferences

    def _build_summary(self, preferences: List[Dict[str, Any]]) -> str:
        if not preferences:
            return "未识别到有效偏好。"
        parts = []
        for item in preferences:
            pref_type = item.get("type", "")
            value = item.get("value", "")
            action = item.get("action", "replace")
            parts.append(f"{pref_type}={value}({action})")
        return "；".join(parts)
