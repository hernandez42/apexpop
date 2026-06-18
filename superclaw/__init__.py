"""
superclaw — 轻量级 AI Agent 框架
灵感来自 nanobot / picoclaw / Evolver
设计目标：代码极简、真实可运行、不造假
"""
__version__ = "2.3.0"
__logo__ = "🦖"

from .agent import Agent, AgentResult
from .providers import get_provider, list_providers
from .tools import ToolRegistry, tool, scan_skills
from .session import SessionManager
from .memory import MemoryStore, SelfReflection, KnowledgeIndex, EvolutionHistory
from .config import load_config
from .llm_router import LLMRouter, get_router, CompletionResult, ProviderConfig
from .channels import (
    MessageBus, InboundMessage, OutboundMessage,
    BaseChannel, ChannelManager, ConsoleChannel,
)
from .gep_schema import Gene, Capsule, EvolutionEvent, Signal, GeneLibrary
from .gep_engine import GEPEngine, SignalExtractor, StrategyManager

__all__ = [
    "Agent",
    "AgentResult",
    "get_provider",
    "list_providers",
    "ToolRegistry",
    "tool",
    "scan_skills",
    "SessionManager",
    "MemoryStore",
    "SelfReflection",
    "KnowledgeIndex",
    "EvolutionHistory",
    "load_config",
    "LLMRouter",
    "get_router",
    "CompletionResult",
    "ProviderConfig",
    "MessageBus",
    "InboundMessage",
    "OutboundMessage",
    "BaseChannel",
    "ChannelManager",
    "ConsoleChannel",
    "Gene",
    "Capsule",
    "EvolutionEvent",
    "Signal",
    "GeneLibrary",
    "GEPEngine",
    "SignalExtractor",
    "StrategyManager",
]
