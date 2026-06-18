"""测试 superclaw.channels — 消息总线/事件/控制台渠道/渠道管理器/权限"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from superclaw.channels import (
    MessageBus,
    InboundMessage,
    OutboundMessage,
    BaseChannel,
    ChannelManager,
    ConsoleChannel,
)
from superclaw.channels.manager import (
    _CHANNEL_REGISTRY,
    _SEND_RETRY_DELAYS,
    register_channel,
)


# ============================================================
# 辅助：最小可用的 BaseChannel 子类（用于测试）
# ============================================================

class _StubChannel(BaseChannel):
    """最小可用的渠道实现，用于测试 BaseChannel 行为"""
    name = "stub"

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def send(self, msg: OutboundMessage) -> None:
        pass


# ============================================================
# InboundMessage / OutboundMessage dataclass
# ============================================================

def test_inbound_message_defaults():
    msg = InboundMessage(channel="console", sender_id="u1", chat_id="c1", content="hi")
    assert msg.channel == "console"
    assert msg.sender_id == "u1"
    assert msg.media == []
    assert msg.metadata == {}
    assert msg.session_key_override is None


def test_inbound_message_session_key():
    msg = InboundMessage(channel="console", sender_id="u1", chat_id="c1", content="hi")
    assert msg.session_key == "console:c1"


def test_inbound_message_session_key_override():
    msg = InboundMessage(
        channel="console", sender_id="u1", chat_id="c1", content="hi",
        session_key_override="custom-session",
    )
    assert msg.session_key == "custom-session"


def test_outbound_message_defaults():
    msg = OutboundMessage(channel="console", chat_id="c1", content="hi")
    assert msg.reply_to is None
    assert msg.media == []
    assert msg.metadata == {}
    assert msg.buttons == []


def test_outbound_message_with_fields():
    msg = OutboundMessage(
        channel="feishu", chat_id="c1", content="hello",
        reply_to="msg-123",
        media=["http://img.png"],
        buttons=[["A", "B"], ["C"]],
    )
    assert msg.reply_to == "msg-123"
    assert len(msg.media) == 1
    assert msg.buttons == [["A", "B"], ["C"]]


# ============================================================
# MessageBus — 异步入队/出队
# ============================================================

def test_message_bus_inbound_enqueue_dequeue():
    async def run():
        bus = MessageBus()
        msg = InboundMessage(channel="t", sender_id="u", chat_id="c", content="hi")
        await bus.publish_inbound(msg)
        assert bus.inbound_size == 1
        received = await bus.consume_inbound()
        assert received is msg
        assert bus.inbound_size == 0

    asyncio.run(run())


def test_message_bus_outbound_enqueue_dequeue():
    async def run():
        bus = MessageBus()
        msg = OutboundMessage(channel="t", chat_id="c", content="out")
        await bus.publish_outbound(msg)
        assert bus.outbound_size == 1
        received = await bus.consume_outbound()
        assert received is msg
        assert bus.outbound_size == 0

    asyncio.run(run())


def test_message_bus_fifo_order():
    async def run():
        bus = MessageBus()
        for i in range(3):
            await bus.publish_inbound(
                InboundMessage(channel="t", sender_id="u", chat_id="c", content=str(i))
            )
        assert bus.inbound_size == 3
        order = []
        while bus.inbound_size > 0:
            m = await bus.consume_inbound()
            order.append(m.content)
        assert order == ["0", "1", "2"]

    asyncio.run(run())


def test_message_bus_clear():
    async def run():
        bus = MessageBus()
        await bus.publish_inbound(InboundMessage(channel="t", sender_id="u", chat_id="c", content="a"))
        await bus.publish_outbound(OutboundMessage(channel="t", chat_id="c", content="b"))
        assert bus.inbound_size == 1
        assert bus.outbound_size == 1
        bus.clear()
        assert bus.inbound_size == 0
        assert bus.outbound_size == 0

    asyncio.run(run())


def test_message_bus_initially_empty():
    bus = MessageBus()
    assert bus.inbound_size == 0
    assert bus.outbound_size == 0


# ============================================================
# BaseChannel — 权限检查（allow_from 白名单）
# ============================================================

def test_is_allowed_whitelist():
    bus = MessageBus()
    ch = _StubChannel({"allow_from": ["user1", "user2"]}, bus)
    assert ch.is_allowed("user1") is True
    assert ch.is_allowed("user2") is True
    assert ch.is_allowed("user3") is False


def test_is_allowed_wildcard():
    bus = MessageBus()
    ch = _StubChannel({"allow_from": ["*"]}, bus)
    assert ch.is_allowed("anyone") is True
    assert ch.is_allowed("12345") is True


def test_is_allowed_empty_list():
    bus = MessageBus()
    ch = _StubChannel({"allow_from": []}, bus)
    assert ch.is_allowed("anyone") is False


def test_is_allowed_no_config():
    bus = MessageBus()
    ch = _StubChannel({}, bus)
    assert ch.is_allowed("anyone") is False


def test_is_allowed_allowFrom_camelcase():
    """allowFrom 驼峰命名也应支持"""
    bus = MessageBus()
    ch = _StubChannel({"allowFrom": ["user1"]}, bus)
    assert ch.is_allowed("user1") is True
    assert ch.is_allowed("user2") is False


def test_is_allowed_object_config():
    """非 dict 配置（对象属性）也应支持"""
    bus = MessageBus()

    class ObjConfig:
        allow_from = ["user1"]

    ch = _StubChannel(ObjConfig(), bus)
    assert ch.is_allowed("user1") is True
    assert ch.is_allowed("user2") is False


# ============================================================
# BaseChannel._handle_message — 权限检查 → 转发到 bus
# ============================================================

def test_handle_message_accepts_authorized():
    async def run():
        bus = MessageBus()
        ch = _StubChannel({"allow_from": ["user1"]}, bus)
        await ch._handle_message("user1", "chat1", "hello")
        assert bus.inbound_size == 1
        msg = await bus.consume_inbound()
        assert msg.content == "hello"
        assert msg.sender_id == "user1"
        assert msg.channel == "stub"

    asyncio.run(run())


def test_handle_message_rejects_unauthorized():
    async def run():
        bus = MessageBus()
        ch = _StubChannel({"allow_from": ["user1"]}, bus)
        await ch._handle_message("hacker", "chat1", "hi", is_dm=True)
        assert bus.inbound_size == 0

    asyncio.run(run())


def test_handle_message_with_session_override():
    async def run():
        bus = MessageBus()
        ch = _StubChannel({"allow_from": ["*"]}, bus)
        await ch._handle_message("u1", "c1", "hi", session_key="override-key")
        msg = await bus.consume_inbound()
        assert msg.session_key == "override-key"

    asyncio.run(run())


# ============================================================
# ConsoleChannel — start/stop/send
# ============================================================

def test_console_channel_send(capsys):
    async def run():
        bus = MessageBus()
        ch = ConsoleChannel({"allow_from": ["*"]}, bus)
        msg = OutboundMessage(channel="console", chat_id="c1", content="hello world")
        await ch.send(msg)

    asyncio.run(run())
    captured = capsys.readouterr()
    assert "hello world" in captured.out


def test_console_channel_send_with_buttons(capsys):
    async def run():
        bus = MessageBus()
        ch = ConsoleChannel({}, bus)
        msg = OutboundMessage(
            channel="console", chat_id="c1", content="choose",
            buttons=[["A", "B"], ["C"]],
        )
        await ch.send(msg)

    asyncio.run(run())
    captured = capsys.readouterr()
    assert "choose" in captured.out
    assert "A" in captured.out
    assert "C" in captured.out


def test_console_channel_start_stop_and_forward(monkeypatch):
    """启动控制台 → 读取输入 → 转发到 bus → exit 退出 → 停止"""
    async def run():
        bus = MessageBus()
        ch = ConsoleChannel({"allow_from": ["*"]}, bus)

        inputs = iter(["hello from user", "exit"])

        def fake_input(prompt):
            return next(inputs)

        monkeypatch.setattr("builtins.input", fake_input)

        await ch.start()
        # 等待 reader 处理两条输入
        for _ in range(100):
            if not ch.is_running:
                break
            await asyncio.sleep(0.02)

        # "hello from user" 应被转发到 bus
        assert bus.inbound_size == 1
        msg = await bus.consume_inbound()
        assert msg.content == "hello from user"
        assert msg.channel == "console"
        assert msg.sender_id == "console-user"
        # "exit" 已使 reader 退出
        assert not ch.is_running

        await ch.stop()
        assert not ch.is_running

    asyncio.run(asyncio.wait_for(run(), timeout=10))


def test_console_channel_stop_without_start():
    async def run():
        bus = MessageBus()
        ch = ConsoleChannel({"allow_from": ["*"]}, bus)
        # stop 在 start 之前调用不应报错
        await ch.stop()
        assert not ch.is_running

    asyncio.run(run())


def test_console_channel_eof_stops(monkeypatch):
    """EOFError 应停止 reader"""
    async def run():
        bus = MessageBus()
        ch = ConsoleChannel({"allow_from": ["*"]}, bus)

        def fake_input(prompt):
            raise EOFError()

        monkeypatch.setattr("builtins.input", fake_input)

        await ch.start()
        for _ in range(100):
            if not ch.is_running:
                break
            await asyncio.sleep(0.02)
        assert not ch.is_running
        await ch.stop()

    asyncio.run(asyncio.wait_for(run(), timeout=10))


# ============================================================
# ChannelManager — 注册/初始化/路由/重试
# ============================================================

def test_channel_manager_inits_console_by_default():
    """console 渠道默认启用（即使配置里没写 enabled）"""
    bus = MessageBus()
    manager = ChannelManager({"channels": {}}, bus)
    assert "console" in manager.channels


def test_channel_manager_inits_enabled_channels():
    bus = MessageBus()
    config = {"channels": {"console": {"enabled": True, "allow_from": ["*"]}}}
    manager = ChannelManager(config, bus)
    assert "console" in manager.channels
    assert isinstance(manager.channels["console"], ConsoleChannel)


def test_channel_manager_disabled_channel_not_inited():
    """显式禁用的渠道不初始化（console 除外）"""
    bus = MessageBus()
    # 注册一个自定义渠道用于测试
    original = _CHANNEL_REGISTRY.copy()
    try:
        register_channel("stub", _StubChannel)
        config = {"channels": {"stub": {"enabled": False}}}
        manager = ChannelManager(config, bus)
        assert "stub" not in manager.channels
    finally:
        _CHANNEL_REGISTRY.clear()
        _CHANNEL_REGISTRY.update(original)


def test_channel_manager_object_config():
    """非 dict 配置对象也应工作"""
    bus = MessageBus()

    class Cfg:
        channels = {"console": {"enabled": True, "allow_from": ["*"]}}

    manager = ChannelManager(Cfg(), bus)
    assert "console" in manager.channels


def test_register_channel_adds_to_registry():
    original = _CHANNEL_REGISTRY.copy()
    try:
        register_channel("test_custom_ch", _StubChannel)
        assert _CHANNEL_REGISTRY["test_custom_ch"] is _StubChannel
    finally:
        _CHANNEL_REGISTRY.clear()
        _CHANNEL_REGISTRY.update(original)


def test_channel_manager_inits_registered_channel():
    original = _CHANNEL_REGISTRY.copy()
    try:
        register_channel("stub", _StubChannel)
        bus = MessageBus()
        config = {"channels": {"stub": {"enabled": True, "allow_from": ["*"]}}}
        manager = ChannelManager(config, bus)
        assert "stub" in manager.channels
        assert isinstance(manager.channels["stub"], _StubChannel)
    finally:
        _CHANNEL_REGISTRY.clear()
        _CHANNEL_REGISTRY.update(original)


def test_send_with_retry_succeeds_first_attempt():
    async def run():
        bus = MessageBus()
        manager = ChannelManager({"channels": {}}, bus)
        sent = []

        class OkChannel(_StubChannel):
            name = "ok"
            async def send(self, msg):
                sent.append(msg)

        ch = OkChannel({}, bus)
        msg = OutboundMessage(channel="ok", chat_id="c1", content="hi")
        await manager._send_with_retry(ch, msg)
        assert len(sent) == 1

    asyncio.run(run())


def test_send_with_retry_succeeds_after_failures(monkeypatch):
    """指数退避：前两次失败，第三次成功"""
    delays = []

    async def fake_sleep(d):
        delays.append(d)

    monkeypatch.setattr("asyncio.sleep", fake_sleep)

    async def run():
        bus = MessageBus()
        manager = ChannelManager({"channels": {}}, bus)

        attempt = [0]

        class FlakyChannel(_StubChannel):
            name = "flaky"
            async def send(self, msg):
                attempt[0] += 1
                if attempt[0] < 3:
                    raise RuntimeError("transient")
                # 3rd attempt succeeds

        ch = FlakyChannel({}, bus)
        msg = OutboundMessage(channel="flaky", chat_id="c1", content="hi")
        await manager._send_with_retry(ch, msg)

        assert attempt[0] == 3
        # 指数退避：1s, 2s（_SEND_RETRY_DELAYS = (1, 2, 4)）
        assert delays == [1, 2]

    asyncio.run(run())


def test_send_with_retry_all_attempts_fail(monkeypatch):
    """所有重试都失败时不应抛异常，只记录错误"""
    delays = []

    async def fake_sleep(d):
        delays.append(d)

    monkeypatch.setattr("asyncio.sleep", fake_sleep)

    async def run():
        bus = MessageBus()
        manager = ChannelManager({"channels": {}}, bus)

        attempt = [0]

        class AlwaysFailChannel(_StubChannel):
            name = "fail"
            async def send(self, msg):
                attempt[0] += 1
                raise RuntimeError("always fails")

        ch = AlwaysFailChannel({}, bus)
        msg = OutboundMessage(channel="fail", chat_id="c1", content="hi")
        # 不应抛异常
        await manager._send_with_retry(ch, msg)

        # 3 次尝试（_SEND_RETRY_DELAYS 长度），2 次退避
        assert attempt[0] == 3
        assert delays == [1, 2]

    asyncio.run(run())


def test_send_retry_delays_are_exponential():
    """验证重试延迟常量是指数退避 (1, 2, 4)"""
    assert _SEND_RETRY_DELAYS == (1, 2, 4)


def test_dispatch_outbound_routes_to_channel():
    async def run():
        bus = MessageBus()
        manager = ChannelManager({"channels": {}}, bus)

        received = []

        class ReceiverChannel(_StubChannel):
            name = "receiver"
            async def send(self, msg):
                received.append(msg)

        manager.channels["receiver"] = ReceiverChannel({}, bus)

        # 启动分发任务
        task = asyncio.create_task(manager._dispatch_outbound())

        # 发布一条出站消息
        msg = OutboundMessage(channel="receiver", chat_id="c1", content="dispatched")
        await bus.publish_outbound(msg)

        # 等待分发
        for _ in range(50):
            if len(received) > 0:
                break
            await asyncio.sleep(0.02)

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        assert len(received) == 1
        assert received[0].content == "dispatched"

    asyncio.run(asyncio.wait_for(run(), timeout=10))


def test_dispatch_outbound_drops_unknown_channel():
    """目标渠道不存在时丢弃消息（不崩溃）"""
    async def run():
        bus = MessageBus()
        manager = ChannelManager({"channels": {}}, bus)

        task = asyncio.create_task(manager._dispatch_outbound())

        # 发往不存在的渠道
        msg = OutboundMessage(channel="nonexistent", chat_id="c1", content="lost")
        await bus.publish_outbound(msg)

        # 等待分发处理
        await asyncio.sleep(0.1)

        # 分发任务应仍在运行（没有崩溃）
        assert not task.done()

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(asyncio.wait_for(run(), timeout=10))


def test_stop_all_stops_channels():
    async def run():
        bus = MessageBus()
        manager = ChannelManager(
            {"channels": {"console": {"enabled": True, "allow_from": ["*"]}}},
            bus,
        )
        # 模拟渠道正在运行
        for ch in manager.channels.values():
            ch._running = True
        await manager.stop_all()
        for ch in manager.channels.values():
            assert not ch.is_running

    asyncio.run(run())


def test_active_channels_property():
    bus = MessageBus()
    manager = ChannelManager({"channels": {}}, bus)
    # 初始无活跃渠道
    assert manager.active_channels == []
    # 标记 console 为运行中
    manager.channels["console"]._running = True
    assert "console" in manager.active_channels


def test_start_all_and_stop_all_with_console(monkeypatch):
    """端到端：启动管理器（含 console）→ console reader 退出 → 停止"""
    async def run():
        bus = MessageBus()
        manager = ChannelManager(
            {"channels": {"console": {"enabled": True, "allow_from": ["*"]}}},
            bus,
        )

        def fake_input(prompt):
            raise EOFError()

        monkeypatch.setattr("builtins.input", fake_input)

        # start_all 启动分发任务 + 所有渠道的 start()
        await manager.start_all()

        # 等待 console reader 因 EOF 退出
        for _ in range(100):
            if not manager.channels["console"].is_running:
                break
            await asyncio.sleep(0.02)

        await manager.stop_all()
        assert not manager.channels["console"].is_running

    asyncio.run(asyncio.wait_for(run(), timeout=10))
