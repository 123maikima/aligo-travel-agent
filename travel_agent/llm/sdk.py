"""Unified LLM SDK facade.

This module isolates provider-specific chat model creation and normalizes
message/response shapes for the Agent layer.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional


class LLMProviderError(ValueError):
    """Raised when the configured LLM provider cannot be created."""


@dataclass
class UnifiedLLMResponse:
    """Provider-neutral response object exposed to Agents."""

    content: str
    raw: Any = None
    provider: str = ""
    model_name: str = ""

    @property
    def text(self) -> str:
        return self.content


class UnifiedChatModel:
    """Callable chat model wrapper with normalized protocol.

    Agents call this object with either dict messages, AgentScope Msg objects,
    or plain strings. The wrapper converts them to OpenAI-style messages and
    returns a stable response object with `.content` and `.text`.
    """

    OPENAI_COMPATIBLE_PROVIDERS = {
        "openai",
        "openai_compatible",
        "doubao",
        "volcengine",
        "ark",
        "deepseek",
        "qwen",
        "dashscope",
        "moonshot",
        "kimi",
        "zhipu",
        "glm",
        "custom",
    }

    def __init__(
        self,
        *,
        provider: str,
        model_name: str,
        api_key: str,
        base_url: str,
        temperature: float = 0.7,
        max_tokens: int = 2000,
        timeout: float = 60.0,
        extra: Optional[Dict[str, Any]] = None,
    ):
        self.provider = (provider or "openai_compatible").strip().lower()
        self.model_name = model_name
        self.api_key = api_key
        self.base_url = base_url
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.extra = extra or {}
        self._client = self._create_provider_client()

    def _create_provider_client(self):
        if self.provider not in self.OPENAI_COMPATIBLE_PROVIDERS:
            raise LLMProviderError(
                f"Unsupported LLM_PROVIDER '{self.provider}'. "
                "Use an OpenAI-compatible provider such as doubao/openai/deepseek/qwen, "
                "or expose the provider through an OpenAI-compatible gateway."
            )

        try:
            from agentscope.model import OpenAIChatModel
        except ImportError as exc:  # pragma: no cover - environment issue
            raise LLMProviderError("AgentScope is required for OpenAI-compatible LLMs") from exc

        return OpenAIChatModel(
            model_name=self.model_name,
            api_key=self.api_key,
            client_kwargs={
                "base_url": self.base_url,
                "timeout": float(self.timeout),
            },
            generate_kwargs={
                "temperature": self.temperature,
                "max_tokens": self.max_tokens,
            },
        )

    @staticmethod
    def _normalize_content(content: Any) -> str:
        if content is None:
            return ""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: List[str] = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    if "text" in item:
                        parts.append(str(item.get("text") or ""))
                    elif item.get("type") == "text":
                        parts.append(str(item.get("content") or ""))
                    else:
                        parts.append(str(item))
                else:
                    parts.append(str(item))
            return "\n".join(part for part in parts if part)
        return str(content)

    def normalize_messages(self, messages: Any) -> List[Dict[str, str]]:
        if isinstance(messages, str):
            return [{"role": "user", "content": messages}]

        if isinstance(messages, dict):
            messages = [messages]

        if not isinstance(messages, Iterable):
            return [{"role": "user", "content": str(messages)}]

        normalized: List[Dict[str, str]] = []
        for message in messages:
            if isinstance(message, dict):
                role = str(message.get("role") or "user")
                content = self._normalize_content(message.get("content", ""))
            elif hasattr(message, "role") and hasattr(message, "content"):
                role = str(getattr(message, "role") or "user")
                content = self._normalize_content(getattr(message, "content", ""))
            else:
                role = "user"
                content = self._normalize_content(message)

            if role not in {"system", "user", "assistant", "tool"}:
                role = "user"
            normalized.append({"role": role, "content": content})

        return normalized or [{"role": "user", "content": ""}]

    async def _extract_text(self, response: Any) -> str:
        if response is None:
            return ""

        if hasattr(response, "__aiter__"):
            chunks: List[str] = []
            async for chunk in response:
                text = self._extract_text_sync(chunk)
                if text:
                    chunks.append(text)
            return "".join(chunks)

        return self._extract_text_sync(response)

    def _extract_text_sync(self, response: Any) -> str:
        if response is None:
            return ""
        if isinstance(response, str):
            return response
        if isinstance(response, dict):
            if "content" in response:
                return self._normalize_content(response.get("content"))
            if "text" in response:
                return self._normalize_content(response.get("text"))
            return str(response)
        if hasattr(response, "text"):
            return self._normalize_content(getattr(response, "text"))
        if hasattr(response, "content"):
            return self._normalize_content(getattr(response, "content"))
        return str(response)

    async def __call__(self, messages: Any) -> UnifiedLLMResponse:
        normalized_messages = self.normalize_messages(messages)
        raw_response = await self._client(normalized_messages)
        content = await self._extract_text(raw_response)
        return UnifiedLLMResponse(
            content=content,
            raw=raw_response,
            provider=self.provider,
            model_name=self.model_name,
        )


def create_chat_model(
    config: Optional[Dict[str, Any]] = None,
    *,
    tier: Optional[str] = None,
    agent_name: Optional[str] = None,
    timeout: Optional[float] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
) -> UnifiedChatModel:
    """Create the configured chat model through the unified SDK facade."""
    if config is None:
        from travel_agent.config import LLM_CONFIG, LLM_MODEL_PROFILES, SYSTEM_CONFIG

        selected_tier = tier or resolve_model_tier(agent_name)
        config = dict(LLM_MODEL_PROFILES.get(selected_tier, LLM_CONFIG))
        config["tier"] = selected_tier
        default_timeout = float(SYSTEM_CONFIG.get("timeout", 60))
    else:
        config = dict(config)
        default_timeout = 60.0

    return UnifiedChatModel(
        provider=config.get("provider", "openai_compatible"),
        model_name=config.get("model_name", ""),
        api_key=config.get("api_key", ""),
        base_url=config.get("base_url", ""),
        temperature=temperature if temperature is not None else float(config.get("temperature", 0.7)),
        max_tokens=max_tokens if max_tokens is not None else int(config.get("max_tokens", 2000)),
        timeout=timeout if timeout is not None else float(config.get("timeout", default_timeout)),
        extra=config,
    )


def resolve_model_tier(agent_name: Optional[str] = None, default: str = "default") -> str:
    """Resolve the model tier for an Agent or internal task."""
    from travel_agent.config import AGENT_MODEL_TIERS, LLM_MODEL_PROFILES

    key = (agent_name or default or "default").strip().lower()
    tier = AGENT_MODEL_TIERS.get(key, AGENT_MODEL_TIERS.get("default", "default"))
    if tier not in LLM_MODEL_PROFILES:
        tier = "default"
    return tier


class LLMModelFactory:
    """Cache and create model instances by tier.

    The factory lets orchestration choose different LLMs for different Agents
    while keeping each Agent's call protocol unchanged.
    """

    def __init__(
        self,
        *,
        timeout: Optional[float] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ):
        self.timeout = timeout
        self.temperature = temperature
        self.max_tokens = max_tokens
        self._cache: Dict[str, UnifiedChatModel] = {}

    def __call__(
        self,
        agent_name: Optional[str] = None,
        *,
        tier: Optional[str] = None,
    ) -> UnifiedChatModel:
        selected_tier = tier or resolve_model_tier(agent_name)
        if selected_tier not in self._cache:
            self._cache[selected_tier] = create_chat_model(
                tier=selected_tier,
                timeout=self.timeout,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
            )
        return self._cache[selected_tier]

    def get_profile_summary(self) -> Dict[str, Dict[str, str]]:
        from travel_agent.config import LLM_MODEL_PROFILES

        summary: Dict[str, Dict[str, str]] = {}
        for tier, config in LLM_MODEL_PROFILES.items():
            summary[tier] = {
                "provider": str(config.get("provider", "")),
                "model_name": str(config.get("model_name", "")),
            }
        return summary


def create_model_factory(
    *,
    timeout: Optional[float] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
) -> LLMModelFactory:
    return LLMModelFactory(
        timeout=timeout,
        temperature=temperature,
        max_tokens=max_tokens,
    )
