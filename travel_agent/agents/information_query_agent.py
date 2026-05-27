"""
信息查询智能体 InformationQueryAgent
支持天气查询与通用网络搜索。
"""
from __future__ import annotations

from agentscope.agent import AgentBase
from agentscope.message import Msg
from typing import Optional, Union, List, Dict, Any
import json
import logging
import os
import re
from urllib.parse import quote

logger = logging.getLogger(__name__)

try:
    import requests
except ImportError:  # pragma: no cover - 运行时依赖可选
    requests = None

try:
    from ddgs import DDGS
except ImportError:  # pragma: no cover - 运行时依赖可选
    DDGS = None


class InformationQueryAgent(AgentBase):
    """信息查询智能体：天气 + 通用搜索。"""

    WEATHER_KEYWORDS = ("天气", "气温", "温度", "预报", "下雨", "晴", "阴", "风力")
    SEARCH_KEYWORDS = ("搜索", "查一下", "查询", "了解", "最新", "新闻", "怎么", "为什么", "什么是")
    POLICY_KEYWORDS = ("差旅标准", "报销", "制度", "政策", "流程", "标准")

    def __init__(self, name: str = "InformationQueryAgent", model=None, memory_manager=None, **kwargs):
        super().__init__()
        self.name = name
        self.model = model
        self.memory_manager = memory_manager

    async def reply(self, x: Optional[Union[Msg, List[Msg]]] = None) -> Msg:
        if x is None:
            return Msg(name=self.name, content=json.dumps({"query_type": "网络搜索", "query_success": False, "results": {"error": "empty input"}}, ensure_ascii=False), role="assistant")

        input_content = x[-1].content if isinstance(x, list) else x.content
        query = self._extract_query(input_content)
        if not query:
            return Msg(
                name=self.name,
                content=json.dumps({
                    "query_type": "网络搜索",
                    "query_success": False,
                    "results": {"error": "无法获取查询内容", "summary": "", "sources": []},
                }, ensure_ascii=False),
                role="assistant",
            )

        if self._is_weather_query(query):
            result = self._query_weather(query)
        else:
            result = self._query_web(query)

        return Msg(name=self.name, content=json.dumps(result, ensure_ascii=False), role="assistant")

    def _extract_query(self, content: Any) -> str:
        if isinstance(content, dict):
            context = content.get("context", {})
            if isinstance(context, dict):
                query = context.get("rewritten_query") or context.get("query") or ""
                if query:
                    return str(query).strip()
            return str(content).strip()

        if isinstance(content, str):
            try:
                parsed = json.loads(content)
                return self._extract_query(parsed)
            except Exception:
                return content.strip()

        return str(content).strip()

    def _is_weather_query(self, query: str) -> bool:
        lowered = query.lower()
        if any(k in query for k in self.POLICY_KEYWORDS):
            return False
        return any(k in query for k in self.WEATHER_KEYWORDS) or "天气" in query or "weather" in lowered

    def _extract_location(self, query: str) -> str:
        query = re.sub(r"(今天|明天|后天|下周[一二三四五六日天]?)", "", query)
        query = re.sub(r"(的)?(天气|气温|温度|预报|怎么样|如何|如何天气|weather)", "", query, flags=re.IGNORECASE)
        query = re.sub(r"[？?。，,！!\s]", "", query)
        query = query.strip()
        return query or "当前位置"

    def _query_weather(self, query: str) -> Dict[str, Any]:
        location = self._extract_location(query)
        if requests is None:
            return {
                "query_type": "天气查询",
                "query_success": False,
                "results": {
                    "summary": "当前环境缺少 requests，无法查询天气。",
                    "sources": [],
                    "error": "requests not installed",
                },
            }

        try:
            url = f"https://wttr.in/{quote(location)}?format=j1"
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            data = resp.json()

            current = (data.get("current_condition") or [{}])[0]
            area_name = location
            if data.get("nearest_area"):
                area_name = data["nearest_area"][0].get("areaName", [{}])[0].get("value", location)

            temp_c = current.get("temp_C", "")
            feels_like = current.get("FeelsLikeC", "")
            desc = ""
            if current.get("weatherDesc"):
                desc = current["weatherDesc"][0].get("value", "")
            humidity = current.get("humidity", "")
            wind = current.get("windspeedKmph", "")

            forecast_lines = []
            for day in (data.get("weather") or [])[:3]:
                date = day.get("date", "")
                max_t = day.get("maxtempC", "")
                min_t = day.get("mintempC", "")
                summary = (day.get("hourly") or [{}])[0].get("weatherDesc", [{}])[0].get("value", "")
                if date:
                    forecast_lines.append(f"{date} 最高{max_t}°C / 最低{min_t}°C {summary}")

            summary = f"{area_name}当前{desc}，气温{temp_c}°C，体感{feels_like}°C，湿度{humidity}%，风速{wind}km/h。"
            if forecast_lines:
                summary += " 未来3天：" + "；".join(forecast_lines)

            return {
                "query_type": "天气查询",
                "query_success": True,
                "results": {
                    "summary": summary,
                    "sources": [{"title": "wttr.in", "url": url, "snippet": summary}],
                },
            }
        except Exception as e:
            logger.warning(f"Weather query failed: {e}")
            return {
                "query_type": "天气查询",
                "query_success": False,
                "results": {
                    "summary": "",
                    "sources": [],
                    "error": str(e),
                    "message": f"天气查询失败: {e}",
                },
            }

    def _query_web(self, query: str) -> Dict[str, Any]:
        if DDGS is None:
            return {
                "query_type": "网络搜索",
                "query_success": False,
                "results": {
                    "summary": "当前环境缺少 ddgs，无法执行网络搜索。",
                    "sources": [],
                    "error": "ddgs not installed",
                },
            }

        try:
            with DDGS() as ddgs:
                results = list(ddgs.text(query, max_results=5))

            sources = []
            snippets = []
            for item in results:
                title = item.get("title") or item.get("heading") or ""
                url = item.get("href") or item.get("url") or ""
                snippet = item.get("body") or item.get("snippet") or ""
                if snippet:
                    snippets.append(snippet)
                sources.append({"title": title, "url": url, "snippet": snippet})

            summary = self._build_search_summary(query, snippets)
            return {
                "query_type": "网络搜索",
                "query_success": True,
                "results": {
                    "summary": summary,
                    "sources": sources,
                },
            }
        except Exception as e:
            logger.warning(f"Web search failed: {e}")
            return {
                "query_type": "网络搜索",
                "query_success": False,
                "results": {
                    "summary": "",
                    "sources": [],
                    "error": str(e),
                    "message": f"网络搜索失败: {e}",
                },
            }

    def _build_search_summary(self, query: str, snippets: List[str]) -> str:
        if not snippets:
            return f"未检索到与「{query}」相关的可靠结果。"

        # 取前三条摘要，避免输出过长。
        concise = []
        for snippet in snippets[:3]:
            snippet = str(snippet).strip()
            if snippet:
                concise.append(snippet[:160])
        return "；".join(concise) if concise else f"已搜索「{query}」，但未找到足够摘要信息。"
