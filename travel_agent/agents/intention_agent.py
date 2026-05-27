"""
意图识别智能体 IntentionRecognitionAgent
职责：准确识别用户意图，并进行智能体调度

核心功能：
1. 多意图识别和分类：融合上下文对模糊意图进行消歧
2. 智能体调度决策：基于预定义的触发条件和业务规则，根据识别结果决定调用哪些子智能体
3. Query改写：标准化用户口语化的query输入，补全上下文信息，提取和重组关键信息
4. 显示推理：输出的两段式结构（推理过程 + JSON决策），提升意图识别准确度

架构：
- 使用单一LLM（用户配置的模型）
- 输入：用户query（自然语言）
- 输出：推理过程生成（包含reasoning+原因） + 多意图识别（原因） + 智能Query改写 + 构建结构化决策
"""
from agentscope.agent import AgentBase
from agentscope.message import Msg
from typing import Optional, Union, List, Dict, Any, Tuple
import json
import logging
import re
from datetime import datetime
from travel_agent.llm.sdk import extract_text_sync
from travel_agent.utils.skill_loader import SkillLoader

logger = logging.getLogger(__name__)


class IntentionAgent(AgentBase):
    """意图识别智能体（IntentionRecognitionAgent）"""

    MAX_DIALOGUE_CHARS = 800
    MAX_REWRITTEN_QUERY_CHARS = 240

    LEGACY_AGENT_ALIASES = {
        "memory-query": "memory_query",
        "plan-trip": "itinerary_planning",
        "query-info": "information_query",
        "ask-question": "rag_knowledge",
        "event-collection": "event_collection",
    }

    INTENT_DEFAULTS = {
        "memory_query": {
            "description": "查询历史记忆",
            "priority": 1,
            "expected_output": "历史记忆相关结果",
        },
        "event_collection": {
            "description": "收集行程要素",
            "priority": 1,
            "expected_output": "结构化行程要素",
        },
        "preference": {
            "description": "识别和更新用户偏好",
            "priority": 1,
            "expected_output": "偏好增量或覆盖信息",
        },
        "information_query": {
            "description": "查询实时或客观信息",
            "priority": 1,
            "expected_output": "实时查询结果和来源",
        },
        "rag_knowledge": {
            "description": "查询企业知识库",
            "priority": 1,
            "expected_output": "知识库检索结果",
        },
        "itinerary_planning": {
            "description": "生成完整行程规划",
            "priority": 2,
            "expected_output": "完整行程方案",
        },
    }

    RULE_PATTERNS = {
        "memory_query": [
            r"我(去过|到过|以前|之前|曾经|过去|历史)",
            r"(我的|我自己的).*(行程|偏好|家|酒店|航空|记录)",
            r"记得(我|之前|以前)",
            r"我.*(说过|提过|问过)",
        ],
        "preference": [
            r"(喜欢|偏好|常坐|常住|爱住|爱坐|只住|只坐)",
            r"(还喜欢|也喜欢|还想要|也想要)",
            r"(搬家到|改成|换成|现在住|现在是)",
            r"(酒店|航空|座位|餐食|机型|房型|预算)",
        ],
        "event_collection": [
            r"(从|去|到|前往|出发|往).*(出差|旅游|旅行|出行)",
            r"(出差|旅游|旅行|出行|行程|计划)",
            r"(什么时候|哪天|几天|多久|几月|几号)",
            r"(我要|我想|帮我).*(去|前往|飞到|到)",
        ],
        "information_query": [
            r"(天气|温度|限行|路况|航班|机票|酒店价格|最新|实时|今天|明天|现在)",
            r"(怎么样|有什么|哪些|哪里|多少)",
        ],
        "rag_knowledge": [
            r"(标准|政策|报销|规定|制度|知识库|FAQ|内部知识)",
            r"(差旅|旅规|商务旅行|公司规定)",
        ],
    }

    def __init__(self, name: str = "IntentionRecognitionAgent", model=None, **kwargs):
        super().__init__()
        self.name = name
        self.model = model
        self.skill_loader = SkillLoader()
        self._skill_prompt_cache: Optional[str] = None

    def _normalize_agent_name(self, agent_name: str) -> str:
        """将 LLM 输出的 legacy agent 名称标准化为内部名称"""
        return self.LEGACY_AGENT_ALIASES.get(agent_name, agent_name)

    def _extract_user_query_and_context(self, x: Union[Msg, List[Msg]]) -> Tuple[str, str]:
        """
        从输入消息中提取用户 query 和上下文。

        保留 system 记忆，但限制普通对话长度，避免 prompt 过大。
        """
        if isinstance(x, list):
            if not x:
                return "", "无历史对话"

            user_query = x[-1].content if hasattr(x[-1], "content") else str(x[-1])
            context_lines: List[str] = []

            for msg in x[:-1]:
                if not hasattr(msg, "content") or not hasattr(msg, "role"):
                    continue

                if msg.role == "system":
                    context_lines.append(f"[系统记忆]\n{msg.content}")
                    continue

                role_name = "用户" if msg.role == "user" else "助手"
                content = str(msg.content)
                if len(content) > self.MAX_DIALOGUE_CHARS:
                    content = content[:self.MAX_DIALOGUE_CHARS] + "..."
                context_lines.append(f"{role_name}: {content}")

            context_str = "\n".join(context_lines) if context_lines else "无历史对话"
            return user_query, context_str

        return (x.content if hasattr(x, "content") else str(x)), "无历史对话"

    def _get_current_time_block(self) -> str:
        """生成时间上下文，方便时间相关意图推断"""
        now = datetime.now()
        weekday = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"][now.weekday()]
        return f"{now.strftime('%Y年%m月%d日 %H:%M')} {weekday}"

    def _get_skill_prompt(self) -> str:
        """缓存技能描述，避免每次重复读取磁盘"""
        if self._skill_prompt_cache is None:
            self._skill_prompt_cache = self.skill_loader.get_skill_prompt()
        return self._skill_prompt_cache

    def _build_prompt(self, user_query: str, context_str: str) -> str:
        """构建意图识别提示词"""
        return f"""你是一个高级意图识别专家（IntentionRecognitionAgent）。请分析用户查询，识别意图并输出结构化决策。

【当前时间】
{self._get_current_time_block()}
（重要：当用户说"2月28日"、"明天"、"下周三"等相对时间时，请根据当前时间推断完整日期）

【用户Query】
{user_query}

【对话历史上下文】
{context_str}

【可调度的子智能体 (Skills)】
{self._get_skill_prompt()}

【重要 - 意图区分原则】
请基于语义理解判断意图，不要机械匹配关键词：
- "我去过北京吗？" → memory_query（询问自己的历史）
- "北京怎么样？" / "北京有什么好玩的？" → information_query（询问客观信息）
- "我想去北京" → itinerary_planning（规划未来行程）
- "我喜欢住汉庭" / "我还喜欢如家" → preference（偏好管理）

优先级规则：
- memory_query 优先于 information_query（当问题涉及用户自己的历史时）
- 如果用户明确询问"我的"、"我过去的"，必须识别为 memory_query
- 只有在明显需要实时/客观信息时，才调用 information_query

【任务要求】
请按以下步骤进行分析：

**第1步：推理过程生成**
- 分析用户query的核心诉求
- 识别query中的关键实体和意图信号
- 判断是否需要结合对话历史进行消歧
- 说明如何融合上下文信息进行推理

**第2步：多意图识别（原因）**
- 识别所有可能的用户意图（可以是多个）
- 为每个意图分配置信度（0-1之间）
- 说明为什么识别出该意图的原因

**第3步：智能Query改写**
- 识别口语化表达，进行标准化
- 补全省略的上下文信息
- 提取和重组关键信息

**第4步：构建结构化决策**
- 基于识别的意图，决定调用哪些子智能体
- 说明调用顺序和优先级
- 输出结构化的调用策略

【输出格式要求】
必须严格按照以下JSON格式输出（**只输出JSON，不要有其他文本**）：

{{
    "reasoning": "这里是详细的推理过程，包含第1步的分析，说明如何理解用户query，如何结合上下文，如何识别意图信号",
    "intents": [
        {{
            "type": "意图类型（如：itinerary_planning, preference, information_query等）",
            "confidence": 0.95,
            "description": "该意图的具体说明",
            "reason": "为什么识别出该意图的原因"
        }}
    ],
    "key_entities": {{
        "origin": "出发地（如果有）",
        "destination": "目的地（如果有）",
        "date": "日期（如果有）",
        "duration": "时长（如果有）",
        "other": "其他关键信息"
    }},
    "rewritten_query": "标准化、补全后的查询内容",
    "agent_schedule": [
        {{
            "agent_name": "子智能体名称",
            "priority": 1,
            "reason": "调用该智能体的原因和依据",
            "expected_output": "期望该智能体提供什么输出"
        }}
    ]
}}

【重要提示 - 优先级设置规则】
优先级数字相同的智能体会**并行执行**，不同优先级按顺序批次执行。

**所有智能体优先级分组：**

**Priority 1（并行执行）- 信息收集类：**
- memory_query: 记忆查询智能体
- event_collection: 事项收集智能体
- preference: 偏好管理智能体
- information_query: 信息查询智能体（联网搜索）
- rag_knowledge: RAG知识库智能体（查询企业知识库）

**Priority 2（依赖 Priority 1）- 行程规划类：**
- itinerary_planning: 行程规划智能体（需要事项收集的结果）

**说明：**
- Priority 1 的智能体都是信息获取，互不依赖，可并行执行提升速度
- Priority 2 的智能体需要使用 Priority 1 收集的信息
- 示例：用户说"我要从天津去北京，喜欢住汉庭"
  → Priority 1: preference + event_collection（并行）
  → Priority 2: itinerary_planning（使用 Priority 1 的结果）

请开始分析，直接输出JSON：
"""

    def _strip_code_fences(self, text: str) -> str:
        """移除 markdown 代码块包装"""
        text = text.strip()
        if text.startswith("```json"):
            text = text[7:]
        if text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]
        return text.strip()

    def _parse_json_payload(self, text: str) -> Dict[str, Any]:
        """解析 JSON；如果整体不是 JSON，尝试截取 JSON 主体"""
        cleaned = self._strip_code_fences(text)
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError as e1:
            start_idx = cleaned.find("{")
            end_idx = cleaned.rfind("}")
            if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
                json_str = cleaned[start_idx:end_idx + 1]
                try:
                    return json.loads(json_str)
                except json.JSONDecodeError as e2:
                    logger.error(f"JSON parse failed. Text sample: {json_str[:120]}")
                    raise ValueError(f"Failed to parse JSON. Error: {e2}") from e2
            raise ValueError(f"No JSON found in response. Parse error: {e1}") from e1

    def _normalize_intents(self, intents: Any, user_query: str) -> List[Dict[str, Any]]:
        """归一化 intents 列表"""
        if not isinstance(intents, list):
            return []

        normalized = []
        for item in intents:
            if not isinstance(item, dict):
                continue
            intent_type = self._normalize_agent_name(str(item.get("type", "")).strip())
            if not intent_type:
                continue

            confidence = item.get("confidence", 0.5)
            try:
                confidence = float(confidence)
            except (TypeError, ValueError):
                confidence = 0.5
            confidence = max(0.0, min(1.0, confidence))

            normalized.append({
                "type": intent_type,
                "confidence": confidence,
                "description": str(item.get("description", "")).strip(),
                "reason": str(item.get("reason", "")).strip(),
            })

        if not normalized:
            return []

        normalized.sort(key=lambda x: x["confidence"], reverse=True)
        return normalized

    def _dedupe_by_agent_name(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """按 agent_name 去重，保留首次出现的条目"""
        deduped = []
        seen = set()
        for item in items:
            agent_name = item.get("agent_name")
            if not agent_name or agent_name in seen:
                continue
            seen.add(agent_name)
            deduped.append(item)
        return deduped

    def _normalize_schedule(self, schedule: Any) -> List[Dict[str, Any]]:
        """归一化 agent_schedule 列表"""
        if not isinstance(schedule, list):
            return []

        normalized = []
        for item in schedule:
            if not isinstance(item, dict):
                continue
            agent_name = self._normalize_agent_name(str(item.get("agent_name", "")).strip())
            if not agent_name:
                continue
            try:
                priority = int(item.get("priority", 1))
            except (TypeError, ValueError):
                priority = 1

            normalized.append({
                "agent_name": agent_name,
                "priority": max(1, priority),
                "reason": str(item.get("reason", "")).strip(),
                "expected_output": str(item.get("expected_output", "")).strip(),
            })

        normalized.sort(key=lambda x: (x["priority"], x["agent_name"]))
        return self._dedupe_by_agent_name(normalized)

    def _normalize_entities(self, entities: Any) -> Dict[str, Any]:
        """归一化关键实体"""
        base = {"origin": "", "destination": "", "date": "", "duration": "", "other": ""}
        if isinstance(entities, dict):
            for key in base:
                value = entities.get(key, "")
                base[key] = value if value is not None else ""
        return base

    def _build_rewritten_query(self, user_query: str, entities: Dict[str, Any]) -> str:
        """生成标准化后的查询文本"""
        if not user_query:
            return ""

        destination = entities.get("destination") or ""
        origin = entities.get("origin") or ""
        date = entities.get("date") or ""
        duration = entities.get("duration") or ""
        other = entities.get("other") or ""

        parts = [user_query.strip()]
        if origin or destination or date or duration or other:
            extra = []
            if origin:
                extra.append(f"出发地={origin}")
            if destination:
                extra.append(f"目的地={destination}")
            if date:
                extra.append(f"日期={date}")
            if duration:
                extra.append(f"时长={duration}")
            if other:
                extra.append(f"其他={other}")
            parts.append("【补充信息】" + "，".join(extra))

        rewritten = " ".join(parts).strip()
        if len(rewritten) > self.MAX_REWRITTEN_QUERY_CHARS:
            rewritten = rewritten[:self.MAX_REWRITTEN_QUERY_CHARS] + "..."
        return rewritten

    def _extract_entities_rule_based(self, user_query: str) -> Dict[str, Any]:
        """基于规则的关键信息提取，供兜底和 query 改写使用"""
        entities = self._normalize_entities({})
        query = user_query.strip()

        travel_match = re.search(r"从(?P<origin>[^，。,.、\s]{1,12})[去到前往至]\s*(?P<destination>[^，。,.、\s]{1,12})", query)
        if travel_match:
            entities["origin"] = travel_match.group("origin")
            entities["destination"] = travel_match.group("destination")
        else:
            simple_match = re.search(r"(?P<origin>[^，。,.、\s]{1,12})[去到前往至]\s*(?P<destination>[^，。,.、\s]{1,12})", query)
            if simple_match and any(keyword in query for keyword in ["去", "到", "前往"]):
                entities["origin"] = simple_match.group("origin")
                entities["destination"] = simple_match.group("destination")

        date_match = re.search(
            r"(?P<date>(\d{4}年)?\d{1,2}月\d{1,2}[日号]?|明天|后天|今天|下周[一二三四五六日天]|周[一二三四五六日天]|星期[一二三四五六日天])",
            query,
        )
        if date_match:
            entities["date"] = date_match.group("date")

        duration_match = re.search(r"(?P<duration>\d+\s*(天|晚|小时|周|个月))", query)
        if duration_match:
            entities["duration"] = duration_match.group("duration").replace(" ", "")

        if any(keyword in query for keyword in ["出差", "旅游", "旅行", "出行", "行程", "计划"]):
            entities["other"] = "出行相关"

        return entities

    def _build_fallback_result(self, user_query: str, context_str: str, reason: str) -> Dict[str, Any]:
        """在模型不可用或返回异常时，基于规则生成兜底结果"""
        lower_query = user_query.lower()
        combined_text = f"{user_query} {context_str}"

        # 规则回退只保留少量高置信意图，避免把错误放大到下游调度。
        detected = []
        for intent_type in ["memory_query", "preference", "event_collection", "information_query", "rag_knowledge"]:
            patterns = self.RULE_PATTERNS.get(intent_type, [])
            if any(re.search(pattern, combined_text, flags=re.IGNORECASE) for pattern in patterns):
                detected.append(intent_type)

        if not detected:
            detected = ["information_query"]

        intents = []
        schedule = []

        for intent_type in detected:
            meta = self.INTENT_DEFAULTS[intent_type]
            confidence = 0.92 if intent_type != "information_query" else 0.78
            intents.append({
                "type": intent_type,
                "confidence": confidence,
                "description": meta["description"],
                "reason": f"规则回退命中: {intent_type}",
            })
            schedule.append({
                "agent_name": intent_type,
                "priority": meta["priority"],
                "reason": f"规则回退触发 {intent_type}",
                "expected_output": meta["expected_output"],
            })

        if "itinerary_planning" not in {item["agent_name"] for item in schedule} and (
            any(keyword in lower_query for keyword in ["去", "出差", "旅游", "行程", "出行", "计划", "前往", "从"])
            and any(keyword in lower_query for keyword in ["我要", "我想", "帮我", "安排", "规划"])
        ):
            # 行程类请求需要先收集要素，再进入规划智能体。
            intents.append({
                "type": "itinerary_planning",
                "confidence": 0.88,
                "description": self.INTENT_DEFAULTS["itinerary_planning"]["description"],
                "reason": "规则回退识别为行程规划",
            })
            schedule.append({
                "agent_name": "event_collection",
                "priority": 1,
                "reason": "先收集行程要素",
                "expected_output": "结构化行程要素",
            })
            schedule.append({
                "agent_name": "itinerary_planning",
                "priority": 2,
                "reason": "基于行程要素生成方案",
                "expected_output": "完整行程方案",
            })

        intents = self._normalize_intents(intents, user_query)
        schedule = self._dedupe_by_agent_name(sorted(schedule, key=lambda x: (x["priority"], x["agent_name"])))
        entities = self._extract_entities_rule_based(user_query)
        return self._normalize_result({
            "reasoning": f"{reason}；使用规则回退策略完成意图识别。",
            "intents": intents,
            "key_entities": entities,
            "rewritten_query": self._build_rewritten_query(user_query, entities),
            "agent_schedule": schedule,
        }, user_query)

    def _normalize_result(self, result: Any, user_query: str) -> Dict[str, Any]:
        """对模型输出做归一化，保证下游可消费"""
        if not isinstance(result, dict):
            raise ValueError("Result is not a JSON object")

        reasoning = str(result.get("reasoning", "")).strip()
        entities = self._normalize_entities(result.get("key_entities", {}))
        intents = self._normalize_intents(result.get("intents", []), user_query)
        schedule = self._normalize_schedule(result.get("agent_schedule", []))

        rewritten_query = str(result.get("rewritten_query", "")).strip() or self._build_rewritten_query(user_query, entities)
        if len(rewritten_query) > self.MAX_REWRITTEN_QUERY_CHARS:
            rewritten_query = rewritten_query[:self.MAX_REWRITTEN_QUERY_CHARS] + "..."

        if not intents:
            intents = [{
                "type": "information_query",
                "confidence": 0.5,
                "description": self.INTENT_DEFAULTS["information_query"]["description"],
                "reason": "模型未返回有效 intents，使用默认信息查询",
            }]

        if not schedule:
            schedule = [{
                "agent_name": "information_query",
                "priority": 1,
                "reason": "默认调度信息查询",
                "expected_output": self.INTENT_DEFAULTS["information_query"]["expected_output"],
            }]

        return {
            "reasoning": reasoning or "意图识别完成。",
            "intents": intents,
            "key_entities": entities,
            "rewritten_query": rewritten_query,
            "agent_schedule": schedule,
        }

    async def reply(self, x: Optional[Union[Msg, List[Msg]]] = None) -> Msg:
        """
        意图识别主流程
        1. 推理过程生成
        2. 多意图识别
        3. 智能Query改写
        4. 构建结构化决策
        """
        if x is None:
            return Msg(name=self.name, content=json.dumps({}), role="assistant")

        # 统一抽取 query 和上下文，避免 LLM 提示词在入口处散落。
        user_query, context_str = self._extract_user_query_and_context(x)
        if not str(user_query).strip():
            return Msg(
                name=self.name,
                content=json.dumps(self._build_fallback_result("", context_str, "空输入")),
                role="assistant",
            )

        try:
            prompt = self._build_prompt(user_query, context_str)
            messages = [
                {"role": "system", "content": "你是一个高级意图识别专家。只输出JSON格式的结果，不要输出其他文本。"},
                {"role": "user", "content": prompt}
            ]
            if self.model is None:
                raise RuntimeError("IntentionAgent model is not initialized")

            response = await self.model(messages)
            text = extract_text_sync(response).strip()
            # 先做 JSON 解析，再做字段归一化，保证下游调度契约稳定。
            result = self._normalize_result(self._parse_json_payload(text), user_query)
        except Exception as e:
            logger.error(f"Intent recognition failed: {e}")
            result = self._build_fallback_result(user_query, context_str, f"意图识别出错: {e}")

        return Msg(name=self.name, content=json.dumps(result, ensure_ascii=False), role="assistant")
