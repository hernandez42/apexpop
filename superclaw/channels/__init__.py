"""
superclaw 消息通道系统 — 移植自 nanobot

架构：
    ┌──────────┐     ┌──────────┐     ┌──────────┐
    │  飞书     │     │ Telegram │     │  WebUI   │
    │ Channel  │     │ Channel  │     │ Channel  │
    └────┬─────┘     └────┬─────┘     └────┬─────┘
         │                │                │
         ▼                ▼                ▼
    ┌──────────────────────────────────────────┐
    │              MessageBus                   │
    │  inbound queue  ←  channels              │
    │  outbound queue →  channels              │
    └──────────────────┬───────────────────────┘
                       │
                       ▼
                 ┌──────────┐
                 │  Agent   │
                 │  Core    │
                 └──────────┘
"""
from .bus import MessageBus
from .events import InboundMessage, OutboundMessage
from .base import BaseChannel
from .manager import ChannelManager
from .console import ConsoleChannel

__all__ = [
    "MessageBus",
    "InboundMessage",
    "OutboundMessage",
    "BaseChannel",
    "ChannelManager",
    "ConsoleChannel",
]
