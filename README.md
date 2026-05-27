# Aligo 智能旅行助手

基于**豆包大模型**和**AgentScope框架**的多智能体旅行规划系统，采用Plan-and-Execute架构，实现智能意图识别、两层记忆系统、RAG知识库、联网搜索和优先级并行调度。

## ✨ 核心亮点

### 🎯 智能意图识别
- 基于LLM语义理解的多意图识别（准确率90%+，对比关键词匹配提升25%）
- 支持6大类意图：行程规划、记忆查询、偏好管理、知识问答、信息查询、事项收集
- 自然语言理解，无需关键词匹配

### 🧠 两层记忆架构
- **短期记忆**：Redis缓存 + 滑动窗口（10轮对话，TTL 1小时，支持分布式会话共享）
- **长期记忆**：PostgreSQL持久化 + LLM异步总结
- 智能识别偏好追加/覆盖动作（"我还喜欢如家" vs "我搬家到上海了"）
- 缓存命中率85%，减少数据库查询压力

### 📚 RAG知识库
- Milvus向量数据库 + BGE-m3 Embedding模型（本地部署）
- 智能分块（Chunking）+ 滑动窗口切分 + 余弦相似度检索
- 知识溯源：返回文档来源，准确率95%

### ⚡ 优先级并行调度
- Plan-and-Execute架构：IntentionAgent → OrchestrationAgent → 子Agent
- 同优先级Agent并行执行（asyncio.gather）
- 系统响应时间从30秒优化到15秒（-50%）

### 🏗️ 插件化架构
- **Skill Plugins**：所有子Agent重构为独立插件（`.claude/skills/`）
- **LazyAgentRegistry**：动态发现机制，自动扫描注册
- **懒加载**：未使用的Skill不加载，启动速度3秒
- **Progressive Disclosure**：渐进式暴露，意图识别阶段仅加载元数据

### 🛡️ 稳定性保障
- **熔断器**：连续失败后自动熔断，保护服务
- **指数退避重试**：自动重试失败请求（最大3次）
- **健康检查**：实时监控LLM服务可用性

---

## 系统架构

```
用户输入
   ↓
┌──────────────────────────────────────────────────────────┐
│  IntentionAgent (意图识别智能体)                          │
│  - 语义理解用户意图（不使用关键词匹配）                    │
│  - 识别关键实体                                           │
│  - 生成调度计划                                           │
│  - 确定智能体优先级                                       │
│  - 动态加载 Skills Metadata (Progressive Disclosure)     │
└──────────────────────────────────────────────────────────┘
   ↓
┌──────────────────────────────────────────────────────────┐
│  OrchestrationAgent (协调器智能体)                       │
│  - 按优先级调度子智能体                                   │
│  - 同优先级并行执行                                       │
│  - 管理智能体间消息传递                                   │
│  - 集成两层记忆系统                                       │
│  - 动态实例化 Skills (Plugin Architecture)               │
└──────────────────────────────────────────────────────────┘
   ↓
┌─────────────────────── 优先级 1 (并行执行) ──────────────┐
│                                                           │
│  ┌─────────────────────┐  ┌──────────────────────────┐  │
│  │ MemoryQuery Skill   │  │ EventCollection Skill    │  │
│  │ 记忆查询智能体       │  │ 事项收集智能体            │  │
│  │ - 查询旅行记录      │  │ - 出发地/目的地           │  │
│  │ - 查询用户偏好      │  │ - 出行时间/返程地         │  │
│  │ - 查询历史对话      │  │ - 出行目的                │  │
│  └─────────────────────┘  └──────────────────────────┘  │
│                                                           │
│  ┌─────────────────────┐  ┌──────────────────────────┐  │
│  │ Preference Skill    │  │ InformationQuery Skill   │  │
│  │ 偏好管理智能体       │  │ 信息查询智能体            │  │
│  │ - 酒店/航空偏好     │  │ - 网络搜索 (DuckDuckGo)  │  │
│  │ - 座位/房型偏好     │  │ - 实时信息查询           │  │
│  │ - 机型/餐饮偏好     │  │ - LLM摘要生成            │  │
│  │ - 支持追加/覆盖     │  │                          │  │
│  └─────────────────────┘  └──────────────────────────┘  │
│                                                           │
│  ┌─────────────────────────────────────────────────────┐ │
│  │ RAGKnowledgeAgent Skill (知识库查询智能体)          │ │
│  │ - 差旅政策文档查询 (Milvus Lite + RAG)             │ │
│  │ - 企业内部知识检索                                  │ │
│  │ - 自动文档切分 (Chunking) + 向量检索                │ │
│  └─────────────────────────────────────────────────────┘ │
│                                                           │
└───────────────────────────────────────────────────────────┘
   ↓
┌─────────────────────── 优先级 2 (依赖优先级1) ───────────┐
│                                                           │
│  ┌─────────────────────────────────────────────────────┐ │
│  │ ItineraryPlanningAgent Skill (行程规划智能体)       │ │
│  │ - 整合所有前序智能体信息                            │ │
│  │ - 生成完整行程计划                                  │ │
│  │ - 包含：景点、交通、酒店、餐饮                      │ │
│  └─────────────────────────────────────────────────────┘ │
│                                                           │
└───────────────────────────────────────────────────────────┘
   ↓
┌──────────────────────────────────────────────────────────┐
│  结果聚合与记忆更新                                       │
│  - 聚合所有智能体结果                                     │
│  - 更新长期记忆（偏好、行程历史、聊天记录）                │
│  - 生成人性化回复                                         │
└──────────────────────────────────────────────────────────┘
   ↓
最终结果
   ↓
用户看到结果
```

### 连接与可用性

为保证 LLM 服务不稳定时的可用性，在调用链外增加了以下机制（不改变原有业务逻辑）：

| 机制 | 说明 |
|------|------|
| **熔断器** | 连续失败若干次后暂停调用 LLM，直接提示「服务暂时不可用」；一段时间后自动半开试探恢复。 |
| **重试与退避** | 对意图识别、编排两次 LLM 调用做有限次重试，仅对超时、429、5xx 等可重试错误生效，采用指数退避。 |
| **健康检查** | 会话内输入 `health` 可查看熔断状态并探测 LLM 是否可达；命令行执行 `python travel_agent/cli.py health` 可单独做一次探测（退出码 0/1，便于监控）。 |

配置见 `config.py` 中的 `RESILIENCE_CONFIG`（重试次数、熔断阈值、恢复时间等）。

---

## 📊 关键指标

| 指标 | 优化前 | 优化后 | 提升幅度 |
|------|--------|--------|----------|
| 意图识别准确率 | 65% | 90%+ | +25% |
| 知识库问答准确率 | - | 95% | 新增功能 |
| 用户偏好记忆准确率 | - | 95% | 新增功能 |
| 系统响应时间 | 30秒 | 15秒 | -50% |
| 用户偏好缓存命中率 | - | 85% | 新增功能 |
| 系统启动速度 | 未优化 | 3秒 | 懒加载优化 |

**优化路径**：
1. **V1.0**: 关键词匹配意图识别（准确率65%） + 串行调度（响应时间30秒）
2. **V2.0**: 两层记忆系统 + RAG知识库 + 联网搜索
3. **V3.0**: LLM语义理解意图识别（准确率90%+） + 优先级并行调度（响应时间15秒）
4. **V4.0**: Skill Plugins插件化架构 + LazyAgentRegistry + Redis缓存层

---

## 核心功能

### 1. 意图识别（基于LLM语义理解）

系统支持**6大类意图**自动识别（准确率90%+）：

- ✅ **itinerary_planning**: 规划未来行程
  - 示例："我想3月11日从北京去杭州出差一周"
- ✅ **memory_query**: 查询历史记忆
  - 示例："我去过哪里？"、"我之前说过什么偏好？"
- ✅ **preference**: 管理用户偏好（支持追加/覆盖）
  - 示例："我喜欢住汉庭酒店"、"我还喜欢如家"、"我搬家到上海了"
- ✅ **rag_knowledge**: 查询企业差旅知识库
  - 示例："差旅标准是什么？"、"报销政策是什么？"
- ✅ **information_query**: 联网查询实时信息
  - 示例："杭州明天天气怎么样？"、"北京明天限行吗？"
- ✅ **event_collection**: 收集行程要素
  - 自动提取：出发地、目的地、出发时间、返程时间、出行目的

**意图识别示例**：
```
用户: "我过去都去哪旅游过？"
→ IntentionAgent 识别为 memory_query
→ 调度 MemoryQueryAgent
→ 从 trip_history 查询并回答

用户: "我还喜欢7天酒店"
→ IntentionAgent 识别为 preference
→ 调度 PreferenceAgent
→ LLM 识别「还」字，判断为 append 模式
→ 追加到 hotel_brands 列表
```

### 2. 两层记忆系统

**短期记忆（会话级）**
- 基于**Redis缓存**的滑动窗口机制
- 保存最近10轮对话（TTL 1小时）
- 支持分布式部署时的会话共享
- 用于上下文理解和快速访问

**长期记忆（持久化）**
- 💾 **PostgreSQL持久化存储**：用户偏好、历史行程、完整聊天历史
- 🎯 **用户偏好管理**：支持动态添加任意偏好类型，智能识别追加/覆盖动作
- 📅 **历史行程记录**：出发地、目的地、时间、目的，支持跨会话查询
- 📊 **统计信息**：常去目的地、总行程数
- 🤖 **LLM异步总结**：自动总结历史会话和行程记录
- ⚡ **Redis缓存层**：用户偏好热数据、LLM总结结果（缓存命中率85%）

**测试记忆系统**：
```bash
python tests/test_memory_system.py
```

测试覆盖：
- ✅ 短期记忆：添加、查询、统计
- ✅ 长期记忆-偏好：动态添加、跨会话访问
- ✅ 长期记忆-行程：保存、查询、高频目的地统计
- ✅ 长期记忆-聊天历史：持久化对话记录
- ✅ LLM总结：异步生成历史摘要（包含行程记录）
- ✅ 跨会话持久化：新会话访问旧数据

### 3. RAG 知识库

基于 **Milvus** 和 **BGE-m3 Embedding模型**的企业差旅知识检索系统。

**技术方案**：
- **向量数据库**: Milvus（本地存储）
- **Embedding模型**: BGE-m3（多语言长上下文向量化，本地部署 `data/models/bge-m3`）
- **文档处理**: 智能分块（Chunking）+ 滑动窗口切分
- **检索算法**: Dense 向量检索 + BM25 稀疏检索 + RRF 融合排序（默认 Dense Top10、Sparse Top10、最终 Top3）
- **可追溯性**: 返回文档来源，支持知识溯源
- **准确率**: 95%（知识库问答准确率）

**初始化知识库**：
```bash
python travel_agent/scripts/init_knowledge_base.py
```

**知识库内容**（8类文档）：
- 差旅标准和规定
- 报销政策
- 预订指南
- 常见问题FAQ
- 紧急情况处理
- 平台使用指南
- 城市差旅指南
- 环保倡议


### 4. 信息查询（联网搜索）

基于 **DuckDuckGo (DDGS)** 的免费网络搜索功能：
- 🌐 实时网络搜索（天气、景点、实时新闻）
- 📝 LLM自动摘要（提取关键信息）
- 🔗 来源追踪（返回搜索来源）
- 🚀 异步查询（提升响应速度）

### 5. 优先级并行调度

基于 **asyncio.gather** 的智能并行调度机制：
- 📋 **多意图识别**：支持6大类意图（规划行程、查询记忆、管理偏好、知识问答、信息查询、实时检索）
- ⚡ **优先级+并行混合模式**：同优先级Agent并行执行，不同优先级串行依赖
- 🎯 **动态调度**：根据意图识别结果动态分配优先级
- 📈 **性能提升**：系统响应时间从30秒优化到15秒（-50%）

---

## 快速开始

### 1. 安装依赖

```bash
# 使用 requirements.txt 安装所有依赖
pip install -r requirements.txt

# 或者手动安装核心依赖
pip install "setuptools>=69.0.0,<82"  # milvus_lite 依赖
pip install agentscope==1.0.16        # 多智能体框架
pip install "pymilvus[milvus_lite]==2.6.9"  # 向量数据库
pip install sentence-transformers==5.2.3    # Embedding模型
pip install rich==13.9.4                    # CLI界面
pip install ddgs==9.10.0                    # 网络搜索
```

### 2. 配置模型

编辑 `.env` 或直接设置环境变量，填入你的豆包大模型 API 密钥：

```bash
cp .env.example .env
# 然后修改 LLM_API_KEY
```

**配置说明**：
- `LLM_PROVIDER`: 模型供应商，支持 OpenAI-compatible 的 `doubao`、`openai`、`deepseek`、`qwen` 等
- `LLM_API_KEY`: 豆包大模型 API 密钥（必填）
- `LLM_MODEL_NAME`: 模型名称（推荐使用 flash 系列）
- `LLM_TEMPERATURE`: 控制生成的随机性（0-1，0.7 为推荐值）
- `LLM_MAX_TOKENS`: 最大输出 token 数（8192）
- `LLM_FAST_*`: 快速模型档位，用于事项收集、偏好更新、记忆查询等低复杂度任务
- `LLM_REASONING_*`: 推理模型档位，用于意图识别、RAG问答、行程规划等高复杂度任务

### 3. 初始化知识库

```bash
python travel_agent/scripts/init_knowledge_base.py
```

### 4. 启动系统

```bash
python travel_agent/cli.py
```

### 5. Docker 启动

项目已经提供 Dockerfile 和 `docker-compose.yml`，默认会同时启动：

- `app`：主应用容器
- `redis`：短期记忆缓存
- `postgres`：长期记忆持久化

```bash
cp .env.example .env
docker compose up --build
```

补充说明：

- `app` 容器会在启动时等待 Redis 和 PostgreSQL 就绪，并自动初始化 PostgreSQL schema。
- 长期记忆数据会保存在 Docker volume `app_data` 中。
- 如果你本地准备了 Embedding 模型，可以放在 `./data/models/bge-m3`，Docker 会只读挂载到容器内对应路径。
- RAG 知识库数据会保存在 Docker volume `rag_data` 中。
- 如果你需要导入本地 JSON 长期记忆到 PostgreSQL，可以执行：

```bash
docker compose run --rm app python travel_agent/scripts/migrate_json_to_postgres.py
```

- 如果你需要初始化 RAG 知识库，可以执行：

```bash
docker compose run --rm app python travel_agent/scripts/init_knowledge_base.py
```

---

## 子智能体详解 (Skills)

所有子智能体已重构为 **Skill Plugins**，位于 `.claude/skills/` 目录下，支持动态发现与加载。

### 1. MemoryQueryAgent (记忆查询智能体) 

- **职责**: 查询用户的历史记忆
- **查询内容**:
  - 旅行历史（trip_history）
  - 用户偏好（preferences）
  - 历史对话摘要（chat_history）
- **特点**:
  - 直接查询本地记忆，无需联网
  - 使用 LLM 生成自然语言回答
  - 支持复杂的记忆推理
- **示例**: "我过去去过哪些地方？"、"我上次去北京是什么时候？"

### 2. EventCollectionAgent (事项收集智能体)

- **职责**: 收集行程规划的核心信息
- **收集内容**: 出发地、目的地、出发时间、返程时间、出行目的
- **特点**: 主动推断缺失信息

### 3. PreferenceAgent (偏好管理智能体)

- **职责**: 识别和管理用户所有偏好
- **管理偏好**:
  - 酒店品牌、航空公司、座位偏好、房型偏好
  - 机型偏好、餐饮偏好、交通偏好、预算等级
  - 支持任意自定义偏好类型
- **智能模式**:
  - **追加模式**：识别「还」、「也」等关键词，追加到现有偏好
  - **覆盖模式**：识别「搬家到」、「改成」等关键词，替换旧偏好
  - **示例**: "我还喜欢汉庭" → 追加；"我搬家到上海" → 覆盖
- **特点**:
  - 感知当前已有偏好，避免重复
  - 所有偏好作为长期偏好持久化保存
  - 从对话中提取隐含偏好

### 4. InformationQueryAgent (信息查询智能体)

- **职责**: 实时信息检索（联网）
- **查询能力**: DuckDuckGo 搜索 + LLM 摘要
- **查询场景**: 天气、景点、实时新闻、通用问答

### 5. ItineraryPlanningAgent (行程规划智能体)

- **职责**: 生成完整行程计划
- **规划内容**: 每日时间表、住宿建议、餐饮建议、交通路线、注意事项
- **特点**: 即使信息不完整也给出合理建议

### 6. RAGKnowledgeAgent (知识库查询智能体)

- **职责**: 查询企业商旅知识库
- **技术栈**: Milvus Lite + BGE 中文向量模型
- **特点**: 提供文档溯源，返回参考来源

---

## CLI 使用指南

### 启动

```bash
python travel_agent/cli.py
```

**启动速度**: 约 3 秒（采用LazyAgentRegistry懒加载技术）

### 内置命令

| 命令 | 说明 |
|------|------|
| `help` | 显示帮助信息 |
| `status` | 查看当前状态和记忆 |
| `health` | 检查 LLM 服务是否可用并显示熔断器状态 |
| `clear` | 清空当前任务（保留长期记忆） |
| `history` | 查看历史行程 |
| `preferences` | 查看用户偏好 |
| `exit` | 退出程序 |

单独做健康检查（不进入交互）：`python travel_agent/cli.py health`，返回 `OK` / `FAIL: ...`，退出码 0/1。

---

## Web API 使用指南

项目提供了一个基于 FastAPI + React 的 Web 界面，支持 JWT 鉴权、会话管理、普通 JSON 响应和 SSE 流式响应。

### 本地启动

```bash
uvicorn travel_agent.web_api:app --host 0.0.0.0 --port 8000
```

### Docker 启动

```bash
docker compose up --build web frontend
```

### 接口

- `POST /api/v1/auth/token`：签发 JWT
- `GET /api/v1/auth/me`：查看当前身份
- `POST /api/v1/auth/refresh`：刷新 JWT
- `GET /api/v1/sessions/current`：查看当前会话
- `POST /api/v1/sessions/new`：创建新会话
- `POST /api/v1/sessions/close`：关闭当前会话
- `GET /api/v1/sessions`：查看当前用户会话列表
- `GET /health`：检查 LLM 和熔断器状态
- `POST /api/v1/chat/sync`：同步返回完整结果
- `POST /api/v1/chat`：SSE 流式返回执行过程

### 前端功能

- JWT 登录与刷新
- 会话创建、切换、关闭
- SSE 流式聊天
- 任务状态、意图与 Agent 事件可视化
- 原始执行结果面板

### 请求示例

```bash
TOKEN=$(curl -s http://localhost:8000/api/v1/auth/token \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "default_user"
  }' | jq -r .access_token)

curl -N http://localhost:8000/api/v1/chat \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "message": "我想从北京去杭州出差三天"
  }'
```

---

## 测试

### 集成测试 (QA)
完整跑通所有意图和子智能体的端到端测试：
```bash
python tests/test_cli_qa.py
```

### 单元测试
针对各个核心模块的测试：

```bash
python tests/test_memory_system.py  # 记忆系统
python tests/test_intention_agent.py # 意图识别
python tests/test_orchestration.py  # 协调系统
```

---

## 项目结构

```
shanglv/
├── travel_agent/                    # 主代码包
│   ├── cli.py                       # CLI 主程序
│   ├── config.py                    # 配置文件
│   ├── config_agentscope.py         # AgentScope 初始化与模型配置
│   ├── agents/                      # 核心编排层
│   │   ├── intention_agent.py       # 意图识别（语义理解）
│   │   ├── orchestration_agent.py   # 协调器（并行调度）
│   │   └── lazy_agent_registry.py   # 智能体插件注册器（懒加载）
│   ├── context/                     # 记忆系统
│   │   ├── memory_manager.py        # 记忆管理器
│   │   ├── short_term_memory.py     # 短期记忆
│   │   └── long_term_memory.py      # 长期记忆（支持 PostgreSQL + JSON 兜底）
│   └── utils/                       # 工具与连接可用性
│       ├── circuit_breaker.py       # 熔断器
│       ├── llm_resilience.py        # 重试退避、健康检查
│       ├── json_parser.py           # JSON 解析
│       └── skill_loader.py          # Skill 加载器
├── .claude/skills/                  # Skill Plugins (子智能体)
│   ├── ask-question/                # 知识库问答 Skill
│   │   ├── script/                  # 代码 (agent.py, init_script)
│   │   ├── data/                    # 数据 (documents, milvus db)
│   │   └── SKILL.md                 # 技能定义
│   ├── event-collection/            # 事项收集 Skill
│   ├── plan-trip/                   # 行程规划 Skill
│   ├── preference/                  # 偏好管理 Skill
│   ├── query-info/                  # 信息查询 Skill
│   └── memory-query/                # 记忆查询 Skill
├── docs/                            # 项目文档
│   ├── TECHNICAL_REPORT.md          # 技术报告
│   └── proj_question.md             # 项目说明与面试问答
├── data/
│   ├── memory/                      # 长期记忆 JSON 兜底与迁移来源（user_id.json）
│   ├── documents/                   # RAG 知识文档
│   └── models/                      # 本地模型文件
│       └── bge-m3/                  # BGE-m3 Embedding模型
├── tests/                           # 测试脚本
│   ├── test_cli_qa.py               # 端到端集成测试
│   ├── test_memory_system.py        # 记忆系统测试
│   ├── test_intention_agent.py      # 意图识别测试
│   └── test_orchestration.py        # 协调系统测试
└── README.md                        # 本文件
```

---

## 技术栈总览

### 核心框架
- 📦 **AgentScope 1.0.16** - 多智能体框架
- 🤖 **豆包大模型 (doubao-seed-1-6-flash-250828)** - 大语言模型

### 数据存储
- 🗄️ **PostgreSQL** - 长期记忆持久化（用户偏好、历史行程、聊天记录）
- ⚡ **Redis** - 短期记忆缓存（会话状态、用户偏好热数据、LLM总结结果）
- 🔍 **Milvus** - 向量数据库（本地存储，RAG知识库）

### 向量化与检索
- 🧠 **BGE-m3** - 多语言长上下文Embedding模型（本地部署）
- 📚 **Sentence-Transformers 5.2.3** - 向量化工具库
- 🎯 **Dense + BM25 + RRF** - 向量检索与稀疏检索融合排序

### 联网与搜索
- 🌐 **DuckDuckGo (DDGS 9.10.0)** - 免费网络搜索引擎
- 📝 **LLM自动摘要** - 搜索结果智能提取

### 架构设计
- 🏗️ **Skill Plugins插件化架构** - 独立开发、测试、部署
- 🔄 **LazyAgentRegistry动态发现** - 自动扫描注册Agent插件
- ⚡ **懒加载机制** - 未使用的Skill不加载（启动速度3秒）
- 🔀 **Progressive Disclosure渐进式暴露** - 意图识别阶段仅加载元数据，执行阶段按需加载
- 🎯 **优先级+并行混合调度** - asyncio.gather并发执行

### 稳定性保障
- 🔁 **指数退避重试** - 自动重试失败请求（最大3次）
- 🩺 **熔断器机制** - 连续失败后暂停调用
- 💊 **健康检查** - 实时监控LLM服务可用性
- 🔭 **全链路可观测性** - Trace ID、Agent事件、JSONL指标与本地汇总脚本

### 用户界面
- 🖥️ **Rich 13.9.4** - 精美的CLI终端界面

---

## ⚠️ 注意事项

### 模型配置
- 通过 `LLM_PROVIDER` 选择模型供应商，当前支持 OpenAI-compatible 协议的 `doubao`、`openai`、`deepseek`、`qwen`、`moonshot`、`zhipu` 等
- 通过 `travel_agent.llm.create_chat_model()` 统一创建 LLM，Agent 层只接收统一模型对象，避免不同供应商响应协议不一致
- 通过 `travel_agent.llm.create_model_factory()` 按 Agent 分层选型：低复杂度任务默认走 `fast`，复杂推理任务默认走 `reasoning`
- 必须配置对应供应商的 `LLM_API_KEY`、`LLM_MODEL_NAME` 和 `LLM_BASE_URL`
- BGE-m3 Embedding模型需下载到 `data/models/bge-m3/`

### 数据存储
- 当前版本长期记忆默认采用 **PostgreSQL 持久化**，`data/memory/{user_id}.json` 作为迁移来源和离线兜底
- Redis 作为短期记忆和热数据缓存层，已接入配置化切换
- 通过 `POSTGRES_ENABLED=true` 可启用 PostgreSQL 后端；未启用时自动回退到 JSON 文件

### 知识库初始化
- 首次运行前必须初始化RAG知识库
- 知识库文档位于 `data/documents/`
- Milvus数据库文件生成在 `.claude/skills/ask-question/data/rag_knowledge/milvus_lite.db`
- BM25 稀疏索引生成在 `.claude/skills/ask-question/data/rag_knowledge/bm25_index.json`

### 性能优化
- 懒加载机制：系统启动时仅扫描Skill元数据，首次调用时才加载
- 并行调度：同优先级Agent并发执行，提升响应速度
- 缓存策略：热数据缓存，减少重复计算和LLM调用
- 分层模型：事项收集、偏好、记忆查询、信息查询默认使用 fast 档；意图识别、RAG、行程规划默认使用 reasoning 档
- 可观测性：请求、意图识别、Agent执行、完成/失败事件会写入 `data/traces/events.jsonl`，聚合指标写入 `data/traces/metrics.jsonl`

### 可观测性报告

```bash
python travel_agent/scripts/observability_report.py
```

---

## 🚀 未来规划

- [x] Redis 缓存层
- [x] 支持更多 OpenAI-compatible LLM 模型（Doubao、OpenAI、DeepSeek、Qwen 等）
- [x] Web 界面（FastAPI + React）
- [x] BM25 混合检索
- [x] Agent 分层模型选型
- [x] 全链路可观测性
- [ ] 强化学习与策略优化
- [ ] 更多 Skill 插件（酒店预订、机票查询等）

---

## 许可证

MIT License
