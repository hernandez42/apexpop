"""飞书渠道深度单元测试 — 用 mock 覆盖 lark-oapi SDK 的连接路径。

沙箱未安装 lark-oapi，因此在导入 superclaw.channels.feishu 之前，先用
types.ModuleType + ModuleSpec 在 sys.modules 中注入一个伪 lark_oapi 包
(含 core.const / ws.client / api.im.v1 等子模块)，使 feishu.py 既能 import
又能让 importlib.util.find_spec("lark_oapi") 返回非 None (FEISHU_AVAILABLE=True)。

注意: feishu.py 没有 _send_text 方法，发送消息统一走 send()；本文件对 send()
及其依赖的 _get_tenant_token (REST API 取 token) 做了完整 mock 覆盖。
"""
from __future__ import annotations

import asyncio
import importlib.machinery
import importlib.util
import json
import os
import sys
import types
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# 1. 让 superclaw 包可导入
# ---------------------------------------------------------------------------
_SUPERCLAW_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SUPERCLAW_ROOT not in sys.path:
    sys.path.insert(0, _SUPERCLAW_ROOT)


# ---------------------------------------------------------------------------
# 2. 注入伪 lark_oapi 模块 (必须在 import feishu 之前完成)
# ---------------------------------------------------------------------------
def _make_module(name: str, is_package: bool = False) -> types.ModuleType:
    """构造一个带 __spec__ 的模块，使 importlib.util.find_spec 返回非 None。"""
    mod = types.ModuleType(name)
    mod.__path__ = []  # 标记为 package，允许 from x.y import z
    mod.__spec__ = importlib.machinery.ModuleSpec(
        name, loader=None, is_package=is_package
    )
    return mod


def _install_fake_lark_oapi(force: bool = False) -> None:
    """安装伪 lark_oapi 及其子模块到 sys.modules。

    force=True 时无条件重建，使每个测试拿到调用记录干净的 mock。
    """
    if not force and getattr(sys.modules.get("lark_oapi"), "_superclaw_fake", False):
        return

    lark = _make_module("lark_oapi", is_package=True)
    lark._superclaw_fake = True
    lark.LogLevel = MagicMock()
    lark.LogLevel.INFO = "INFO"
    lark.Client = MagicMock()
    lark.EventDispatcherHandler = MagicMock()
    sys.modules["lark_oapi"] = lark

    core = _make_module("lark_oapi.core", is_package=True)
    sys.modules["lark_oapi.core"] = core
    const = _make_module("lark_oapi.core.const")
    const.FEISHU_DOMAIN = "https://open.feishu.cn"
    const.LARK_DOMAIN = "https://open.larksuite.com"
    sys.modules["lark_oapi.core.const"] = const

    ws = _make_module("lark_oapi.ws", is_package=True)
    sys.modules["lark_oapi.ws"] = ws
    ws_client = _make_module("lark_oapi.ws.client")
    ws_client.Client = MagicMock()
    sys.modules["lark_oapi.ws.client"] = ws_client

    api = _make_module("lark_oapi.api", is_package=True)
    sys.modules["lark_oapi.api"] = api
    im = _make_module("lark_oapi.api.im", is_package=True)
    sys.modules["lark_oapi.api.im"] = im
    im_v1 = _make_module("lark_oapi.api.im.v1")
    im_v1.P2ImMessageReceiveV1 = MagicMock()
    im_v1.CreateMessageRequest = MagicMock()
    im_v1.CreateMessageRequestBody = MagicMock()
    sys.modules["lark_oapi.api.im.v1"] = im_v1


_install_fake_lark_oapi()


# ---------------------------------------------------------------------------
# 3. 现在可以安全导入被测代码
# ---------------------------------------------------------------------------
# 如果 superclaw.channels.feishu 已被其他测试模块（如 test_channels.py）提前
# 导入，FEISHU_AVAILABLE 会在伪 lark_oapi 安装前被计算为 False。这里 reload
# 确保它基于已安装的伪模块重新计算。
import importlib  # noqa: E402
if "superclaw.channels.feishu" in sys.modules:
    importlib.reload(sys.modules["superclaw.channels.feishu"])

from superclaw.channels.bus import MessageBus  # noqa: E402
from superclaw.channels.events import InboundMessage, OutboundMessage  # noqa: E402
from superclaw.channels.feishu import (  # noqa: E402
    FEISHU_AVAILABLE,
    MSG_TYPE_MAP,
    FeishuChannel,
    _get_mention_name,
)
import superclaw.channels.feishu as feishu_mod  # noqa: E402

# 确保 feishu 渠道在 ChannelManager 注册表中（manager 可能在伪模块安装前已导入）
from superclaw.channels.manager import register_channel as _register_channel  # noqa: E402
_register_channel("feishu", FeishuChannel)


# ---------------------------------------------------------------------------
# 每个测试前重建伪 lark_oapi，保证 mock 调用记录干净
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _fresh_fake_lark():
    _install_fake_lark_oapi(force=True)
    yield


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------
def _fake_lark():
    return sys.modules["lark_oapi"]


def _fake_v1():
    return sys.modules["lark_oapi.api.im.v1"]


def _build_event(
    msg_type: str = "text",
    content=None,
    chat_type: str = "p2p",
    open_id: str = "ou_sender",
    chat_id: str = "oc_chat",
    mentions=None,
):
    """构造一个模拟飞书 P2ImMessageReceiveV1 事件的 data 对象。"""
    if content is None:
        content = {}
    content_str = content if isinstance(content, str) else json.dumps(content)
    msg = SimpleNamespace(
        chat_id=chat_id,
        message_type=msg_type,
        content=content_str,
        chat_type=chat_type,
        mentions=mentions,
    )
    sender = SimpleNamespace(sender_id=SimpleNamespace(open_id=open_id))
    event = SimpleNamespace(message=msg, sender=sender)
    return SimpleNamespace(event=event)


def _run(coro):
    """同步运行协程 (pytest-asyncio 未安装时的便携写法)。"""
    return asyncio.run(coro)


@contextmanager
def _lark_uninstalled():
    """临时把 lark_oapi.* 从 sys.modules 移除，模拟 SDK 未安装。"""
    saved = {
        k: v for k, v in sys.modules.items()
        if k == "lark_oapi" or k.startswith("lark_oapi.")
    }
    for k in list(saved):
        del sys.modules[k]
    try:
        yield
    finally:
        sys.modules.update(saved)


# ===========================================================================
# FEISHU_AVAILABLE 检测逻辑
# ===========================================================================
def test_feishu_available_true_when_fake_installed():
    assert importlib.util.find_spec("lark_oapi") is not None
    assert FEISHU_AVAILABLE is True


def test_find_spec_none_when_lark_uninstalled():
    with _lark_uninstalled():
        assert importlib.util.find_spec("lark_oapi") is None
    # 恢复后再次可用
    assert importlib.util.find_spec("lark_oapi") is not None


def test_load_lark_raises_when_unavailable():
    ch = FeishuChannel({"app_id": "x", "app_secret": "y"}, MessageBus())
    with patch.object(feishu_mod, "FEISHU_AVAILABLE", False):
        with pytest.raises(ImportError, match="lark-oapi 未安装"):
            ch._load_lark()


def test_load_lark_returns_module_and_sets_domains():
    ch = FeishuChannel({"app_id": "x", "app_secret": "y"}, MessageBus())
    lark = ch._load_lark()
    assert lark is _fake_lark()
    assert ch._lark is lark
    assert ch._feishu_domain == "https://open.feishu.cn"
    assert ch._lark_domain == "https://open.larksuite.com"


# ===========================================================================
# 实例化与配置
# ===========================================================================
def test_instantiation_with_dict_config():
    bus = MessageBus()
    ch = FeishuChannel(
        {"app_id": "cli_x", "app_secret": "sec", "allow_from": ["*"]},
        bus,
    )
    assert ch.app_id == "cli_x"
    assert ch.app_secret == "sec"
    assert ch.name == "feishu"
    assert ch.display_name == "飞书"
    assert ch.bus is bus
    assert ch._lark is None
    assert ch._ws_client is None
    assert ch._tenant_access_token == ""
    assert ch.is_running is False
    assert ch.send_progress is True
    assert ch.show_reasoning is True


def test_instantiation_with_object_config():
    cfg = SimpleNamespace(
        app_id="cli_obj", app_secret="obj_sec", allow_from=["u1"]
    )
    ch = FeishuChannel(cfg, MessageBus())
    assert ch.app_id == "cli_obj"
    assert ch.app_secret == "obj_sec"


def test_get_config_defaults_when_missing():
    ch = FeishuChannel({}, MessageBus())
    assert ch.app_id == ""
    assert ch.app_secret == ""


def test_default_config():
    cfg = FeishuChannel.default_config()
    assert cfg["enabled"] is False
    assert cfg["app_id"] == ""
    assert cfg["app_secret"] == ""
    assert cfg["allow_from"] == ["*"]
    assert cfg["streaming"] is False


# ===========================================================================
# MSG_TYPE_MAP 映射
# ===========================================================================
def test_msg_type_map_known_types():
    assert MSG_TYPE_MAP["image"] == "[图片]"
    assert MSG_TYPE_MAP["audio"] == "[语音]"
    assert MSG_TYPE_MAP["file"] == "[文件]"
    assert MSG_TYPE_MAP["sticker"] == "[表情]"
    assert MSG_TYPE_MAP["post"] == "[富文本]"
    assert MSG_TYPE_MAP["share_chat"] == "[分享群名片]"
    assert MSG_TYPE_MAP["share_user"] == "[分享个人名片]"


def test_msg_type_map_is_dict():
    assert isinstance(MSG_TYPE_MAP, dict)
    assert len(MSG_TYPE_MAP) == 7


# ===========================================================================
# 权限检查 (allow_from 白名单)
# ===========================================================================
def test_permission_wildcard_allows_all():
    ch = FeishuChannel({"allow_from": ["*"]}, MessageBus())
    assert ch.is_allowed("anyone") is True
    assert ch.is_allowed("ou_123") is True


def test_permission_specific_user_allowed():
    ch = FeishuChannel({"allow_from": ["ou_alice"]}, MessageBus())
    assert ch.is_allowed("ou_alice") is True
    assert ch.is_allowed("ou_bob") is False


def test_permission_empty_denies_all():
    ch = FeishuChannel({}, MessageBus())
    assert ch.is_allowed("anyone") is False


def test_permission_allowfrom_alias():
    ch = FeishuChannel({"allowFrom": ["ou_x"]}, MessageBus())
    assert ch.is_allowed("ou_x") is True
    assert ch.is_allowed("ou_y") is False


def test_permission_str_sender_id_matched_against_str_list():
    ch = FeishuChannel({"allow_from": ["12345"]}, MessageBus())
    assert ch.is_allowed(12345) is True  # str(12345) in ["12345"]


# ===========================================================================
# _handle_message: 权限检查 → InboundMessage → MessageBus
# ===========================================================================
def test_handle_message_allowed_publishes_to_bus():
    bus = MessageBus()
    ch = FeishuChannel({"allow_from": ["*"]}, bus)
    _run(ch._handle_message(
        sender_id="ou_1", chat_id="oc_1", content="hello", is_dm=True,
    ))
    assert bus.inbound_size == 1
    msg = _run(bus.consume_inbound())
    assert isinstance(msg, InboundMessage)
    assert msg.channel == "feishu"
    assert msg.sender_id == "ou_1"
    assert msg.chat_id == "oc_1"
    assert msg.content == "hello"
    assert msg.session_key == "feishu:oc_1"


def test_handle_message_denied_no_dm_no_publish():
    bus = MessageBus()
    ch = FeishuChannel({"allow_from": ["ou_allowed"]}, bus)
    ch.send = AsyncMock()
    _run(ch._handle_message(
        sender_id="ou_bad", chat_id="oc_1", content="hi", is_dm=False,
    ))
    assert bus.inbound_size == 0
    ch.send.assert_not_awaited()


def test_handle_message_denied_dm_sends_warning():
    bus = MessageBus()
    ch = FeishuChannel({"allow_from": ["ou_allowed"]}, bus)
    ch.send = AsyncMock()
    _run(ch._handle_message(
        sender_id="ou_bad", chat_id="oc_1", content="hi", is_dm=True,
    ))
    assert bus.inbound_size == 0
    ch.send.assert_awaited_once()
    sent = ch.send.await_args.args[0]
    assert isinstance(sent, OutboundMessage)
    assert sent.channel == "feishu"
    assert sent.chat_id == "oc_1"
    assert "权限" in sent.content


def test_handle_message_metadata_and_session_key():
    bus = MessageBus()
    ch = FeishuChannel({"allow_from": ["*"]}, bus)
    _run(ch._handle_message(
        sender_id="u", chat_id="c", content="x",
        session_key="custom-session", metadata={"foo": "bar"},
    ))
    msg = _run(bus.consume_inbound())
    assert msg.session_key == "custom-session"
    assert msg.metadata.get("foo") == "bar"


def test_handle_message_streaming_adds_wants_stream():
    bus = MessageBus()
    ch = FeishuChannel({"allow_from": ["*"], "streaming": True}, bus)
    _run(ch._handle_message(sender_id="u", chat_id="c", content="x"))
    msg = _run(bus.consume_inbound())
    assert msg.metadata.get("_wants_stream") is True


def test_supports_streaming_flag():
    assert FeishuChannel({"allow_from": ["*"]}, MessageBus()).supports_streaming is False
    assert FeishuChannel(
        {"allow_from": ["*"], "streaming": True}, MessageBus()
    ).supports_streaming is True


# ===========================================================================
# _process_message_event: 解析飞书事件 → _handle_message
# ===========================================================================
def test_process_message_event_text_p2p():
    ch = FeishuChannel({"app_id": "cli_bot", "allow_from": ["*"]}, MessageBus())
    ch._handle_message = AsyncMock()
    data = _build_event(msg_type="text", content={"text": "你好"}, chat_type="p2p")
    _run(ch._process_message_event(data))
    ch._handle_message.assert_awaited_once()
    kwargs = ch._handle_message.await_args.kwargs
    assert kwargs["sender_id"] == "ou_sender"
    assert kwargs["chat_id"] == "oc_chat"
    assert kwargs["content"] == "你好"
    assert kwargs["is_dm"] is True


def test_process_message_event_text_group_is_not_dm():
    ch = FeishuChannel({"allow_from": ["*"]}, MessageBus())
    ch._handle_message = AsyncMock()
    data = _build_event(msg_type="text", content={"text": "hi"}, chat_type="group")
    _run(ch._process_message_event(data))
    kwargs = ch._handle_message.await_args.kwargs
    assert kwargs["is_dm"] is False
    assert kwargs["content"] == "hi"


def test_process_message_event_image_uses_msg_type_map():
    ch = FeishuChannel({"allow_from": ["*"]}, MessageBus())
    ch._handle_message = AsyncMock()
    data = _build_event(msg_type="image", content={}, chat_type="group")
    _run(ch._process_message_event(data))
    kwargs = ch._handle_message.await_args.kwargs
    assert kwargs["content"] == "[图片]"
    assert kwargs["is_dm"] is False


def test_process_message_event_audio_and_file_mapping():
    ch = FeishuChannel({"allow_from": ["*"]}, MessageBus())
    ch._handle_message = AsyncMock()
    for mtype, expected in [("audio", "[语音]"), ("file", "[文件]"),
                            ("sticker", "[表情]"), ("post", "[富文本]")]:
        ch._handle_message.reset_mock()
        data = _build_event(msg_type=mtype, content={}, chat_type="p2p")
        _run(ch._process_message_event(data))
        assert ch._handle_message.await_args.kwargs["content"] == expected


def test_process_message_event_unknown_type_fallback():
    ch = FeishuChannel({"allow_from": ["*"]}, MessageBus())
    ch._handle_message = AsyncMock()
    data = _build_event(msg_type="interactive", content={}, chat_type="p2p")
    _run(ch._process_message_event(data))
    kwargs = ch._handle_message.await_args.kwargs
    assert kwargs["content"] == "[interactive]"


def test_process_message_event_empty_text_skipped():
    ch = FeishuChannel({"allow_from": ["*"]}, MessageBus())
    ch._handle_message = AsyncMock()
    data = _build_event(msg_type="text", content={"text": "   "}, chat_type="p2p")
    _run(ch._process_message_event(data))
    ch._handle_message.assert_not_awaited()


def test_process_message_event_mention_bot_removed():
    ch = FeishuChannel({"app_id": "cli_bot", "allow_from": ["*"]}, MessageBus())
    ch._handle_message = AsyncMock()
    mention = SimpleNamespace(id="cli_bot", name="Bot")
    data = _build_event(
        msg_type="text", content={"text": "@Bot 你好"},
        chat_type="group", mentions=[mention],
    )
    _run(ch._process_message_event(data))
    assert ch._handle_message.await_args.kwargs["content"] == "你好"


def test_process_message_event_mention_other_kept():
    ch = FeishuChannel({"app_id": "cli_bot", "allow_from": ["*"]}, MessageBus())
    ch._handle_message = AsyncMock()
    mention = SimpleNamespace(id="ou_other", name="Alice")
    data = _build_event(
        msg_type="text", content={"text": "@Alice hi"},
        chat_type="group", mentions=[mention],
    )
    _run(ch._process_message_event(data))
    assert ch._handle_message.await_args.kwargs["content"] == "@Alice hi"


def test_process_message_event_mentions_none_does_not_crash():
    ch = FeishuChannel({"app_id": "cli_bot", "allow_from": ["*"]}, MessageBus())
    ch._handle_message = AsyncMock()
    data = _build_event(
        msg_type="text", content={"text": "ok"}, chat_type="p2p", mentions=None,
    )
    _run(ch._process_message_event(data))
    assert ch._handle_message.await_args.kwargs["content"] == "ok"


def test_process_message_event_exception_swallowed():
    ch = FeishuChannel({"allow_from": ["*"]}, MessageBus())
    ch._handle_message = AsyncMock()
    # message 为 None 会触发 AttributeError，被 except 捕获
    bad = SimpleNamespace(event=SimpleNamespace(message=None, sender=None))
    _run(ch._process_message_event(bad))  # 不应抛出
    ch._handle_message.assert_not_awaited()


# ===========================================================================
# _get_mention_name 辅助函数
# ===========================================================================
def test_get_mention_name_with_name():
    assert _get_mention_name(SimpleNamespace(name="Bot")) == "Bot"


def test_get_mention_name_none_name():
    assert _get_mention_name(SimpleNamespace(name=None)) == ""


def test_get_mention_name_missing_attr():
    class _NoName:
        pass

    assert _get_mention_name(_NoName()) == ""


# ===========================================================================
# send(): REST API 发送 (mock lark Client 链)
# ===========================================================================
def test_send_without_lark_logs_error_and_returns():
    ch = FeishuChannel(
        {"app_id": "x", "app_secret": "y", "allow_from": ["*"]}, MessageBus()
    )
    assert ch._lark is None
    _run(ch.send(OutboundMessage(channel="feishu", chat_id="oc_1", content="hi")))
    # 未初始化 SDK，不应抛出


def _wire_send_response(ch, success: bool, msg: str = "ok"):
    """把 self._lark.Client.builder()...create() 的返回值接到给定响应。"""
    cb = ch._lark.Client.builder.return_value
    # Builder 链: app_id → app_secret → tenant_access_token → build → im.v1.message.create
    after_token = cb.app_id.return_value.app_secret.return_value.tenant_access_token.return_value
    built = after_token.build.return_value
    resp = MagicMock()
    resp.success.return_value = success
    resp.msg = msg
    built.im.v1.message.create.return_value = resp
    return built


def test_send_success_builds_request_and_calls_create():
    ch = FeishuChannel(
        {"app_id": "cli_x", "app_secret": "sec", "allow_from": ["*"]}, MessageBus()
    )
    ch._load_lark()
    ch._get_tenant_token = AsyncMock(return_value="tok_123")
    built = _wire_send_response(ch, success=True)

    _run(ch.send(OutboundMessage(channel="feishu", chat_id="oc_1", content="hello")))

    ch._get_tenant_token.assert_awaited_once()
    # 验证 Client builder 链传入了 app_id / app_secret
    cb = ch._lark.Client.builder.return_value
    cb.app_id.assert_called_once_with("cli_x")
    cb.app_id.return_value.app_secret.assert_called_once_with("sec")
    # 验证 create 被调用
    built.im.v1.message.create.assert_called_once()


def test_send_serializes_content_as_text_json():
    ch = FeishuChannel(
        {"app_id": "cli_x", "app_secret": "sec", "allow_from": ["*"]}, MessageBus()
    )
    ch._load_lark()
    ch._get_tenant_token = AsyncMock(return_value="tok_123")
    _wire_send_response(ch, success=True)

    _run(ch.send(OutboundMessage(channel="feishu", chat_id="oc_1", content="hello")))

    v1 = _fake_v1()
    # CreateMessageRequest.builder().receive_id_type("chat_id")
    v1.CreateMessageRequest.builder.return_value.receive_id_type.assert_called_once_with(
        "chat_id"
    )
    # CreateMessageRequestBody 链: receive_id(chat_id) / msg_type("text") / content(json)
    body = v1.CreateMessageRequestBody.builder.return_value
    body.receive_id.assert_called_once_with("oc_1")
    body.receive_id.return_value.msg_type.assert_called_once_with("text")
    body.receive_id.return_value.msg_type.return_value.content.assert_called_once_with(
        json.dumps({"text": "hello"})
    )


def test_send_failure_response_logs_error():
    ch = FeishuChannel(
        {"app_id": "cli_x", "app_secret": "sec", "allow_from": ["*"]}, MessageBus()
    )
    ch._load_lark()
    ch._get_tenant_token = AsyncMock(return_value="tok_123")
    built = _wire_send_response(ch, success=False, msg="rate limited")

    _run(ch.send(OutboundMessage(channel="feishu", chat_id="oc_1", content="hi")))
    built.im.v1.message.create.assert_called_once()


def test_send_no_token_returns_early():
    ch = FeishuChannel(
        {"app_id": "cli_x", "app_secret": "sec", "allow_from": ["*"]}, MessageBus()
    )
    ch._load_lark()
    ch._get_tenant_token = AsyncMock(return_value="")
    built = _wire_send_response(ch, success=True)

    _run(ch.send(OutboundMessage(channel="feishu", chat_id="oc_1", content="hi")))
    built.im.v1.message.create.assert_not_called()


def test_send_exception_swallowed():
    ch = FeishuChannel(
        {"app_id": "cli_x", "app_secret": "sec", "allow_from": ["*"]}, MessageBus()
    )
    ch._load_lark()
    ch._get_tenant_token = AsyncMock(return_value="tok")
    cb = ch._lark.Client.builder.return_value
    # 让链路中途抛出
    cb.app_id.side_effect = RuntimeError("boom")
    _run(ch.send(OutboundMessage(channel="feishu", chat_id="oc_1", content="hi")))


# ===========================================================================
# send() 路由: send_delta 与 ChannelManager 出站分发
# ===========================================================================
def test_send_delta_routes_to_feishu_channel():
    ch = FeishuChannel(
        {"app_id": "cli_x", "app_secret": "sec", "allow_from": ["*"]}, MessageBus()
    )
    ch.send = AsyncMock()
    _run(ch.send_delta(chat_id="oc_1", delta="chunk"))
    ch.send.assert_awaited_once()
    sent = ch.send.await_args.args[0]
    assert isinstance(sent, OutboundMessage)
    assert sent.channel == "feishu"
    assert sent.chat_id == "oc_1"
    assert sent.content == "chunk"


def test_channel_manager_routes_feishu_outbound():
    from superclaw.channels.manager import ChannelManager

    bus = MessageBus()
    config = {
        "channels": {
            "feishu": {
                "enabled": True,
                "app_id": "cli_x",
                "app_secret": "sec",
                "allow_from": ["*"],
            }
        }
    }
    mgr = ChannelManager(config, bus)
    assert "feishu" in mgr.channels
    feishu_ch = mgr.channels["feishu"]
    feishu_ch.send = AsyncMock()

    _run(bus.publish_outbound(
        OutboundMessage(channel="feishu", chat_id="oc_1", content="routed")
    ))

    async def _drive():
        task = asyncio.create_task(mgr._dispatch_outbound())
        await asyncio.sleep(0.3)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    _run(_drive())
    feishu_ch.send.assert_awaited_once()
    sent = feishu_ch.send.await_args.args[0]
    assert sent.channel == "feishu"
    assert sent.content == "routed"


# ===========================================================================
# _get_tenant_token: REST API 取 token (mock urllib)
# ===========================================================================
def _make_urlopen_resp(payload: dict):
    resp = MagicMock()
    resp.read.return_value = json.dumps(payload).encode("utf-8")
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False
    return resp


def test_get_tenant_token_success():
    ch = FeishuChannel(
        {"app_id": "cli_x", "app_secret": "sec", "allow_from": ["*"]}, MessageBus()
    )
    ch._load_lark()
    resp = _make_urlopen_resp(
        {"code": 0, "tenant_access_token": "tok_abc", "msg": "ok"}
    )
    with patch("urllib.request.urlopen", return_value=resp) as mock_open:
        token = _run(ch._get_tenant_token())
    assert token == "tok_abc"
    assert ch._tenant_access_token == "tok_abc"
    mock_open.assert_called_once()
    req = mock_open.call_args.args[0]
    assert "tenant_access_token/internal" in req.full_url
    assert req.get_method() == "POST"


def test_get_tenant_token_cached_no_request():
    ch = FeishuChannel(
        {"app_id": "cli_x", "app_secret": "sec", "allow_from": ["*"]}, MessageBus()
    )
    ch._load_lark()
    ch._tenant_access_token = "cached_tok"
    with patch("urllib.request.urlopen") as mock_open:
        token = _run(ch._get_tenant_token())
    assert token == "cached_tok"
    mock_open.assert_not_called()


def test_get_tenant_token_failure_code_returns_empty():
    ch = FeishuChannel(
        {"app_id": "cli_x", "app_secret": "sec", "allow_from": ["*"]}, MessageBus()
    )
    ch._load_lark()
    resp = _make_urlopen_resp({"code": 99999, "msg": "bad secret"})
    with patch("urllib.request.urlopen", return_value=resp):
        token = _run(ch._get_tenant_token())
    assert token == ""
    assert ch._tenant_access_token == ""


def test_get_tenant_token_exception_returns_empty():
    ch = FeishuChannel(
        {"app_id": "cli_x", "app_secret": "sec", "allow_from": ["*"]}, MessageBus()
    )
    ch._load_lark()
    with patch("urllib.request.urlopen", side_effect=Exception("network error")):
        token = _run(ch._get_tenant_token())
    assert token == ""


# ===========================================================================
# start/stop 生命周期 (mock WebSocket)
# ===========================================================================
def test_start_no_app_id_returns_early():
    ch = FeishuChannel({"app_id": "", "app_secret": "", "allow_from": ["*"]}, MessageBus())
    _run(ch.start())
    assert ch.is_running is False
    assert ch._ws_client is None


def test_start_unavailable_returns_early():
    ch = FeishuChannel(
        {"app_id": "cli_x", "app_secret": "sec", "allow_from": ["*"]}, MessageBus()
    )
    with patch.object(feishu_mod, "FEISHU_AVAILABLE", False):
        _run(ch.start())
    assert ch.is_running is False
    assert ch._ws_client is None


def test_start_success_builds_ws_client():
    ch = FeishuChannel(
        {"app_id": "cli_x", "app_secret": "sec", "allow_from": ["*"]}, MessageBus()
    )
    WsClient = sys.modules["lark_oapi.ws.client"].Client
    WsClient.reset_mock()
    _run(ch.start())
    assert ch.is_running is True
    assert ch._ws_client is not None
    WsClient.assert_called_once()
    kwargs = WsClient.call_args.kwargs
    assert kwargs["app_id"] == "cli_x"
    assert kwargs["app_secret"] == "sec"
    assert "event_handler" in kwargs
    assert kwargs["log_level"] == _fake_lark().LogLevel.INFO


def test_start_ws_error_swallowed():
    ch = FeishuChannel(
        {"app_id": "cli_x", "app_secret": "sec", "allow_from": ["*"]}, MessageBus()
    )
    WsClient = sys.modules["lark_oapi.ws.client"].Client
    WsClient.return_value.start.side_effect = RuntimeError("ws boom")
    _run(ch.start())  # 不应抛出
    assert ch.is_running is True  # _running 在 executor 调用前已置 True


def test_stop_without_ws_client():
    ch = FeishuChannel(
        {"app_id": "x", "app_secret": "y", "allow_from": ["*"]}, MessageBus()
    )
    ch._running = True
    _run(ch.stop())
    assert ch.is_running is False


def test_stop_with_ws_client_calls_stop():
    ch = FeishuChannel(
        {"app_id": "x", "app_secret": "y", "allow_from": ["*"]}, MessageBus()
    )
    ch._running = True
    ch._ws_client = MagicMock()
    _run(ch.stop())
    assert ch.is_running is False
    ch._ws_client.stop.assert_called_once()


def test_stop_ws_client_error_swallowed():
    ch = FeishuChannel(
        {"app_id": "x", "app_secret": "y", "allow_from": ["*"]}, MessageBus()
    )
    ch._running = True
    ch._ws_client = MagicMock()
    ch._ws_client.stop.side_effect = RuntimeError("stop err")
    _run(ch.stop())  # 不应抛出
    assert ch.is_running is False


# ===========================================================================
# _build_event_handler: 事件处理器构建
# ===========================================================================
def test_build_event_handler_registers_callback():
    ch = FeishuChannel(
        {"app_id": "cli_x", "app_secret": "sec", "allow_from": ["*"]}, MessageBus()
    )
    EDH = _fake_lark().EventDispatcherHandler
    EDH.reset_mock()
    handler = ch._build_event_handler()
    EDH.builder.assert_called_once_with("", "")
    register_mock = EDH.builder.return_value.register_p2_im_message_receive_v1
    register_mock.assert_called_once()
    assert handler is register_mock.return_value.build.return_value


def test_event_handler_callback_dispatches_to_process():
    ch = FeishuChannel(
        {"app_id": "cli_x", "app_secret": "sec", "allow_from": ["*"]}, MessageBus()
    )
    ch._process_message_event = AsyncMock()
    EDH = _fake_lark().EventDispatcherHandler
    EDH.reset_mock()
    ch._build_event_handler()
    register_mock = EDH.builder.return_value.register_p2_im_message_receive_v1
    callback = register_mock.call_args.args[0]

    fake_data = MagicMock()

    async def _drive():
        callback(fake_data)  # 内部 asyncio.create_task
        await asyncio.sleep(0.05)  # 让任务调度执行

    _run(_drive())
    ch._process_message_event.assert_awaited_once_with(fake_data)
