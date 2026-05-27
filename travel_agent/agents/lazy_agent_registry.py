#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
懒加载智能体注册器
基于 .claude/skills 的插件化发现机制。
"""
import importlib
import importlib.util
import inspect
import sys
from pathlib import Path
from typing import Dict, Any, Optional
from rich.console import Console
from agentscope.agent import AgentBase
from travel_agent.utils.skill_loader import SkillLoader

class LazyAgentRegistry:
    """
    懒加载智能体注册器 - 插件化版本

    启动时扫描 .claude/skills 下的 SKILL.md frontmatter，
    根据 agent_name / agent_module / agent_class 懒加载实现。
    """

    def __init__(self, model, cache: Dict, memory_manager=None, model_factory=None):
        """
        初始化懒加载注册器

        Args:
            model: 共享的 LLM 模型实例
            cache: 用于缓存已加载智能体的字典
            memory_manager: 记忆管理器 (可选，用于注入给需要它的 Agent)
            model_factory: 可选，按 agent_name 返回分层模型实例
        """
        self.model = model
        self.model_factory = model_factory
        self.cache = cache
        self.memory_manager = memory_manager
        self.console = Console()
        self.skill_loader = SkillLoader()
        self._plugin_specs: Dict[str, Dict[str, Any]] = {}
        self._alias_map: Dict[str, str] = {}
        self._discover_plugins()

    def _discover_plugins(self):
        """扫描技能目录，构建 agent_name -> plugin spec 映射。"""
        self._plugin_specs = {}
        self._alias_map = {}
        for _, spec in self.skill_loader.get_agent_specs().items():
            agent_name = spec["agent_name"]
            if agent_name not in self._plugin_specs:
                self._plugin_specs[agent_name] = spec
            for alias in spec.get("aliases", []):
                self._alias_map[alias] = agent_name

    def _resolve_agent_name(self, agent_name: str) -> Optional[str]:
        """解析智能体名称到 canonical agent_name"""
        if agent_name in self._plugin_specs:
            return agent_name
        return self._alias_map.get(agent_name)

    def __getitem__(self, agent_name: str):
        """获取智能体 (懒加载)"""
        if agent_name in self.cache:
            return self.cache[agent_name]

        canonical_name = self._resolve_agent_name(agent_name)
        if not canonical_name:
            raise KeyError(f"Agent '{agent_name}' not found")

        spec = self._plugin_specs[canonical_name]
        module_path = spec.get("agent_module")
        class_name = spec.get("agent_class")
        agent_file = spec.get("agent_file")
        skill_dir = spec.get("skill_dir")
        self.console.print(f"[dim]🔄 正在加载 {agent_name} ({module_path or agent_file})...[/dim]")
        
        try:
            module = None
            if module_path:
                module = importlib.import_module(module_path)
            elif agent_file:
                module = self._load_module_from_file(agent_file, canonical_name)
            else:
                candidate = Path(".claude/skills") / skill_dir / "script" / "agent.py"
                if candidate.exists():
                    module = self._load_module_from_file(str(candidate), canonical_name)

            if module is None:
                raise ValueError(f"No module configured for plugin {canonical_name}")

            agent_class = getattr(module, class_name, None) if class_name else None
            if agent_class is None or not inspect.isclass(agent_class) or not issubclass(agent_class, AgentBase):
                raise ValueError(f"No valid {class_name} found in plugin {canonical_name}")

            init_params = {
                "name": agent_name,
                "model": self.model_factory(canonical_name) if self.model_factory else self.model,
            }
            
            sig = inspect.signature(agent_class.__init__)
            if "memory_manager" in sig.parameters:
                init_params["memory_manager"] = self.memory_manager
                
            agent_instance = agent_class(**init_params)
            
            # 缓存
            self.cache[agent_name] = agent_instance
            self.console.print(f"[dim]✓ {agent_name} 加载完成[/dim]")
            
            return agent_instance
            
        except Exception as e:
            self.console.print(f"[red]✗ 加载 {agent_name} 失败: {e}[/red]")
            import traceback
            traceback.print_exc()
            raise

    def _load_module_from_file(self, file_path: str, module_key: str):
        """从文件路径动态加载插件模块。"""
        path = Path(file_path)
        if not path.is_absolute():
            path = Path.cwd() / path
        module_name = f"travel_agent.plugins.{module_key}"
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot load spec from {path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return module

    def __contains__(self, agent_name: str) -> bool:
        return self._resolve_agent_name(agent_name) is not None or agent_name in self.cache

    def get(self, agent_name: str, default=None):
        try:
            return self[agent_name]
        except KeyError:
            return default

    def keys(self):
        keys = set(self._plugin_specs.keys())
        keys.update(self._alias_map.keys())
        return list(keys)

    def values(self):
        return self.cache.values()

    def items(self):
        return self.cache.items()
        
    def get_loaded_agents(self) -> list:
        return list(self.cache.keys())
