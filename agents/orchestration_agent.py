"""
协调器智能体 OrchestrationAgent
职责：根据意图识别结果，协调调度多个子智能体完成任务

核心功能：
1. 接收 IntentionAgent 的调度决策
2. 按优先级分组，组内并行执行，组间串行
3. 管理智能体之间的消息传递
4. 聚合多个智能体的结果
5. 与记忆系统集成

执行模式：
- 按优先级分组，每组内并行执行（asyncio.gather）
- 不同优先级组间串行执行（前一组结果作为后一组的上下文）
"""
from agentscope.agent import AgentBase
from agentscope.message import Msg
from typing import Optional, Union, List, Dict, Any, Protocol
import json
import logging
import asyncio

from context.memory_updater import MemoryUpdater

logger = logging.getLogger(__name__)


class AgentRegistry(Protocol):
    """Agent 注册表接口，支持 dict 和 LazyAgentRegistry"""
    def __contains__(self, name: str) -> bool: ...
    def __getitem__(self, name: str) -> AgentBase: ...
    def get(self, name: str, default=None) -> Optional[AgentBase]: ...


# 单个 Agent 执行超时时间（秒）
AGENT_TIMEOUT_SEC = 60.0


class OrchestrationAgent(AgentBase):
    """协调器智能体 - 调度和协调多个子智能体"""

    def __init__(
        self,
        name: str = "OrchestrationAgent",
        agent_registry: AgentRegistry = None,
        memory_manager = None,
        agent_timeout_sec: float = AGENT_TIMEOUT_SEC,
        **kwargs
    ):
        super().__init__()
        self.name = name
        self.agent_registry = agent_registry or {}
        self.memory_manager = memory_manager
        self.agent_timeout_sec = agent_timeout_sec

    async def reply(self, x: Optional[Union[Msg, List[Msg]]] = None) -> Msg:
        """
        协调执行流程

        按优先级分组调度：
        - 同优先级的 Agent 并行执行（asyncio.gather）
        - 不同优先级按顺序执行，前一组结果作为后一组的上下文

        Args:
            x: 输入消息，包含 IntentionAgent 的输出

        Returns:
            Msg: 执行结果
        """
        if x is None:
            return Msg(name=self.name, content=json.dumps({"error": "No input provided"}), role="assistant")

        # 解析输入
        if isinstance(x, list):
            intention_output = x[-1].content if x else "{}"
        else:
            intention_output = x.content
        try:
            intention_data = json.loads(intention_output) if isinstance(intention_output, str) else intention_output
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse intention output: {e}")
            return Msg(name=self.name, content=json.dumps({"error": "Invalid intention format"}), role="assistant")

        # 获取并排序调度计划
        agent_schedule = intention_data.get("agent_schedule", [])
        if not agent_schedule:
            return Msg(name=self.name, content=json.dumps({"status": "no_agents", "message": "没有需要调度的智能体"}), role="assistant")

        sorted_schedule = sorted(agent_schedule, key=lambda t: t.get("priority", 999))

        # 按优先级分组执行：组内并行，组间串行
        all_results = []
        priority_batches = self._group_by_priority(sorted_schedule)

        for priority, batch_tasks in priority_batches:
            logger.info(f"Executing priority {priority} batch: {[t['agent_name'] for t in batch_tasks]}")
            batch_results = await self._execute_parallel_batch(batch_tasks, intention_data, all_results)
            all_results.extend(batch_results)

        # 聚合结果 + 更新记忆
        final_result = self._aggregate_results(all_results, intention_data)
        if self.memory_manager:
            MemoryUpdater.update(self.memory_manager, all_results)

        return Msg(name=self.name, content=json.dumps(final_result, ensure_ascii=False), role="assistant")

    @staticmethod
    def _group_by_priority(sorted_schedule: List[Dict]) -> List[tuple]:
        """按优先级分组，返回 [(priority, [tasks]), ...]"""
        if not sorted_schedule:
            return []

        batches = []
        current_priority = None
        current_batch = []

        for task in sorted_schedule:
            priority = task.get("priority", 0)
            if priority != current_priority:
                if current_batch:
                    batches.append((current_priority, current_batch))
                current_priority = priority
                current_batch = [task]
            else:
                current_batch.append(task)

        if current_batch:
            batches.append((current_priority, current_batch))

        return batches

    def _build_context(self, intention_data: Dict[str, Any], previous_results: List[Dict]) -> Dict[str, Any]:
        """构建上下文，包含上游 Agent 执行结果"""
        context = {
            "reasoning": intention_data.get("reasoning", ""),
            "intents": intention_data.get("intents", []),
            "key_entities": intention_data.get("key_entities", {}),
            "rewritten_query": intention_data.get("rewritten_query", ""),
            "recent_dialogue": [],
            "user_preferences": {},
            "previous_results": previous_results,
        }

        if self.memory_manager:
            recent = self.memory_manager.short_term.get_recent_context(3)
            context["recent_dialogue"] = recent
            context["user_preferences"] = self.memory_manager.long_term.get_preference()


        return context

    async def _execute_parallel_batch(
        self,
        tasks: List[Dict],
        intention_data: Dict[str, Any],
        previous_results: List[Dict]
    ) -> List[Dict]:
        """
        执行一个优先级的并行批次

        - 单个 task：直接串行执行
        - 多个 task：asyncio.gather 并行执行

        Args:
            tasks: 同优先级的任务列表
            intention_data: 意图识别结果
            previous_results: 前序批次结果（不同优先级）

        Returns:
            执行结果列表
        """
        if len(tasks) == 1:
            task = tasks[0]
            context = self._build_context(intention_data, previous_results)
            result = await self._execute_single_agent(
                agent_name=task["agent_name"],
                context=context,
                reason=task.get("reason", ""),
                expected_output=task.get("expected_output", ""),
            )
            return [{"agent_name": task["agent_name"], "priority": task.get("priority", 0), "result": result}]

        logger.info(f"Executing {len(tasks)} agents in parallel (priority={tasks[0].get('priority', 0)})")

        # 所有并行 Agent 共享同一份上游上下文
        context = self._build_context(intention_data, previous_results)

        # 创建并行协程
        coroutines = []
        for task in tasks:
            agent_name = task["agent_name"]
            priority = task.get("priority", 0)
            logger.info(f"  → {agent_name} (priority={priority}, reason={task.get('reason', '')})")
            coro = self._execute_single_agent(
                agent_name=agent_name,
                context=context,
                reason=task.get("reason", ""),
                expected_output=task.get("expected_output", ""),
            )
            coroutines.append((agent_name, priority, coro))

        # 并发执行，不阻塞其他 Agent
        exec_results = await asyncio.gather(
            *[coro for _, _, coro in coroutines],
            return_exceptions=True
        )

        # 整理结果
        results = []
        for (agent_name, priority, _), exec_result in zip(coroutines, exec_results):
            if isinstance(exec_result, Exception):
                logger.error(f"Agent {agent_name} failed with exception: {exec_result}")
                result = {
                    "status": "error",
                    "agent_name": agent_name,
                    "data": {"error": str(exec_result)},
                    "message": f"并行执行失败: {str(exec_result)}"
                }
            else:
                result = exec_result

            results.append({"agent_name": agent_name, "priority": priority, "result": result})

        return results

    async def _execute_single_agent(
        self,
        agent_name: str,
        context: Dict[str, Any],
        reason: str,
        expected_output: str,
    ) -> Dict[str, Any]:
        """
        执行单个 Agent，带超时保护

        Returns:
            执行结果 dict
        """
        if agent_name not in self.agent_registry:
            logger.warning(f"Agent not registered: {agent_name}")
            return {"status": "error", "message": f"智能体未注册: {agent_name}"}

        agent = self.agent_registry[agent_name]

        input_msg = Msg(
            name="Orchestrator",
            content=json.dumps({
                "context": context,
                "reason": reason,
                "expected_output": expected_output,
                "previous_results": context.get("previous_results", []),
            }, ensure_ascii=False),
            role="user"
        )

        try:
            response = await asyncio.wait_for(
                agent.reply(input_msg),
                timeout=self.agent_timeout_sec
            )
        except asyncio.TimeoutError:
            logger.error(f"Agent {agent_name} timed out after {self.agent_timeout_sec}s")
            return {
                "status": "error",
                "agent_name": agent_name,
                "data": {"error": "执行超时"},
                "message": f"智能体 {agent_name} 执行超时（{self.agent_timeout_sec}s）"
            }
        except Exception as e:
            logger.error(f"Agent {agent_name} failed: {e}")
            return {
                "status": "error",
                "agent_name": agent_name,
                "data": {"error": str(e)},
                "message": f"智能体执行失败: {str(e)}"
            }

        # 解析响应
        if isinstance(response.content, str):
            try:
                result = json.loads(response.content)
            except json.JSONDecodeError:
                result = {"output": response.content}
        else:
            result = response.content

        # Agent 内部返回了 error 字段，也视为失败
        if isinstance(result, dict) and "error" in result:
            return {
                "status": "error",
                "agent_name": agent_name,
                "data": result,
                "message": result.get("error", "未知错误")
            }

        return {"status": "success", "agent_name": agent_name, "data": result}

    def _aggregate_results(self, results: List[Dict], intention_data: Dict[str, Any]) -> Dict[str, Any]:
        """扁平化聚合结果"""
        agent_results = []
        errors = []

        for result in results:
            agent_results.append({
                "agent_name": result["agent_name"],
                "priority": result["priority"],
                "status": result["result"].get("status", "unknown"),
                "data": result["result"].get("data", {}),
                "message": result["result"].get("message", "")
            })
            if result["result"].get("status") == "error":
                errors.append({
                    "agent_name": result["agent_name"],
                    "error": result["result"].get("message", "unknown")
                })

        overall = "completed"
        if errors:
            overall = "partial_failure" if len(errors) < len(results) else "all_failed"

        return {
            "status": overall,
            "reasoning": intention_data.get("reasoning", ""),
            "key_entities": intention_data.get("key_entities", {}),
            "agents_executed": len(results),
            "errors": errors,
            "results": agent_results
        }

    def _update_memory(self, intention_data: Dict[str, Any], results: List[Dict]):
        """更新记忆系统（委托给 context.memory_updater.MemoryUpdater）"""
        if not self.memory_manager:
            return
        MemoryUpdater.update(self.memory_manager, results)
        logger.info("Memory updated after orchestration")
