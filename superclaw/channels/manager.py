"""
渠道管理器 — 移植自 nanobot/channels/manager.py

职责:
- 初始化已启用的渠道（飞书/Telegram/Console 等）
- 启动/停止渠道
- 路由出站消息到对应渠道
- 消息发送重试（指数退避）
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional, Type

from .base import BaseChannel
from .bus import MessageBus
from .events import OutboundMessage
from .console import ConsoleChannel

logger = logging.getLogger(__name__)

# 消息发送重试延迟（指数退避: 1s, 2s, 4s）
_SEND_RETRY_DELAYS = (1, 2, 4)

# 可用渠道注册表
_CHANNEL_REGISTRY: Dict[str, Type[BaseChannel]] = {
    "console": ConsoleChannel,
}


def register_channel(name: str, cls: Type[BaseChannel]) -> None:
    """注册渠道类"""
    _CHANNEL_REGISTRY[name] = cls


def _try_import_feishu():
    """尝试导入飞书渠道（lark-oapi 可能未安装）"""
    try:
        from .feishu import FeishuChannel, FEISHU_AVAILABLE
        if FEISHU_AVAILABLE:
            register_channel("feishu", FeishuChannel)
            return True
    except ImportError:
        pass
    return False


# 启动时尝试注册飞书
_try_import_feishu()


class ChannelManager:
    """管理所有聊天渠道，协调消息路由

    使用方式:
        bus = MessageBus()
        manager = ChannelManager(config, bus)
        await manager.start_all()  # 启动所有渠道
        # ... 运行中 ...
        await manager.stop_all()   # 停止所有渠道
    """

    def __init__(self, config: Any, bus: MessageBus):
        """
        Args:
            config: 全局配置（包含 channels 部分）
            bus: 消息总线
        """
        self.config = config
        self.bus = bus
        self.channels: Dict[str, BaseChannel] = {}
        self._dispatch_task: Optional[asyncio.Task] = None
        self._init_channels()

    def _init_channels(self):
        """初始化所有已启用的渠道"""
        channels_config = self._get_channels_config()

        for name, channel_cls in _CHANNEL_REGISTRY.items():
            ch_config = channels_config.get(name, {})
            if isinstance(ch_config, dict) and not ch_config.get("enabled", False):
                # console 默认启用
                if name != "console":
                    continue
            elif not isinstance(ch_config, dict) and not getattr(ch_config, "enabled", False):
                if name != "console":
                    continue

            try:
                channel = channel_cls(ch_config, self.bus)
                self.channels[name] = channel
                logger.info(f"[ChannelManager] 已注册渠道: {name}")
            except Exception as e:
                logger.error(f"[ChannelManager] 初始化渠道 {name} 失败: {e}")

    def _get_channels_config(self) -> Dict[str, Any]:
        """从全局配置中获取渠道配置"""
        if isinstance(self.config, dict):
            return self.config.get("channels", {})
        return getattr(self.config, "channels", {}) or {}

    async def start_all(self) -> None:
        """启动所有已注册的渠道"""
        if not self.channels:
            logger.warning("[ChannelManager] 没有已启用的渠道")
            return

        # 启动出站消息分发器
        self._dispatch_task = asyncio.create_task(self._dispatch_outbound())

        # 启动所有渠道
        tasks = []
        for name, channel in self.channels.items():
            logger.info(f"[ChannelManager] 启动渠道: {name}...")
            tasks.append(asyncio.create_task(channel.start()))

        # 等待所有渠道（它们应该永久运行）
        await asyncio.gather(*tasks, return_exceptions=True)

    async def stop_all(self) -> None:
        """停止所有渠道"""
        # 停止出站分发
        if self._dispatch_task and not self._dispatch_task.done():
            self._dispatch_task.cancel()
            try:
                await self._dispatch_task
            except asyncio.CancelledError:
                pass

        # 停止所有渠道
        for name, channel in self.channels.items():
            try:
                await channel.stop()
                logger.info(f"[ChannelManager] 渠道 {name} 已停止")
            except Exception as e:
                logger.error(f"[ChannelManager] 停止渠道 {name} 失败: {e}")

    async def _dispatch_outbound(self) -> None:
        """出站消息分发循环 — 从 bus 消费消息，路由到对应渠道"""
        while True:
            try:
                msg: OutboundMessage = await self.bus.consume_outbound()

                channel = self.channels.get(msg.channel)
                if not channel:
                    logger.warning(
                        f"[ChannelManager] 渠道 {msg.channel} 不存在，丢弃消息"
                    )
                    continue

                # 带重试的发送
                await self._send_with_retry(channel, msg)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[ChannelManager] 出站分发错误: {e}")

    async def _send_with_retry(self, channel: BaseChannel,
                                msg: OutboundMessage) -> None:
        """带指数退避重试的消息发送"""
        last_error = None
        for attempt, delay in enumerate(_SEND_RETRY_DELAYS, 1):
            try:
                await channel.send(msg)
                return
            except Exception as e:
                last_error = e
                logger.warning(
                    f"[ChannelManager] 发送失败 (尝试 {attempt}/{len(_SEND_RETRY_DELAYS)}): {e}"
                )
                if attempt < len(_SEND_RETRY_DELAYS):
                    await asyncio.sleep(delay)

        logger.error(
            f"[ChannelManager] 消息发送最终失败 (渠道={msg.channel}, chat={msg.chat_id}): {last_error}"
        )

    @property
    def active_channels(self) -> list:
        """当前活跃的渠道名列表"""
        return [name for name, ch in self.channels.items() if ch.is_running]
