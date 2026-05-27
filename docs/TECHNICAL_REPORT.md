# 差旅出行助手 Agent 技术报告

> 版本: v0.1.0  
> 日期: 2026-05-27  
> 状态: 原型可运行，Web/API/缓存/持久化能力已接入；RAG 语料与本地 Embedding 模型需补齐后才能完整启用

## 1. 项目概述

本项目是一个基于 AgentScope 和豆包大模型的多 Agent 差旅出行助手，目标是把行程规划、偏好记忆、企业差旅知识问答和实时信息查询整合到同一套对话流程中。

当前代码采用 `travel_agent` Python 包结构，核心入口包括：

- CLI: `travel_agent/cli.py`
- Web API: `travel_agent/web_api.py`
- React 前端: `frontend/`
- Agent 实现: `travel_agent/agents/`
- 记忆系统: `travel_agent/context/`
- 服务层: `travel_agent/services/`
- Skill 元数据: `.claude/skills/*/SKILL.md`

需要注意：当前 Skill 插件不是把 Python 代码放在 `.claude/skills/*/script/agent.py` 里执行。`.claude/skills` 只保存 `SKILL.md` 元数据和执行说明，实际 Agent 类在 `travel_agent.agents.*` 中，通过 `agent_module` 和 `agent_class` 动态加载。

## 2. 已实现能力

### 2.1 多 Agent 工作流

系统采用 Plan-and-Execute 思路：

1. `IntentionAgent` 根据用户输入、时间、对话上下文和 Skill 元数据识别意图，生成 `agent_schedule`。
2. `OrchestrationAgent` 按 `priority` 分组执行子 Agent。同优先级使用 `asyncio.gather` 并发执行，不同优先级串行推进。
3. 子 Agent 返回结构化结果后由协调器聚合，并通过 `MemoryUpdater` 更新偏好或行程历史。

当前支持的 Agent：

| Agent | 文件 | 作用 |
| --- | --- | --- |
| `intention_agent` | `travel_agent/agents/intention_agent.py` | 多意图识别、Query 改写、调度计划生成 |
| `orchestration_agent` | `travel_agent/agents/orchestration_agent.py` | 优先级调度、结果聚合、记忆更新 |
| `event_collection` | `travel_agent/agents/event_collection_agent.py` | 提取出发地、目的地、日期、目的等结构化行程信息 |
| `preference` | `travel_agent/agents/preference_agent.py` | 识别并更新用户偏好，支持追加/覆盖语义 |
| `memory_query` | `travel_agent/agents/memory_query_agent.py` | 查询用户偏好、历史行程、历史对话 |
| `rag_knowledge` | `travel_agent/agents/rag_knowledge_agent.py` | 基于 Milvus Lite 的知识库检索与问答 |
| `information_query` | `travel_agent/agents/information_query_agent.py` | wttr.in 天气查询、DDGS 网络搜索和摘要 |
| `itinerary_planning` | `travel_agent/agents/itinerary_planning_agent.py` | 整合前序结果生成行程方案 |

### 2.2 Skill 元数据与懒加载

`.claude/skills/*/SKILL.md` 的 frontmatter 声明了：

- `name`
- `description`
- `agent_name`
- `agent_module`
- `agent_class`
- `aliases`

`travel_agent/utils/skill_loader.py` 负责解析这些元数据：

- `get_skill_prompt()` 只加载 Skill 摘要，供 `IntentionAgent` 进行意图识别。
- `get_agent_specs()` 生成插件清单，供 `LazyAgentRegistry` 动态加载 Agent 类。
- `get_skill_content()` 在具体 Agent 执行阶段按需读取完整指令。

`travel_agent/agents/lazy_agent_registry.py` 通过 `importlib.import_module()` 按 `agent_module` 导入实际 Agent 类，并缓存实例。若 Agent 构造函数声明了 `memory_manager`，注册器会自动注入。

### 2.3 记忆系统

`MemoryManager` 统一管理两类记忆：

- 短期记忆: `ShortTermMemory`，默认保留最近 10 轮对话；接入 Redis 后可把会话窗口缓存到 `stm:{session_id}:messages`，TTL 1 小时。
- 长期记忆: `LongTermMemory`，默认使用 JSON 文件快照；可选启用 PostgreSQL 后端。

Redis 已实现为可选缓存层：

- 短期记忆: `stm:{session_id}:messages`
- LLM 总结: `summary:{user_id}`
- 用户偏好: `pref:{user_id}:{pref_type}`

PostgreSQL 后端已实现：

- 文件: `travel_agent/context/postgres_storage.py`
- 初始化脚本: `travel_agent/scripts/init_postgres_schema.py`
- JSON 迁移脚本: `travel_agent/scripts/migrate_json_to_postgres.py`
- 默认配置: `POSTGRES_ENABLED=False`

因此当前默认运行仍以 JSON 长期记忆为主；PostgreSQL 是已接入但默认关闭的生产化存储选项。

### 2.4 RAG 知识库

`RAGKnowledgeAgent` 已实现 Milvus Lite + sentence-transformers 检索流程：

- 向量库: Milvus Lite
- Collection: `business_travel_knowledge`
- 检索: Dense 向量检索 + BM25 稀疏检索 + RRF 融合排序，默认最终 `top_k=3`
- 初始化脚本: `travel_agent/scripts/init_knowledge_base.py`
- 默认 Embedding 路径: `data/models/bge-m3`
- 默认知识文档路径: `data/documents`
- BM25 索引路径: `.claude/skills/ask-question/data/rag_knowledge/bm25_index.json`

当前仓库已将 8 类知识文档放在 `data/documents` 下；`data/models/bge-m3` 仍需放置完整模型文件。因此 RAG 混合检索代码和语料已就绪，但完整知识库初始化仍需要先补齐本地 Embedding 模型。由于 bge-m3 向量维度与旧模型不同，升级后需要重新运行初始化脚本重建 Milvus collection。

### 2.5 Web API 与前端

Web API 已实现，不再只是规划项：

- 入口: `travel_agent/web_api.py`
- JWT 鉴权: `travel_agent/web_auth.py`
- ChatPipeline: `travel_agent/services/chat_pipeline.py`
- SessionStore: `travel_agent/services/session_store.py`
- 支持健康检查、Token 签发/刷新、会话管理、同步聊天和 SSE 流式聊天。

React 前端位于 `frontend/`，已实现登录、会话切换、健康检查、同步/流式聊天、Agent 事件展示和结果检查面板。

### 2.6 稳定性机制

已实现：

- 指数退避重试: `travel_agent/utils/llm_resilience.py`
- 熔断器: `travel_agent/utils/circuit_breaker.py`
- 健康检查: CLI 与 Web API 均可调用
- 单 Agent 执行超时: `OrchestrationAgent` 默认 60 秒

### 2.7 统一 LLM SDK 适配层

为避免不同 LLM 供应商在消息格式、响应字段、流式返回形态上的差异直接影响 Agent，当前项目新增 `travel_agent/llm/sdk.py`：

- `create_chat_model()`：统一模型创建入口，CLI、Web API、RAG 初始化和健康检查都通过它加载模型。
- `create_model_factory()`：按 Agent 名称选择模型档位，并按档位缓存模型实例。
- `UnifiedChatModel`：将 `Msg`、dict、字符串等输入统一转换为 OpenAI-style messages。
- `UnifiedLLMResponse`：将底层模型返回统一为 `.content` / `.text`，Agent 不再直接依赖供应商响应结构。
- `LLM_PROVIDER`：支持 `doubao`、`openai`、`deepseek`、`qwen`、`moonshot`、`zhipu` 等 OpenAI-compatible 供应商别名。

目前直接支持 OpenAI-compatible 协议；非兼容协议供应商可通过网关转换，或后续在 SDK 层新增 Provider Adapter，而不需要改 Agent 业务代码。

### 2.8 Agent 分层模型选型

系统支持 `default`、`fast`、`reasoning` 三个模型档位：

- `fast`：用于低复杂度、结构化或高频任务，例如 `event_collection`、`preference`、`memory_query`、`information_query`、`memory_summary`。
- `reasoning`：用于复杂推理和生成任务，例如 `intention_agent`、`rag_knowledge`、`itinerary_planning`。
- `default`：兜底档位，也可用于未显式配置的 Agent。

配置方式：

- `LLM_FAST_*` 配置快速模型。
- `LLM_REASONING_*` 配置推理模型。
- `LLM_TIER_<AGENT>` 调整 Agent 到模型档位的映射。

如果未配置 fast/reasoning 的供应商、模型名、API Key 或 Base URL，会自动回退到默认 `LLM_*` 配置。

## 3. 技术栈

| 类别 | 当前实现 |
| --- | --- |
| Agent 框架 | AgentScope 1.0.16 |
| LLM | 统一 LLM SDK 适配层，默认 `LLM_PROVIDER=doubao`、`doubao-seed-1-6-251015`；支持 OpenAI-compatible 模型 |
| CLI | Rich |
| Web API | FastAPI / Starlette、SSE、JWT |
| 前端 | React + Vite + lucide-react |
| RAG | pymilvus[milvus-lite]、sentence-transformers |
| 搜索 | ddgs |
| 天气 | wttr.in |
| 缓存 | Redis，可选，连接失败自动降级 |
| 持久化 | JSON 默认；PostgreSQL 可选 |

## 4. 测试现状

仓库中已有测试覆盖：

- 意图识别: `tests/test_intention_agent.py`, `tests/test_intention_agent_utils.py`
- 调度器: `tests/test_orchestration.py`, `tests/test_orchestration_utils.py`
- 新 Agent 工具行为: `tests/test_new_agents_utils.py`
- 事件收集、信息查询、RAG、记忆系统、PostgreSQL、Redis
- Web API 与鉴权: `tests/test_web_api_routes.py`, `tests/test_web_auth.py`

文档中涉及“1000 条测试集”“90%+ 准确率”“15 秒响应时间”“95% 知识库问答准确率”等数字，可以作为项目目标、实验口径或简历表达，但当前仓库未提交对应完整评测数据集和评测报告。对外技术报告中应避免把这些数字表述为可由当前代码直接复现实验结论。

## 5. 当前尚未完整实现或需补齐的部分

1. 本地 Embedding 模型未提交  
   默认路径 `data/models/bge-m3` 需要放置完整模型文件。离线环境下 `RAGKnowledgeAgent` 会因模型路径不可用而标记为未初始化。

2. PostgreSQL 默认未启用  
   `POSTGRES_ENABLED` 默认是 `False`。代码和迁移脚本已存在，但本地默认仍使用 JSON 文件长期记忆。

3. Redis 是可选增强，不是强依赖  
   `REDIS_ENABLED` 默认是 `True`，但连接失败会降级为 no-op。没有 Redis 服务时系统仍可运行，只是短期记忆和热数据不会共享缓存。

4. 指标缺少可复现实验资产  
   当前没有提交 1000 条脱敏测试集、RAG 标准答案集、端到端压测记录。指标应标注为“实验/目标口径”，或补充评测脚本与数据后再作为正式结果。

5. Skill 插件热插拔是元数据驱动，不是独立代码目录驱动  
   目前新增 Skill 需要在 `.claude/skills/*/SKILL.md` 声明 `agent_module` 和 `agent_class`，实际 Agent 类仍需进入 Python 包或可导入模块。

## 6. 建议后续工作

1. 下载或挂载完整 `bge-m3` 模型到 `data/models/bge-m3`，或修改 `RAG_EMBEDDING_MODEL` 指向可用路径。
2. 使用 `data/documents` 中的知识文档重新运行 `python travel_agent/scripts/init_knowledge_base.py`，重建 Milvus 向量库和 BM25 稀疏索引。
3. 在 README 和简历材料中把 PostgreSQL/Redis/RAG 的状态写清楚：已接入、默认关闭、需外部服务或数据资产。
4. 增加可复现实验脚本，沉淀意图识别、偏好更新、RAG QA、端到端延迟四类指标。
5. 若要继续强化知识问答效果，可在 hybrid 检索后增加 cross-encoder reranker。

## 7. 后续演进计划

### 7.1 完善全链路可观测性

当前系统已经加入轻量全链路可观测性模块 `travel_agent/observability.py`。每次请求会生成 `trace_id`，并将关键事件写入 JSONL：

- Trace 级别：记录单次请求经过的上下文加载、意图识别、Agent 调用链路、执行时间、异常信息。
- Session 级别：通过 `session_id` 聚合一个会话内的多轮请求、错误次数和平均耗时。
- User 级别：通过 `user_id` 聚合用户请求量、错误次数和平均耗时。
- Agent 级别：统计每个 Agent 的调用次数、成功次数、错误次数、超时次数和平均延迟。

落盘路径：

- 事件日志：`data/traces/events.jsonl`
- 聚合指标：`data/traces/metrics.jsonl`

本地汇总脚本：

```bash
python travel_agent/scripts/observability_report.py
```

后续可将 JSONL sink 替换为 OTLP/Jaeger/Prometheus exporter，用于和测试评估平台打通、定位性能瓶颈、发现异常模式并触发告警。

### 7.2 优化意图识别机制

当前所有用户输入都会进入 LLM 意图识别链路，准确性较好，但响应时间和调用成本较高。后续可以增加规则引擎作为前置快速路径：

- 高频明确意图直接规则命中，例如“我要去北京”“查天气”“报销标准是多少”。
- 规则命中后直接生成基础 `agent_schedule`，跳过 LLM 意图识别。
- 复杂、模糊、复合意图再进入 LLM 深度推理。
- 规则命中率、误判率和 LLM fallback 比例纳入监控。

这样可以在保证复杂场景准确率的同时，降低高频简单请求的延迟和成本。

### 7.3 增强 Agent 协作模式

当前调度主要依赖 `IntentionAgent` 一次性生成计划，`OrchestrationAgent` 按优先级执行。后续可以引入更灵活的协作机制：

- Handoffs：某个 Agent 发现信息不足时，可以主动转交给另一个 Agent。例如行程规划发现缺少出发日期时，转交给事项收集 Agent 补充。
- 动态 Routing：主规划 Agent 根据任务复杂度、用户上下文和前序执行结果，动态决定调用哪些子 Agent、调用顺序和并行策略。
- 结果反馈循环：后置 Agent 可以对前置 Agent 的结构化结果提出补充需求，而不是只能被动消费 `previous_results`。

这会让编排从固定计划执行，逐步升级为更智能的任务协作。

### 7.4 流式响应和过程可视化

Web API 已经具备 SSE 流式接口基础，后续可以进一步细化 Agent 执行事件，让用户不必等待所有 Agent 完成后才看到结果：

- Agent 开始、完成、失败时立即返回事件。
- 信息查询、知识检索、事项收集等中间结果可先展示摘要。
- 行程规划 Agent 输出时支持逐段流式返回。
- 前端展示本次请求调用了哪些 Agent、每步耗时、得到的关键结果。

需要注意的是，对外展示应以“执行过程和关键依据”为主，不直接暴露完整内部思维链，避免泄露系统提示词或产生不可控解释。

### 7.5 多模态能力扩展

当前系统主要支持文本输入。后续可以接入多模态大模型，扩展图片和语音场景：

- 图片输入：用户上传酒店、景点、票据或行程截图，系统识别地点、环境、价格、时间等信息。
- 语音输入：用户通过语音描述出行需求，系统转写后进入现有 Agent 编排链路。
- 偏好识别：结合图片和文本判断用户对酒店风格、景点类型、交通方式的偏好。
- 行程增强：根据景点照片识别地点，并推荐附近路线、餐饮和交通方案。

多模态能力可以复用当前意图识别、事项收集、偏好管理和行程规划链路，只需在入口层增加媒体解析和结构化转换。
