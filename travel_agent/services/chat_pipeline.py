"""
Shared chat execution pipeline for CLI and Web API.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any, AsyncIterator, Dict, List, Optional

from agentscope.message import Msg

from travel_agent.agents.intention_agent import IntentionAgent
from travel_agent.agents.lazy_agent_registry import LazyAgentRegistry
from travel_agent.agents.orchestration_agent import OrchestrationAgent
from travel_agent.config import POSTGRES_CONFIG, REDIS_CONFIG, RESILIENCE_CONFIG
from travel_agent.context.memory_manager import MemoryManager
from travel_agent.context.redis_cache import RedisCache
from travel_agent.llm import create_model_factory
from travel_agent.observability import TraceContext
from travel_agent.utils.circuit_breaker import CircuitBreaker, CircuitOpenError
from travel_agent.utils.llm_resilience import retry_with_backoff

logger = logging.getLogger(__name__)


class ChatPipeline:
    """Encapsulate the core query flow used by CLI and Web."""

    def __init__(
        self,
        model,
        intention_agent: Optional[IntentionAgent] = None,
        redis_cache: Optional[RedisCache] = None,
        circuit_breaker: Optional[CircuitBreaker] = None,
        agent_cache: Optional[Dict[str, Any]] = None,
        model_factory=None,
        storage_path: str = "data/memory",
    ):
        self.model = model
        self.model_factory = model_factory or create_model_factory()
        self.intention_agent = intention_agent or IntentionAgent(
            name="IntentionAgent",
            model=self.model_factory("intention_agent"),
        )
        self.redis_cache = redis_cache
        self.circuit_breaker = circuit_breaker
        self.agent_cache = agent_cache if agent_cache is not None else {}
        self.storage_path = storage_path

    def _create_memory_manager(self, user_id: str, session_id: str) -> MemoryManager:
        return MemoryManager(
            user_id=user_id,
            session_id=session_id,
            storage_path=self.storage_path,
            llm_model=self.model_factory("memory_summary"),
            redis_cache=self.redis_cache,
            postgres_config=POSTGRES_CONFIG,
        )

    def _create_orchestrator(self, memory_manager: MemoryManager, event_callback=None) -> OrchestrationAgent:
        lazy_registry = LazyAgentRegistry(
            model=self.model,
            cache=self.agent_cache,
            memory_manager=memory_manager,
            model_factory=self.model_factory,
        )
        return OrchestrationAgent(
            name="OrchestrationAgent",
            agent_registry=lazy_registry,
            memory_manager=memory_manager,
            event_callback=event_callback,
        )

    async def _get_long_term_summary(
        self,
        memory_manager: MemoryManager,
        user_input: str = "",
    ) -> str:
        summary_parts: List[str] = []

        prefs = memory_manager.long_term.get_preference()
        if prefs:
            pref_lines = ["【用户背景信息】（来自长期记忆，可用于推断缺失信息）"]
            for pref_key, pref_value in prefs.items():
                if not pref_value:
                    continue
                if isinstance(pref_value, list):
                    pref_lines.append(f"• {pref_key}: {', '.join(pref_value)}")
                else:
                    pref_lines.append(f"• {pref_key}: {pref_value}")
            if len(pref_lines) > 1:
                summary_parts.extend(pref_lines)

        chat_summary = await memory_manager.get_long_term_summary_async(max_messages=50)
        if chat_summary:
            summary_parts.append("\n【历史会话总结】")
            summary_parts.append(chat_summary)

        all_trips = memory_manager.long_term.get_trip_history(limit=None)
        if all_trips:
            relevant_trips: List[Dict[str, Any]] = []
            other_trips: List[Dict[str, Any]] = []
            for trip in all_trips:
                origin = trip.get("origin", "") or ""
                destination = trip.get("destination", "") or ""
                if (origin and origin in user_input) or (destination and destination in user_input):
                    relevant_trips.append(trip)
                else:
                    other_trips.append(trip)

            trips_to_show = relevant_trips[:2] + other_trips[:1]
            if trips_to_show:
                summary_parts.append("\n【历史行程】")
                for i, trip in enumerate(trips_to_show[:3], 1):
                    origin = trip.get("origin", "未知")
                    destination = trip.get("destination", "未知")
                    start_date = trip.get("start_date", "")
                    purpose = trip.get("purpose", "")
                    relevance_mark = "✦ " if trip in relevant_trips else ""
                    summary_parts.append(
                        f"{i}. {relevance_mark}{origin} → {destination} ({start_date}) - {purpose}"
                    )

        return "\n".join(summary_parts) if summary_parts else ""

    def _build_context_messages(
        self,
        memory_manager: MemoryManager,
        user_input: str,
        long_term_summary: str,
    ) -> List[Msg]:
        recent_context = memory_manager.short_term.get_recent_context(n_turns=5)
        context_messages: List[Msg] = []
        if long_term_summary:
            context_messages.append(Msg(name="system", content=long_term_summary, role="system"))
        for msg in recent_context:
            context_messages.append(Msg(name=msg["role"], content=msg["content"], role=msg["role"]))
        context_messages.append(Msg(name="user", content=user_input, role="user"))
        return context_messages

    def _extract_agents_executed(self, result_data: Dict[str, Any]) -> List[str]:
        return [r.get("agent_name", "") for r in result_data.get("results", []) if r.get("agent_name")]

    def _build_human_summary(self, result_data: Dict[str, Any]) -> str:
        results = result_data.get("results", [])
        if not results:
            status = result_data.get("status", "unknown")
            if status == "no_agents":
                return "好的，我已记录下来。你可以继续补充信息，或者直接让我帮你规划行程、查信息或问知识库。"
            return "未能获取有效结果，请重新描述您的需求。"

        lines: List[str] = []
        for result in results:
            agent_name = result.get("agent_name", "")
            status = result.get("status", "")
            data = result.get("data", {}) if isinstance(result.get("data", {}), dict) else {}

            if status == "error":
                error_msg = data.get("error", "未知错误")
                lines.append(f"{agent_name}: {error_msg}")
                continue

            if agent_name == "itinerary_planning":
                itinerary = data.get("itinerary") or data.get("data", {}).get("itinerary")
                if isinstance(itinerary, dict):
                    title = itinerary.get("title", "行程规划")
                    duration = itinerary.get("duration", "未知")
                    lines.append(f"{title}，时长 {duration}")
                    continue

            if agent_name == "preference":
                prefs = data.get("preferences") or data.get("data", {}).get("preferences")
                if isinstance(prefs, list) and prefs:
                    lines.append("已更新您的偏好设置。")
                    continue

            if agent_name == "event_collection":
                origin = data.get("origin") or data.get("data", {}).get("origin")
                destination = data.get("destination") or data.get("data", {}).get("destination")
                if origin or destination:
                    lines.append(f"已收集行程信息：{origin or '未知'} → {destination or '未知'}。")
                    continue

            if agent_name == "information_query":
                summary = data.get("summary") or data.get("data", {}).get("summary")
                message = data.get("message") or data.get("data", {}).get("message")
                if summary:
                    lines.append(summary)
                    continue
                if message:
                    lines.append(message)
                    continue

            if agent_name == "rag_knowledge":
                answer = data.get("answer") or data.get("data", {}).get("answer")
                if answer:
                    lines.append(str(answer))
                    continue

            if agent_name == "memory_query":
                answer = data.get("answer") or data.get("result") or data.get("content")
                if answer:
                    lines.append(str(answer))
                    continue

            generic = data.get("answer") or data.get("content") or data.get("result") or data.get("message")
            if generic:
                lines.append(str(generic))

        return "\n".join(lines).strip() or "已处理您的请求。"

    async def execute(
        self,
        user_input: str,
        user_id: str = "default_user",
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        session_id = session_id or str(uuid.uuid4())[:8]
        trace = TraceContext(user_id=user_id, session_id=session_id, query=user_input)
        trace.emit("trace_start", "request", {"query": user_input, "stream": False})
        memory_manager = self._create_memory_manager(user_id, session_id)

        async def on_agent_event(payload: Dict[str, Any]):
            event_name = payload.get("event", "agent_event")
            data = payload.get("data", {})
            trace.record_agent_event(event_name, data)

        orchestrator = self._create_orchestrator(memory_manager, event_callback=on_agent_event)

        if self.circuit_breaker:
            self.circuit_breaker.raise_if_open()

        rc = RESILIENCE_CONFIG
        max_retries = rc.get("max_retries", 3)

        try:
            long_term_summary = await self._get_long_term_summary(memory_manager, user_input)
            context_messages = self._build_context_messages(memory_manager, user_input, long_term_summary)
            trace.emit("context_loaded", "context", {
                "has_long_term_summary": bool(long_term_summary),
                "context_messages": len(context_messages),
            })

            intention_result = await retry_with_backoff(
                lambda: self.intention_agent.reply(context_messages),
                max_retries=max_retries,
                base_delay_sec=rc.get("retry_base_delay_sec", 1.0),
                max_delay_sec=rc.get("retry_max_delay_sec", 30.0),
            )
            if self.circuit_breaker:
                self.circuit_breaker.record_success()

            intention_data = json.loads(intention_result.content)
            trace.emit("intent_done", "intent", {
                "intents": intention_data.get("intents", []),
                "agent_schedule": intention_data.get("agent_schedule", []),
            })

            memory_manager.add_message("user", user_input)

            orchestration_result = await retry_with_backoff(
                lambda: orchestrator.reply(intention_result),
                max_retries=max_retries,
                base_delay_sec=rc.get("retry_base_delay_sec", 1.0),
                max_delay_sec=rc.get("retry_max_delay_sec", 30.0),
            )
            if self.circuit_breaker:
                self.circuit_breaker.record_success()

            result_data = json.loads(orchestration_result.content)
        except json.JSONDecodeError as exc:
            if self.circuit_breaker:
                self.circuit_breaker.record_failure()
            trace.finish("error", error=f"无法解析结果: {exc}")
            raise ValueError(f"无法解析执行结果: {exc}") from exc
        except Exception as exc:
            if self.circuit_breaker:
                self.circuit_breaker.record_failure()
            trace.finish("error", error=str(exc))
            raise

        memory_manager.add_message("assistant", json.dumps(result_data, ensure_ascii=False))
        metrics = trace.finish("success", intention_data=intention_data, result_data=result_data)

        return {
            "trace_id": trace.trace_id,
            "user_id": user_id,
            "session_id": session_id,
            "query": user_input,
            "intention_data": intention_data,
            "result_data": result_data,
            "agents_executed": self._extract_agents_executed(result_data),
            "human_response": self._build_human_summary(result_data),
            "long_term_summary": long_term_summary,
            "metrics": metrics,
        }

    async def stream_execute(
        self,
        user_input: str,
        user_id: str = "default_user",
        session_id: Optional[str] = None,
    ) -> AsyncIterator[Dict[str, Any]]:
        session_id = session_id or str(uuid.uuid4())[:8]
        trace = TraceContext(user_id=user_id, session_id=session_id, query=user_input)
        trace.emit("trace_start", "request", {"query": user_input, "stream": True})
        memory_manager = self._create_memory_manager(user_id, session_id)
        event_queue: asyncio.Queue[Dict[str, Any]] = asyncio.Queue()

        async def on_agent_event(payload: Dict[str, Any]):
            event_name = payload.get("event", "agent_event")
            data = payload.get("data", {})
            trace.record_agent_event(event_name, data)
            payload.setdefault("data", {})
            payload["data"]["trace_id"] = trace.trace_id
            await event_queue.put(payload)

        orchestrator = self._create_orchestrator(memory_manager, event_callback=on_agent_event)

        if self.circuit_breaker:
            self.circuit_breaker.raise_if_open()

        rc = RESILIENCE_CONFIG
        max_retries = rc.get("max_retries", 3)

        yield {
            "event": "status",
            "data": {
                "stage": "context",
                "message": "正在加载上下文",
                "session_id": session_id,
                "trace_id": trace.trace_id,
            },
        }

        long_term_summary = await self._get_long_term_summary(memory_manager, user_input)
        context_messages = self._build_context_messages(memory_manager, user_input, long_term_summary)
        trace.emit("context_loaded", "context", {
            "has_long_term_summary": bool(long_term_summary),
            "context_messages": len(context_messages),
        })

        yield {
            "event": "status",
            "data": {
                "stage": "intent",
                "message": "正在进行意图识别",
                "trace_id": trace.trace_id,
            },
        }

        intention_result = await retry_with_backoff(
            lambda: self.intention_agent.reply(context_messages),
            max_retries=max_retries,
            base_delay_sec=rc.get("retry_base_delay_sec", 1.0),
            max_delay_sec=rc.get("retry_max_delay_sec", 30.0),
        )
        if self.circuit_breaker:
            self.circuit_breaker.record_success()

        try:
            intention_data = json.loads(intention_result.content)
        except json.JSONDecodeError as exc:
            if self.circuit_breaker:
                self.circuit_breaker.record_failure()
            raise ValueError(f"无法解析意图识别结果: {exc}") from exc

        yield {
            "event": "intent",
            "data": {**intention_data, "trace_id": trace.trace_id},
        }
        trace.emit("intent_done", "intent", {
            "intents": intention_data.get("intents", []),
            "agent_schedule": intention_data.get("agent_schedule", []),
        })

        memory_manager.add_message("user", user_input)

        yield {
            "event": "status",
            "data": {
                "stage": "orchestration",
                "message": "正在调度子智能体",
                "trace_id": trace.trace_id,
            },
        }

        orchestration_task = asyncio.create_task(retry_with_backoff(
            lambda: orchestrator.reply(intention_result),
            max_retries=max_retries,
            base_delay_sec=rc.get("retry_base_delay_sec", 1.0),
            max_delay_sec=rc.get("retry_max_delay_sec", 30.0),
        ))

        while True:
            if orchestration_task.done() and event_queue.empty():
                break
            try:
                event = await asyncio.wait_for(event_queue.get(), timeout=0.1)
                yield event
            except asyncio.TimeoutError:
                continue

        orchestration_result = await orchestration_task
        if self.circuit_breaker:
            self.circuit_breaker.record_success()

        try:
            result_data = json.loads(orchestration_result.content)
        except json.JSONDecodeError as exc:
            if self.circuit_breaker:
                self.circuit_breaker.record_failure()
            trace.finish("error", intention_data=intention_data, error=f"无法解析执行结果: {exc}")
            raise ValueError(f"无法解析执行结果: {exc}") from exc

        human_response = self._build_human_summary(result_data)
        memory_manager.add_message("assistant", json.dumps(result_data, ensure_ascii=False))
        metrics = trace.finish("success", intention_data=intention_data, result_data=result_data)

        chunks = [part.strip() for part in human_response.split("\n") if part.strip()]
        if not chunks:
            chunks = [human_response]
        for index, chunk in enumerate(chunks):
            yield {
                "event": "chunk",
                "data": {
                    "content": chunk + ("\n" if index < len(chunks) - 1 else ""),
                    "index": index,
                    "trace_id": trace.trace_id,
                },
            }
        yield {
            "event": "done",
            "data": {
                "status": "success",
                "session_id": session_id,
                "trace_id": trace.trace_id,
                "metrics": metrics,
                "result": {
                    "trace_id": trace.trace_id,
                    "user_id": user_id,
                    "session_id": session_id,
                    "query": user_input,
                    "intention_data": intention_data,
                    "result_data": result_data,
                    "agents_executed": self._extract_agents_executed(result_data),
                    "human_response": human_response,
                    "long_term_summary": long_term_summary,
                    "metrics": metrics,
                },
            },
        }
