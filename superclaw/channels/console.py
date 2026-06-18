"""
Console 渠道 — 终端交互（内置，无需外部依赖）

用于本地测试和 CLI 交互模式。
"""
import asyncio
from typing import Any, Optional

from .base import BaseChannel
from .bus import MessageBus
from .events import OutboundMessage


class ConsoleChannel(BaseChannel):
    """终端控制台渠道

    从 stdin 读取用户输入，输出到 stdout。
    用于本地测试和 CLI 模式。
    """

    name = "console"
    display_name = "Console"

    def __init__(self, config: Any, bus: MessageBus):
        super().__init__(config, bus)
        self._reader_task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        """启动控制台监听"""
        self._running = True
        print("[Console] ✅ 渠道已启动（输入 'exit' 退出）")

        # 异步读取 stdin
        self._reader_task = asyncio.create_task(self._read_loop())

    async def _read_loop(self) -> None:
        """读取 stdin 循环"""
        loop = asyncio.get_event_loop()
        while self._running:
            try:
                # 异步读取一行
                line = await loop.run_in_executor(None, input, "你: ")
                line = line.strip()

                if not line:
                    continue
                if line in ("exit", "quit", "q"):
                    self._running = False
                    break

                # 转发到 bus
                await self._handle_message(
                    sender_id="console-user",
                    chat_id="console",
                    content=line,
                    is_dm=True,
                )

            except (EOFError, KeyboardInterrupt):
                print()
                self._running = False
                break

    async def stop(self) -> None:
        """停止控制台"""
        self._running = False
        if self._reader_task and not self._reader_task.done():
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass

    async def send(self, msg: OutboundMessage) -> None:
        """发送消息到 stdout"""
        print(f"\n🦖: {msg.content}")
        if msg.buttons:
            for row in msg.buttons:
                print(f"  [{' | '.join(row)}]")
        print()
