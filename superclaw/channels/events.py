"""
消息事件类型 — 移植自 nanobot/bus/events.py

InboundMessage: 从渠道收到的消息
OutboundMessage: 要发送到渠道的消息
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional


@dataclass
class InboundMessage:
    """从聊天渠道收到的消息"""

    channel: str          # 渠道名: feishu / telegram / console / webui
    sender_id: str        # 发送者 ID
    chat_id: str          # 会话/群聊 ID
    content: str          # 消息文本
    timestamp: datetime = field(default_factory=datetime.now)
    media: List[str] = field(default_factory=list)        # 媒体 URL 列表
    metadata: dict = field(default_factory=dict)          # 渠道特定数据
    session_key_override: Optional[str] = None            # 会话 key 覆盖

    @property
    def session_key(self) -> str:
        """会话唯一标识"""
        return self.session_key_override or f"{self.channel}:{self.chat_id}"


@dataclass
class OutboundMessage:
    """要发送到聊天渠道的消息"""

    channel: str          # 目标渠道名
    chat_id: str          # 目标会话 ID
    content: str          # 消息文本
    reply_to: Optional[str] = None    # 回复的消息 ID
    media: List[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    buttons: List[List[str]] = field(default_factory=list)  # 按钮矩阵
