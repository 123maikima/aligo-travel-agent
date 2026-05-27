"""
行程规划智能体 ItineraryPlanningAgent
"""
from __future__ import annotations

from agentscope.agent import AgentBase
from agentscope.message import Msg
from typing import Optional, Union, List, Dict, Any
import json
import logging
from datetime import datetime, timedelta
import re

logger = logging.getLogger(__name__)


class ItineraryPlanningAgent(AgentBase):
    """基于事项收集结果生成可执行的行程规划。"""

    CITY_TEMPLATES = {
        "北京": [
            ("故宫博物院", "历史文化之旅"),
            ("天安门广场", "城市地标打卡"),
            ("颐和园", "皇家园林漫游"),
        ],
        "上海": [
            ("外滩", "城市天际线观景"),
            ("豫园", "老城厢文化游览"),
            ("南京路步行街", "购物与城市漫步"),
        ],
        "杭州": [
            ("西湖", "湖滨慢游"),
            ("灵隐寺", "人文禅意体验"),
            ("河坊街", "宋韵市井风情"),
        ],
        "深圳": [
            ("深圳湾公园", "海湾城市漫步"),
            ("华侨城", "都市休闲体验"),
            ("大梅沙海滨公园", "海边放松"),
        ],
        "成都": [
            ("宽窄巷子", "城市慢生活"),
            ("锦里", "川味文化体验"),
            ("成都大熊猫繁育研究基地", "城市名片打卡"),
        ],
    }

    def __init__(self, name: str = "ItineraryPlanningAgent", model=None, memory_manager=None, **kwargs):
        super().__init__()
        self.name = name
        self.model = model
        self.memory_manager = memory_manager

    async def reply(self, x: Optional[Union[Msg, List[Msg]]] = None) -> Msg:
        if x is None:
            return Msg(name=self.name, content=json.dumps({"itinerary": {}, "planning_complete": False}, ensure_ascii=False), role="assistant")

        input_content = x[-1].content if isinstance(x, list) else x.content
        query, context, previous_results = self._extract_payload(input_content)
        event_data = self._extract_event_data(previous_results, context)
        preferences = self._extract_preferences(context)

        itinerary = self._build_itinerary(query, event_data, preferences)
        destination = str(itinerary.get("route", "")).split("->")[-1].strip()
        planning_complete = bool(destination) and destination != "目的地" and "待确认" not in itinerary.get("route", "")
        result = {
            "status": "success",
            # 只有明确识别到目的地时，才认为行程规划完成。
            "planning_complete": planning_complete,
            "itinerary": itinerary,
            "missing_info": itinerary.get("missing_info", []),
        }
        return Msg(name=self.name, content=json.dumps(result, ensure_ascii=False), role="assistant")

    def _extract_payload(self, content: Any) -> tuple[str, Dict[str, Any], List[Dict[str, Any]]]:
        query = ""
        context: Dict[str, Any] = {}
        previous_results: List[Dict[str, Any]] = []

        if isinstance(content, dict):
            context = content.get("context", {}) if isinstance(content.get("context", {}), dict) else {}
            previous_results = content.get("previous_results", []) if isinstance(content.get("previous_results", []), list) else []
            query = str(context.get("rewritten_query") or context.get("query") or "").strip()
            if not query:
                query = str(content.get("query") or "").strip()
            return query, context, previous_results

        if isinstance(content, str):
            try:
                parsed = json.loads(content)
                return self._extract_payload(parsed)
            except Exception:
                return content.strip(), {}, []

        return str(content).strip(), {}, []

    def _extract_event_data(self, previous_results: List[Dict[str, Any]], context: Dict[str, Any]) -> Dict[str, Any]:
        for item in previous_results:
            if item.get("agent_name") == "event_collection":
                data = item.get("data") or item.get("result", {}).get("data", {})
                if isinstance(data, dict):
                    return data
        key_entities = context.get("key_entities", {})
        if isinstance(key_entities, dict):
            return {
                "origin": key_entities.get("origin", ""),
                "destination": key_entities.get("destination", ""),
                "start_date": key_entities.get("date", ""),
                "duration_days": self._parse_duration(key_entities.get("duration", "")),
                "trip_purpose": key_entities.get("other", "") or "出行",
            }
        return {}

    def _extract_preferences(self, context: Dict[str, Any]) -> Dict[str, Any]:
        prefs = context.get("user_preferences", {})
        return prefs if isinstance(prefs, dict) else {}

    def _parse_duration(self, duration: Any) -> int:
        if isinstance(duration, int):
            return max(duration, 1)
        text = str(duration or "")
        match = re.search(r"(\d+)", text)
        if match:
            return max(int(match.group(1)), 1)
        if "一日" in text or "1天" in text:
            return 1
        return 3

    def _infer_destination(self, query: str, event_data: Dict[str, Any]) -> str:
        destination = str(event_data.get("destination") or "").strip()
        if destination:
            return destination
        match = re.search(r"(?:去|到|前往|飞到|抵达)([^，。,.!?\s]{2,16})", query)
        if match:
            return match.group(1)
        return "目的地"

    def _infer_origin(self, query: str, event_data: Dict[str, Any]) -> str:
        origin = str(event_data.get("origin") or "").strip()
        if origin:
            return origin
        match = re.search(r"从([^，。,.!?\s]{2,16})", query)
        if match:
            return match.group(1)
        return ""

    def _infer_purpose(self, query: str, event_data: Dict[str, Any]) -> str:
        purpose = str(event_data.get("trip_purpose") or "").strip()
        if purpose:
            return purpose
        if "出差" in query:
            return "出差"
        if "旅游" in query or "旅行" in query:
            return "旅游"
        return "出行"

    def _parse_start_date(self, event_data: Dict[str, Any]) -> datetime:
        start_date = str(event_data.get("start_date") or "").strip()
        if start_date:
            try:
                return datetime.fromisoformat(start_date)
            except Exception:
                pass
        return datetime.now()

    def _template_for_city(self, city: str) -> List[tuple[str, str]]:
        for key, template in self.CITY_TEMPLATES.items():
            if key in city:
                return template
        return [
            (f"{city}核心商圈", "城市漫游"),
            (f"{city}地标景点", "经典打卡"),
            (f"{city}特色街区", "自由活动"),
        ]

    def _build_itinerary(self, query: str, event_data: Dict[str, Any], preferences: Dict[str, Any]) -> Dict[str, Any]:
        destination = self._infer_destination(query, event_data)
        origin = self._infer_origin(query, event_data)
        purpose = self._infer_purpose(query, event_data)
        duration_days = self._parse_duration(event_data.get("duration_days") or event_data.get("duration") or query)
        start_dt = self._parse_start_date(event_data)

        route = f"{origin or '出发地待确认'} -> {destination}"
        title = f"{destination}{duration_days}日{purpose}行程"
        template = self._template_for_city(destination)
        missing_info = []
        if not origin:
            missing_info.append("出发地")
        if not event_data.get("start_date"):
            missing_info.append("出发日期")

        hotel_brands = preferences.get("hotel_brands")
        airlines = preferences.get("airlines")
        notes = [
            f"本行程按{purpose}场景设计，优先覆盖 {destination} 的代表性地标和通勤便利性。",
            "出发前请再次确认景点开放时间、交通拥堵情况和门票预约要求。",
        ]
        if hotel_brands:
            notes.append(f"住宿可优先匹配偏好酒店：{hotel_brands}")
        if airlines:
            notes.append(f"航班可优先匹配偏好航司：{airlines}")

        daily_plans = []
        for day in range(1, duration_days + 1):
            activity_a = template[(day - 1) % len(template)]
            activity_b = template[day % len(template)]
            current_date = (start_dt + timedelta(days=day - 1)).strftime("%Y-%m-%d")

            if "出差" in purpose:
                theme = "商务差旅与城市轻游"
                activities = [
                    {
                        "time": "09:00-12:00",
                        "location": f"{destination}商务会见/会议地点",
                        "description": "安排当天主要工作会议和商务会面，优先靠近交通枢纽。",
                        "transport": "地铁/网约车",
                    },
                    {
                        "time": "13:30-17:30",
                        "location": activity_a[0],
                        "description": f"工作间隙可安排{activity_a[1]}，放松但不耽误工作节奏。",
                        "transport": "地铁/步行",
                    },
                    {
                        "time": "19:00-21:00",
                        "location": activity_b[0],
                        "description": "晚间可进行简短城市漫步或商务晚餐。",
                        "transport": "打车/步行",
                    },
                ]
            else:
                theme = activity_a[1]
                activities = [
                    {
                        "time": "09:00-12:00",
                        "location": activity_a[0],
                        "description": f"上午安排{activity_a[1]}。",
                        "transport": "地铁/步行",
                    },
                    {
                        "time": "13:30-17:00",
                        "location": activity_b[0],
                        "description": f"下午安排{activity_b[1]}。",
                        "transport": "地铁/打车",
                    },
                    {
                        "time": "19:00-21:00",
                        "location": f"{destination}特色餐饮区",
                        "description": "晚餐可体验本地特色餐饮，并安排休息。",
                        "transport": "打车/步行",
                    },
                ]

            daily_plans.append({
                "day": day,
                "date": current_date,
                "city": destination,
                "theme": theme,
                "activities": activities,
                "meals": {
                    "lunch": f"{destination}当地特色午餐",
                    "dinner": f"{destination}特色晚餐",
                },
            })

        estimated_budget = f"约{duration_days * (600 if '出差' in purpose else 450)}元"
        return {
            "title": title,
            "duration": f"{duration_days}天",
            "route": route,
            "daily_plans": daily_plans,
            "notes": notes,
            "estimated_budget": estimated_budget,
            "missing_info": missing_info,
        }
