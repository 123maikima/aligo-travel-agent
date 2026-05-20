#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
并发压测脚本
测量 process_query 的端到端延迟，输出 P50/P95/P99 统计

用法:
    python tests/benchmark.py                # 默认: 5并发 x 10次 = 50次请求
    python tests/benchmark.py -c 10 -n 20    # 10并发 x 20次 = 200次请求
    python tests/benchmark.py --no-redis     # 禁用Redis，对比无缓存性能
"""
import asyncio
import argparse
import json
import os
import statistics
import sys
import time
from dataclasses import dataclass, field
from typing import List

# 添加项目根目录到路径
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from agentscope.model import OpenAIChatModel
from agentscope.message import Msg
from config_agentscope import init_agentscope
from config import LLM_CONFIG, SYSTEM_CONFIG, RESILIENCE_CONFIG, REDIS_CONFIG
from context.memory_manager import MemoryManager
from context.redis_cache import RedisCache
from agents.intention_agent import IntentionAgent
from agents.orchestration_agent import OrchestrationAgent
from agents.lazy_agent_registry import LazyAgentRegistry
from utils.llm_resilience import retry_with_backoff


@dataclass
class BenchmarkResult:
    """单次请求的压测结果"""
    query: str
    latency_ms: float
    success: bool
    error: str = ""
    agents_executed: List[str] = field(default_factory=list)


class BenchmarkRunner:
    """压测执行器"""

    # 标准测试query集（覆盖6大意图）
    TEST_QUERIES = [
        "我想从北京去杭州出差三天",
        "我喜欢住汉庭酒店",
        "我去过哪些地方",
        "差旅报销标准是多少",
        "杭州明天天气怎么样",
        "我要3月11日从上海去深圳，喜欢坐东航",
    ]

    def __init__(self, enable_redis: bool = True):
        self.enable_redis = enable_redis
        self.redis_cache = None
        self.model = None
        self.memory_managers = {}  # 每个请求用独立的memory_manager避免状态污染
        self.intention_agent = None
        self.orchestrators = {}

    async def setup(self):
        """初始化系统组件"""
        init_agentscope()

        self.model = OpenAIChatModel(
            model_name=LLM_CONFIG["model_name"],
            api_key=LLM_CONFIG["api_key"],
            client_kwargs={
                "base_url": LLM_CONFIG["base_url"],
                "timeout": float(SYSTEM_CONFIG.get("timeout", 60)),
            },
            temperature=LLM_CONFIG.get("temperature", 0.7),
            max_tokens=LLM_CONFIG.get("max_tokens", 2000),
        )

        # Redis 缓存（可选）
        if self.enable_redis and REDIS_CONFIG.get("enabled", True):
            self.redis_cache = RedisCache(**REDIS_CONFIG)
            if self.redis_cache.enabled:
                print(f"[benchmark] Redis cache enabled")
            else:
                print(f"[benchmark] Redis cache unavailable (server not running)")
                self.redis_cache = None
        else:
            print(f"[benchmark] Redis cache disabled (--no-redis)")
            self.redis_cache = None

        # 共享的 IntentionAgent
        self.intention_agent = IntentionAgent(
            name="IntentionAgent",
            model=self.model
        )

        print(f"[benchmark] System initialized")

    def _create_memory_manager(self, user_id: str, session_id: str) -> MemoryManager:
        """创建独立的 MemoryManager（避免并发请求互相污染）"""
        return MemoryManager(
            user_id=user_id,
            session_id=session_id,
            storage_path="data/memory",
            llm_model=self.model,
            redis_cache=self.redis_cache,
        )

    def _create_orchestrator(self, memory_manager: MemoryManager) -> OrchestrationAgent:
        """创建独立的 Orchestrator"""
        cache = {}
        lazy_registry = LazyAgentRegistry(
            model=self.model,
            cache=cache,
            memory_manager=memory_manager,
        )
        return OrchestrationAgent(
            name="OrchestrationAgent",
            agent_registry=lazy_registry,
            memory_manager=memory_manager,
        )

    async def run_single_query(self, user_input: str, user_id: str) -> BenchmarkResult:
        """执行单次查询，测量延迟"""
        import uuid
        session_id = str(uuid.uuid4())[:8]

        memory_manager = self._create_memory_manager(user_id, session_id)
        orchestrator = self._create_orchestrator(memory_manager)

        start = time.monotonic()
        success = False
        error = ""
        agents_executed = []

        try:
            # 1. 长期记忆总结（可能为空）
            long_term_summary = await memory_manager.get_long_term_summary_async(max_messages=50)
            recent_context = memory_manager.short_term.get_recent_context(n_turns=5)
            context_messages = []
            if long_term_summary:
                context_messages.append(Msg(name="system", content=long_term_summary, role="system"))
            for msg in recent_context:
                context_messages.append(Msg(name=msg["role"], content=msg["content"], role=msg["role"]))
            context_messages.append(Msg(name="user", content=user_input, role="user"))

            # 2. 意图识别
            intention_result = await retry_with_backoff(
                lambda: self.intention_agent.reply(context_messages),
                max_retries=RESILIENCE_CONFIG["max_retries"],
                base_delay_sec=RESILIENCE_CONFIG["retry_base_delay_sec"],
                max_delay_sec=RESILIENCE_CONFIG["retry_max_delay_sec"],
            )

            # 3. 解析意图
            intention_data = json.loads(intention_result.content)
            memory_manager.add_message("user", user_input)

            # 4. 调度子Agent
            orchestration_result = await retry_with_backoff(
                lambda: orchestrator.reply(intention_result),
                max_retries=RESILIENCE_CONFIG["max_retries"],
                base_delay_sec=RESILIENCE_CONFIG["retry_base_delay_sec"],
                max_delay_sec=RESILIENCE_CONFIG["retry_max_delay_sec"],
            )

            # 5. 解析结果
            result_data = json.loads(orchestration_result.content)
            agents_executed = [
                r.get("agent_name", "") for r in result_data.get("results", [])
            ]
            memory_manager.add_message(
                "assistant", json.dumps(result_data, ensure_ascii=False)
            )
            success = True

        except Exception as e:
            error = str(e)[:200]
            success = False

        latency_ms = (time.monotonic() - start) * 1000
        return BenchmarkResult(
            query=user_input,
            latency_ms=latency_ms,
            success=success,
            error=error,
            agents_executed=agents_executed,
        )

    async def run_benchmark(self, concurrency: int, num_queries: int):
        """
        执行压测

        Args:
            concurrency: 并发数
            num_queries: 总请求数
        """
        results: List[BenchmarkResult] = []
        semaphore = asyncio.Semaphore(concurrency)

        async def run_with_limit(idx: int):
            async with semaphore:
                query = self.TEST_QUERIES[idx % len(self.TEST_QUERIES)]
                user_id = f"bench_user_{idx % 3}"
                return await self.run_single_query(query, user_id)

        print(f"\n{'='*60}")
        print(f"并发压测: {concurrency} 并发 x {num_queries} 次 = {num_queries} 请求")
        print(f"Redis缓存: {'开启' if self.enable_redis else '关闭'}")
        print(f"测试Query集: {len(self.TEST_QUERIES)} 条 (循环使用)")
        print(f"{'='*60}\n")

        overall_start = time.monotonic()

        tasks = [run_with_limit(i) for i in range(num_queries)]
        results = await asyncio.gather(*tasks)

        total_time = time.monotonic() - overall_start

        # ========== 统计分析 ==========
        latencies = [r.latency_ms for r in results]
        successes = sum(1 for r in results if r.success)
        failures = sum(1 for r in results if not r.success)

        sorted_latencies = sorted(latencies)
        p50 = sorted_latencies[len(sorted_latencies) // 2]
        p95_idx = int(len(sorted_latencies) * 0.95)
        p95 = sorted_latencies[min(p95_idx, len(sorted_latencies) - 1)]
        p99_idx = int(len(sorted_latencies) * 0.99)
        p99 = sorted_latencies[min(p99_idx, len(sorted_latencies) - 1)]
        mean_lat = statistics.mean(latencies)

        qps = num_queries / total_time if total_time > 0 else 0

        # 缓存统计
        cache_stats = {}
        if self.redis_cache and self.redis_cache.enabled:
            cache_stats = self.redis_cache.get_stats()

        # ========== 输出报告 ==========
        print(f"\n{'='*60}")
        print(f"压测结果报告")
        print(f"{'='*60}")
        print(f"总请求数:     {num_queries}")
        print(f"成功:         {successes} ({successes/num_queries*100:.1f}%)")
        print(f"失败:         {failures} ({failures/num_queries*100:.1f}%)")
        print(f"总耗时:       {total_time:.2f}s")
        print(f"吞吐量(QPS):  {qps:.2f}")
        print(f"")
        print(f"延迟统计:")
        print(f"  平均(Pmean):  {mean_lat:.0f}ms")
        print(f"  中位(P50):    {p50:.0f}ms")
        print(f"  P95:          {p95:.0f}ms")
        print(f"  P99:          {p99:.0f}ms")
        print(f"  最小:         {min(latencies):.0f}ms")
        print(f"  最大:         {max(latencies):.0f}ms")

        if cache_stats:
            print(f"")
            print(f"Redis 缓存:")
            print(f"  命中:       {cache_stats.get('hits', 0)}")
            print(f"  未命中:     {cache_stats.get('misses', 0)}")
            print(f"  命中率:     {cache_stats.get('hit_rate', 0)*100:.1f}%")

        # 逐Query延迟详情
        print(f"\n逐请求延迟 (ms):")
        print(f"{'#':>4} {'延迟(ms)':>10} {'成功':>6} {'意图':>30} {'Agent':>20}")
        print(f"{'-'*4} {'-'*10} {'-'*6} {'-'*30} {'-'*20}")
        for i, r in enumerate(results):
            # 提取意图
            intent_str = ""
            if r.success:
                intent_str = ", ".join(r.agents_executed[:3])
            status = "OK" if r.success else "FAIL"
            print(f"{i+1:>4} {r.latency_ms:>10.0f} {status:>6} {intent_str:>30} {'':>20}")

        # Markdown 报告（保存到文件）
        report = self._generate_markdown_report(
            concurrency=concurrency,
            num_queries=num_queries,
            total_time=total_time,
            qps=qps,
            successes=successes,
            failures=failures,
            mean_lat=mean_lat,
            p50=p50,
            p95=p95,
            p99=p99,
            min_lat=min(latencies),
            max_lat=max(latencies),
            cache_stats=cache_stats,
            latencies=latencies,
        )

        report_path = os.path.join(project_root, "tests", "results", "benchmark_report.md")
        os.makedirs(os.path.dirname(report_path), exist_ok=True)
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(report)
        print(f"\nMarkdown 报告已保存: {report_path}")

        return results

    def _generate_markdown_report(self, **kwargs) -> str:
        """生成 Markdown 格式的压测报告"""
        redis_status = "开启" if kwargs["cache_stats"] else "关闭"
        hit_rate = kwargs["cache_stats"].get("hit_rate", 0) * 100 if kwargs["cache_stats"] else "N/A"

        return f"""# 并发压测报告

> 生成时间: {time.strftime('%Y-%m-%d %H:%M:%S')}

## 配置

| 参数 | 值 |
|------|-----|
| 并发数 | {kwargs['concurrency']} |
| 总请求数 | {kwargs['num_queries']} |
| Redis 缓存 | {redis_status} |
| 模型 | {LLM_CONFIG['model_name']} |

## 结果

| 指标 | 值 |
|------|-----|
| 成功/失败 | {kwargs['successes']} / {kwargs['failures']} |
| 总耗时 | {kwargs['total_time']:.2f}s |
| 吞吐量 (QPS) | {kwargs['qps']:.2f} |

## 延迟统计

| 指标 | 延迟 |
|------|------|
| 平均 (Pmean) | {kwargs['mean_lat']:.0f}ms |
| 中位 (P50) | {kwargs['p50']:.0f}ms |
| P95 | {kwargs['p95']:.0f}ms |
| P99 | {kwargs['p99']:.0f}ms |
| 最小 | {kwargs['min_lat']:.0f}ms |
| 最大 | {kwargs['max_lat']:.0f}ms |

## Redis 缓存

| 指标 | 值 |
|------|-----|
| 缓存命中 | {kwargs['cache_stats'].get('hits', 'N/A')} |
| 缓存未命中 | {kwargs['cache_stats'].get('misses', 'N/A')} |
| 命中率 | {hit_rate:.1f}% |
"""


def main():
    parser = argparse.ArgumentParser(description="Aligo 商旅助手并发压测")
    parser.add_argument("-c", "--concurrency", type=int, default=5, help="并发数 (默认5)")
    parser.add_argument("-n", "--num", type=int, default=10, help="总请求数 (默认10)")
    parser.add_argument("--no-redis", action="store_true", help="禁用Redis缓存")
    args = parser.parse_args()

    runner = BenchmarkRunner(enable_redis=not args.no_redis)

    async def run():
        await runner.setup()
        await runner.run_benchmark(
            concurrency=args.concurrency,
            num_queries=args.num,
        )

    asyncio.run(run())


if __name__ == "__main__":
    main()
