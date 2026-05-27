# 差旅出行助手项目问答与简历口径

本文档按当前仓库真实实现整理。重点区分“已经实现”“默认关闭/需配置”“尚未补齐”的部分，避免把规划项写成已上线能力。

## 1. 项目概述

差旅出行助手是一个基于 AgentScope 和豆包大模型的多 Agent 系统，面向差旅场景提供意图识别、事项收集、偏好管理、知识问答、实时信息查询和行程规划。

当前项目已包含：

- CLI 入口: `travel_agent/cli.py`
- FastAPI Web API: `travel_agent/web_api.py`
- React 前端: `frontend/`
- 多 Agent 编排: `travel_agent/agents/`
- 记忆系统: `travel_agent/context/`
- Skill 元数据: `.claude/skills/*/SKILL.md`

一句话介绍：

> 我做的是一个差旅出行助手 Agent，基于 AgentScope 和豆包模型实现 Plan-and-Execute 多 Agent 编排，支持意图识别、偏好记忆、RAG 知识问答、实时信息查询和行程规划，并接入了 Redis 缓存、PostgreSQL 可选持久化、FastAPI 接口和 React 前端。

## 2. 简历写法建议

差旅出行助手 Agent | 2026.02-2026.05

- 基于 AgentScope 设计 Plan-and-Execute 多 Agent 架构，由 `IntentionAgent` 生成结构化调度计划，`OrchestrationAgent` 按优先级并发调度 `event_collection`、`preference`、`memory_query`、`rag_knowledge`、`information_query`、`itinerary_planning` 等子 Agent。
- 实现两层记忆系统：短期记忆保存最近 10 轮会话，长期记忆保存用户偏好、历史行程和聊天历史；接入 Redis 作为可选缓存层，PostgreSQL 作为可选长期存储后端，默认可降级为 JSON 文件存储。
- 实现 Skill 元数据驱动的插件化加载机制：通过 `.claude/skills/*/SKILL.md` 声明 `agent_module` 和 `agent_class`，`LazyAgentRegistry` 首次调用时动态导入并缓存 Agent 实例；意图识别阶段只加载 Skill 元数据，执行阶段按需加载详细指令。
- 实现 RAGKnowledgeAgent、InformationQueryAgent 和 Web 化能力：RAG 使用 Milvus Lite + sentence-transformers 的检索流程；实时信息查询集成 wttr.in 与 DDGS；Web API 支持 JWT、会话管理、同步聊天和 SSE 流式响应，前端基于 React + Vite。
- 增加工程稳定性机制：LLM 调用指数退避重试、熔断器、健康检查、单 Agent 执行超时，并补充意图识别、调度、记忆、RAG、Redis、PostgreSQL、Web API 等测试。

指标写法建议：

- 可以说“项目目标/实验口径是将意图识别从关键词匹配升级到 LLM 语义识别，目标准确率 90%+，并通过优先级并发把端到端耗时从串行约 30 秒降低到约 15 秒”。
- 不建议说“当前仓库可复现 1000 条测试集 90%+、RAG QA 95%”，因为仓库里没有提交完整评测集、标准答案和评测报告。

## 3. 当前真实实现状态

已实现：

- `IntentionAgent`: LLM 语义意图识别、JSON 解析容错、调度计划生成。
- `OrchestrationAgent`: 按优先级分组，同组并发执行，组间串行执行，支持超时和异常隔离。
- `EventCollectionAgent`: 提取结构化行程信息。
- `PreferenceAgent`: 识别偏好追加/覆盖，协调器负责写回长期记忆。
- `MemoryQueryAgent`: 查询偏好、历史行程和聊天历史。
- `InformationQueryAgent`: wttr.in 天气、DDGS 搜索和摘要。
- `ItineraryPlanningAgent`: 整合前序 Agent 结果生成行程。
- `RAGKnowledgeAgent`: Milvus Lite 检索、文档写入、Top-K 查询、基于检索内容生成回答。
- `MemoryManager`: 短期记忆 + 长期记忆，Redis 可选缓存，PostgreSQL 可选后端。
- Web API 和 React 前端。

默认关闭或依赖外部配置：

- PostgreSQL: 代码已实现，但 `POSTGRES_ENABLED=False`，默认仍使用 JSON 文件。
- Redis: `REDIS_ENABLED=True`，但连接失败会自动降级，不阻断主流程。
- RAG: bge-m3 配置、Dense + BM25 + RRF 混合检索代码和 `data/documents` 知识文档已就绪，但需要本地 Embedding 模型。

尚未补齐：

- `data/models/bge-m3` 需要放置完整模型文件。
- 1000 条意图识别评测集、RAG 标准答案集、正式压测报告未提交。

## 4. 面试问答

### 4.1 AgentScope 是什么？

AgentScope 是阿里开源的多智能体开发框架，提供 Agent 抽象、消息对象和异步调用能力。这个项目中所有核心 Agent 都继承 `AgentBase`，通过 `Msg` 传递结构化输入输出，便于统一调度和测试。

### 4.2 为什么用 Plan-and-Execute？

差旅助手通常不是单步问答，而是要先理解用户意图，再调度多个能力：收集事项、查询偏好、查知识库、查实时信息、最后生成行程。Plan-and-Execute 把“规划”和“执行”拆开：

- `IntentionAgent` 负责规划，生成要调用哪些 Agent、优先级是什么。
- `OrchestrationAgent` 负责执行，同优先级并发，不同优先级串行。

相比 ReAct 循环，这种方式更可控，也更容易做并发和错误隔离。

### 4.3 多 Agent 如何通信？

系统使用 AgentScope 的 `Msg` 对象传递消息。协调器会把意图识别结果封装成统一 context，包括：

- `reasoning`
- `intents`
- `key_entities`
- `rewritten_query`
- `recent_dialogue`
- `user_preferences`
- `previous_results`

每个子 Agent 只处理自己的 context，返回 JSON 结构结果。协调器再统一聚合。

### 4.4 优先级并发调度怎么做？

`OrchestrationAgent` 会先按 `priority` 排序，再按优先级分组：

- Priority 1: 信息收集类 Agent，例如 `event_collection`、`memory_query`、`preference`、`information_query`、`rag_knowledge`。
- Priority 2: 依赖前序结果的 `itinerary_planning`。

同一组如果有多个 Agent，就用 `asyncio.gather(..., return_exceptions=True)` 并发执行。单个 Agent 出错不会导致整批失败。

### 4.5 Skill Plugins 是怎么实现的？

当前实现是“元数据驱动的插件化”，不是每个 Skill 目录都放一份独立 Python 实现。

每个 `.claude/skills/*/SKILL.md` frontmatter 声明：

```yaml
agent_name: rag_knowledge
agent_module: travel_agent.agents.rag_knowledge_agent
agent_class: RAGKnowledgeAgent
aliases:
  - ask-question
```

`SkillLoader` 读取这些元数据，`LazyAgentRegistry` 首次调用时通过 `importlib.import_module(agent_module)` 导入类并实例化。加载后的 Agent 会缓存，后续复用。

### 4.6 Progressive Disclosure 是什么？

在这个项目中是按阶段加载信息：

- 意图识别阶段只加载 Skill 名称、描述和 Agent 映射，避免把所有执行指令塞进 Prompt。
- 子 Agent 执行阶段才通过 `get_skill_content()` 读取对应 Skill 的详细说明。

这样可以降低 Prompt 长度，也减少无关指令对意图识别的干扰。

### 4.7 记忆系统怎么设计？

记忆分两层：

- 短期记忆保存当前会话最近 10 轮对话，用于上下文理解。
- 长期记忆保存用户偏好、历史行程和聊天历史，默认是 JSON 文件，可选切到 PostgreSQL。

Redis 是缓存增强层，缓存短期记忆、用户偏好热数据和 LLM 总结。Redis 不可用时会自动降级。

### 4.8 PostgreSQL 是否已经上线？

代码已经接入，但默认没有启用。当前配置中 `POSTGRES_ENABLED=False`，默认长期记忆仍写 JSON 文件。PostgreSQL 相关能力包括 schema 初始化、快照加载保存和 JSON 迁移脚本，适合作为生产化升级路径。

### 4.9 Redis 是否是必须的？

不是。`RedisCache` 初始化失败会把缓存禁用，主流程继续走本地内存和 JSON/数据库持久化。Redis 的价值主要是支持分布式会话共享、减少偏好查询和总结重复计算。

### 4.10 RAG 知识库当前是什么状态？

RAGKnowledgeAgent 的代码链路已经实现：加载本地 bge-m3 Embedding 模型、初始化 Milvus Lite、写入文档向量、构建 BM25 稀疏索引、执行 Dense + BM25 + RRF 混合检索，并基于检索结果生成回答。

但当前仓库仍缺一类关键资产：

- 本地 Embedding 模型完整文件。

知识文档已放在 `data/documents`，所以现在应表述为“RAG 混合检索代码和语料已就绪，bge-m3 模型文件需补齐后完整启用”。

### 4.11 Web API 做到了什么？

`travel_agent/web_api.py` 已实现：

- `/health`
- Token 签发、刷新、当前用户查询
- 会话创建、关闭、列表查询
- 同步聊天
- SSE 流式聊天

鉴权在 `travel_agent/web_auth.py` 中实现，服务层主流程在 `travel_agent/services/chat_pipeline.py` 中。

### 4.12 当前项目最大风险是什么？

主要风险不是代码结构，而是可复现实验资产不足：

- 指标数据没有对应评测集。
- RAG 缺少文档和模型文件。
- PostgreSQL/Redis 需要外部服务才能验证完整链路。

因此答辩时应该强调“工程链路已接入，部分生产资产和评测资产需补齐”。

### 4.13 后续怎么优化这个项目？

后续优化可以分成五个方向：

1. 全链路可观测性  
   当前已加入轻量 Trace 体系，每次请求生成 `trace_id`，记录上下文加载、意图识别、Agent 开始/完成/失败、执行耗时和错误信息。事件写入 `data/traces/events.jsonl`，聚合指标写入 `data/traces/metrics.jsonl`，可通过 `observability_report.py` 从 Trace、Session、User、Agent 四个层级分析系统表现，后续再接入评测平台和告警系统。

2. 意图识别前置规则引擎  
   当前所有请求都走 LLM 意图识别，成本和延迟都偏高。可以增加规则引擎处理高频明确意图，例如“我要去北京”“查天气”“报销标准是多少”。规则命中就直接生成调度计划，复杂模糊场景再 fallback 到 LLM。

3. 更灵活的 Agent 协作  
   当前主要是一次性计划和优先级调度。后续可以引入 Handoffs 机制，让 Agent 主动转交任务；比如行程规划发现信息不足时，转给事项收集 Agent。也可以引入动态 Routing，根据任务复杂度和前序结果动态决定调用哪些 Agent。

4. 流式响应和过程可视化  
   Web API 已有 SSE 基础，后续可以做到 Agent 完成一个就返回一个事件，前端实时展示系统调用了哪些 Agent、每步耗时和关键结果。对外展示应强调“执行过程和依据”，不直接暴露完整内部思维链。

5. 多模态扩展  
   当前主要是文本输入。后续可以支持图片和语音，例如上传酒店照片识别酒店环境和偏好，上传景点照片识别地点并推荐行程，语音输入转写后复用现有 Agent 编排链路。

### 4.14 如何支持更多 LLM 模型？

项目不让 Agent 直接绑定某个供应商 SDK，而是在 `travel_agent/llm/sdk.py` 里做统一适配：

- 入口统一为 `create_chat_model()`。
- 输入统一为 OpenAI-style messages。
- 输出统一为 `UnifiedLLMResponse.content`。
- 供应商通过 `LLM_PROVIDER`、`LLM_MODEL_NAME`、`LLM_BASE_URL`、`LLM_API_KEY` 配置。

当前优先支持 OpenAI-compatible 模型，比如豆包、OpenAI、DeepSeek、Qwen、Moonshot、智谱等。这样 Agent 层只依赖统一模型对象，不关心底层响应是 `.content`、`.text`、dict 还是异步流，降低模型切换时的协议不兼容风险。

### 4.15 Agent 分层模型选型怎么做？

项目在统一 LLM SDK 上增加了 `create_model_factory()`，按 Agent 名称选择模型档位：

- 低复杂度任务走 `fast`：事项收集、偏好管理、记忆查询、信息查询、长期记忆总结。
- 复杂推理任务走 `reasoning`：意图识别、RAG 知识问答、行程规划。
- 其他任务走 `default`。

这样简单任务可以用更快更便宜的模型，复杂任务保留更强的推理模型。Agent 代码不需要关心自己用了哪个供应商或模型，只接收统一的 `model` 对象。后续只要改环境变量 `LLM_FAST_*`、`LLM_REASONING_*` 或 `LLM_TIER_*`，就能切换模型策略。

## 5. 运行提示

CLI：

```bash
python travel_agent/cli.py
```

Web API：

```bash
uvicorn travel_agent.web_api:app --host 0.0.0.0 --port 8000
```

前端：

```bash
cd frontend
npm install
npm run dev
```

RAG 初始化前需要先补齐：

- `data/models/bge-m3`

知识文档默认读取 `data/documents/*.txt`。

然后运行：

```bash
python travel_agent/scripts/init_knowledge_base.py
```
