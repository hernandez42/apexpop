"""
渠道基类 — 移植自 nanobot/channels/base.py

每个渠道（飞书/Telegram/Console/WebUI）继承此类，
实现 start() / stop() / send() 方法。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from .bus import MessageBus
from .events import InboundMessage, OutboundMessage


class BaseChannel(ABC):
    """聊天渠道抽象基类

    子类必须实现:
        - start(): 启动渠道，监听消息
        - stop(): 停止渠道
        - send(msg): 发送消息
    """

    name: str = "base"
    display_name: str = "Base"
    send_progress: bool = True
    send_tool_hints: bool = False
    show_reasoning: bool = True

    def __init__(self, config: Any, bus: MessageBus):
        """
        Args:
            config: 渠道特定配置（dict 或对象）
            bus: 消息总线
        """
        self.config = config
        self.bus = bus
        self._running = False

    @abstractmethod
    async def start(self) -> None:
        """启动渠道，开始监听消息（长运行异步任务）"""
        pass

    @abstractmethod
    async def stop(self) -> None:
        """停止渠道，清理资源"""
        pass

    @abstractmethod
    async def send(self, msg: OutboundMessage) -> None:
        """通过此渠道发送消息"""
        pass

    async def send_delta(self, chat_id: str, delta: str,
                         metadata: Optional[Dict[str, Any]] = None) -> None:
        """流式发送文本块（子类可覆盖以支持流式）"""
        pass

    @property
    def supports_streaming(self) -> bool:
        """是否支持流式输出"""
        cfg = self.config
        streaming = (cfg.get("streaming", False)
                     if isinstance(cfg, dict)
                     else getattr(cfg, "streaming", False))
        return bool(streaming) and type(self).send_delta is not BaseChannel.send_delta

    def is_allowed(self, sender_id: str) -> bool:
        """检查发送者权限: * > allowlist > deny"""
        if isinstance(self.config, dict):
            allow_list = (self.config.get("allow_from")
                          or self.config.get("allowFrom")
                          or [])
        else:
            allow_list = getattr(self.config, "allow_from", None) or []

        if "*" in allow_list:
            return True
        if str(sender_id) in allow_list:
            return True
        return False

    async def _handle_message(
        self,
        sender_id: str,
        chat_id: str,
        content: str,
        media: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        session_key: Optional[str] = None,
        is_dm: bool = False,
    ) -> None:
        """处理入站消息: 权限检查 → 转发到 bus"""
        if not self.is_allowed(sender_id):
            # 未授权 — 在 DM 中提示
            if is_dm:
                await self.send(OutboundMessage(
                    channel=self.name,
                    chat_id=str(chat_id),
                    content="⚠️ 你没有权限使用此机器人。请联系管理员添加白名单。",
                ))
            return

        meta = metadata or {}
        if self.supports_streaming:
            meta = {**meta, "_wants_stream": True}

        msg = InboundMessage(
            channel=self.name,
            sender_id=str(sender_id),
            chat_id=str(chat_id),
            content=content,
            media=media or [],
            metadata=meta,
            session_key_override=session_key,
        )

        await self.bus.publish_inbound(msg)

    @classmethod
    def default_config(cls) -> Dict[str, Any]:
        """默认配置（子类可覆盖）"""
        return {"enabled": False}

    @property
    def is_running(self) -> bool:
        return self._running
