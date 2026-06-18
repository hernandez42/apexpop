"""
异步消息总线 — 移植自 nanobot/bus/queue.py

将聊天渠道与 Agent 核心解耦：
- inbound: 渠道 → Agent（收到的消息）
- outbound: Agent → 渠道（要发送的消息）
"""
import asyncio

from .events import InboundMessage, OutboundMessage


class MessageBus:
    """异步消息总线
    Channels push messages to inbound queue, agent processes them
    and pushes responses to outbound queue.
    """

    def __init__(self):
        self.inbound: asyncio.Queue[InboundMessage] = asyncio.Queue()
        self.outbound: asyncio.Queue[OutboundMessage] = asyncio.Queue()

    async def publish_inbound(self, msg: InboundMessage) -> None:
        """渠道 → Agent"""
        await self.inbound.put(msg)

    async def consume_inbound(self) -> InboundMessage:
        """Agent 消费下一条入站消息（阻塞直到有消息）"""
        return await self.inbound.get()

    async def publish_outbound(self, msg: OutboundMessage) -> None:
        """Agent → 渠道"""
        await self.outbound.put(msg)

    async def consume_outbound(self) -> OutboundMessage:
        """渠道消费下一条出站消息（阻塞直到有消息）"""
        return await self.outbound.get()

    @property
    def inbound_size(self) -> int:
        return self.inbound.qsize()

    @property
    def outbound_size(self) -> int:
        return self.outbound.qsize()

    def clear(self):
        """清空队列"""
        while not self.inbound.empty():
            self.inbound.get_nowait()
        while not self.outbound.empty():
            self.outbound.get_nowait()
