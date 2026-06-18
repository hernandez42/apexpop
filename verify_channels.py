#!/usr/bin/env python3
"""
superclaw 消息通道 + LLM 自动路由 全量验证
"""
import asyncio
import sys
from pathlib import Path

SUPERCLAW_ROOT = Path(__file__).parent.resolve()
sys.path.insert(0, str(SUPERCLAW_ROOT))

passed = 0
failed = 0
errors = []


def ok(name, detail=""):
    global passed
    passed += 1
    print(f"  ✓ {name}" + (f" — {detail}" if detail else ""))


def fail(name, detail=""):
    global failed
    failed += 1
    errors.append(f"{name}: {detail}")
    print(f"  ✗ {name} — {detail}")


def test_message_bus():
    """测试 MessageBus"""
    print("\n[Test 1] MessageBus 异步消息总线")
    try:
        from superclaw.channels import MessageBus, InboundMessage, OutboundMessage

        bus = MessageBus()

        # 验证队列初始状态
        if bus.inbound_size == 0 and bus.outbound_size == 0:
            ok("队列初始化为空")
        else:
            fail("队列初始化", f"inbound={bus.inbound_size}, outbound={bus.outbound_size}")

        # 异步测试
        async def _test():
            # 发布入站消息
            msg = InboundMessage(
                channel="console", sender_id="user1",
                chat_id="chat1", content="你好"
            )
            await bus.publish_inbound(msg)
            assert bus.inbound_size == 1

            # 消费
            received = await bus.consume_inbound()
            assert received.content == "你好"
            assert received.channel == "console"
            assert received.session_key == "console:chat1"

            # 发布出站消息
            out = OutboundMessage(
                channel="console", chat_id="chat1", content="你好，用户"
            )
            await bus.publish_outbound(out)
            assert bus.outbound_size == 1

            out_received = await bus.consume_outbound()
            assert out_received.content == "你好，用户"

        asyncio.run(_test())
        ok("入站/出站消息收发正常")
        ok("session_key 生成正确")

    except Exception as e:
        fail("MessageBus", str(e))
        import traceback
        traceback.print_exc()


def test_events():
    """测试消息事件类型"""
    print("\n[Test 2] 消息事件类型")
    try:
        from superclaw.channels import InboundMessage, OutboundMessage

        # InboundMessage
        msg = InboundMessage(
            channel="feishu", sender_id="ou_xxx",
            chat_id="oc_xxx", content="测试",
            media=["https://example.com/img.png"],
        )
        if msg.session_key == "feishu:oc_xxx":
            ok("InboundMessage session_key")
        else:
            fail("session_key", msg.session_key)

        if msg.media and len(msg.media) == 1:
            ok("InboundMessage media")
        else:
            fail("media", str(msg.media))

        # session_key_override
        msg2 = InboundMessage(
            channel="feishu", sender_id="u", chat_id="c",
            content="x", session_key_override="custom-session"
        )
        if msg2.session_key == "custom-session":
            ok("session_key_override")
        else:
            fail("override", msg2.session_key)

        # OutboundMessage
        out = OutboundMessage(
            channel="console", chat_id="c1", content="回复",
            buttons=[["是", "否"]]
        )
        if out.buttons and len(out.buttons) == 1:
            ok("OutboundMessage buttons")
        else:
            fail("buttons", str(out.buttons))

    except Exception as e:
        fail("事件类型", str(e))


def test_base_channel():
    """测试 BaseChannel 抽象基类"""
    print("\n[Test 3] BaseChannel 基类")
    try:
        from superclaw.channels import BaseChannel, MessageBus

        # 验证是抽象类
        try:
            BaseChannel({}, MessageBus())
            fail("抽象类实例化", "应该失败但成功了")
        except TypeError:
            ok("BaseChannel 是抽象类，不能直接实例化")

        # 验证权限检查
        class TestChannel(BaseChannel):
            name = "test"
            async def start(self): pass
            async def stop(self): pass
            async def send(self, msg): pass

        # allow_from = ["*"]
        ch = TestChannel({"allow_from": ["*"]}, MessageBus())
        if ch.is_allowed("anyone"):
            ok("权限检查: * 允许所有人")
        else:
            fail("权限 *", "应该允许")

        # allow_from = ["user1"]
        ch2 = TestChannel({"allow_from": ["user1"]}, MessageBus())
        if ch2.is_allowed("user1") and not ch2.is_allowed("user2"):
            ok("权限检查: 白名单精确匹配")
        else:
            fail("权限白名单", "匹配错误")

        # 空 allow_from
        ch3 = TestChannel({}, MessageBus())
        if not ch3.is_allowed("anyone"):
            ok("权限检查: 空白名单拒绝所有人")
        else:
            fail("权限空", "应该拒绝")

    except Exception as e:
        fail("BaseChannel", str(e))


def test_console_channel():
    """测试 Console 渠道"""
    print("\n[Test 4] ConsoleChannel 控制台渠道")
    try:
        from superclaw.channels import ConsoleChannel, MessageBus, OutboundMessage

        bus = MessageBus()
        ch = ConsoleChannel({"enabled": True, "allow_from": ["*"]}, bus)

        if ch.name == "console":
            ok("ConsoleChannel 名称正确")
        else:
            fail("名称", ch.name)

        # 测试 send
        async def _test_send():
            await ch.send(OutboundMessage(
                channel="console", chat_id="c", content="测试消息"
            ))

        asyncio.run(_test_send())
        ok("ConsoleChannel send 正常")

        # 测试消息处理流程
        async def _test_handle():
            await ch._handle_message(
                sender_id="user1", chat_id="chat1",
                content="hello", is_dm=True
            )
            # 验证消息进入 bus
            assert bus.inbound_size == 1
            msg = await bus.consume_inbound()
            assert msg.content == "hello"
            assert msg.channel == "console"

        asyncio.run(_test_handle())
        ok("ConsoleChannel 消息处理 → bus 正常")

    except Exception as e:
        fail("ConsoleChannel", str(e))
        import traceback
        traceback.print_exc()


def test_channel_manager():
    """测试 ChannelManager"""
    print("\n[Test 5] ChannelManager 渠道管理器")
    try:
        from superclaw.channels import ChannelManager, MessageBus

        config = {
            "channels": {
                "console": {"enabled": True, "allow_from": ["*"]},
            }
        }
        bus = MessageBus()
        manager = ChannelManager(config, bus)

        if "console" in manager.channels:
            ok("ChannelManager 注册了 console 渠道")
        else:
            fail("渠道注册", f"只有: {list(manager.channels.keys())}")

        # 测试出站分发
        async def _test_dispatch():
            from superclaw.channels import OutboundMessage

            # 发布一条出站消息
            await bus.publish_outbound(OutboundMessage(
                channel="console", chat_id="test", content="分发测试"
            ))

            # 启动分发器（短时间）
            task = asyncio.create_task(manager._dispatch_outbound())
            await asyncio.sleep(0.5)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        asyncio.run(_test_dispatch())
        ok("ChannelManager 出站分发正常")

    except Exception as e:
        fail("ChannelManager", str(e))
        import traceback
        traceback.print_exc()


def test_feishu_channel_import():
    """测试飞书渠道导入（不测试实际连接）"""
    print("\n[Test 6] FeishuChannel 飞书渠道")
    try:
        from superclaw.channels.feishu import FeishuChannel, FEISHU_AVAILABLE
        from superclaw.channels import MessageBus

        if not FEISHU_AVAILABLE:
            ok("lark-oapi 未安装（预期，飞书渠道代码已就绪）")
        else:
            ok("lark-oapi 已安装，飞书渠道可用")

        # 验证类定义
        if FeishuChannel.name == "feishu":
            ok("FeishuChannel.name = 'feishu'")
        else:
            fail("name", FeishuChannel.name)

        # 验证默认配置
        default_cfg = FeishuChannel.default_config()
        if "app_id" in default_cfg and "app_secret" in default_cfg:
            ok(f"默认配置包含 app_id/app_secret: {list(default_cfg.keys())}")
        else:
            fail("默认配置", str(default_cfg))

        # 实例化（不启动）
        ch = FeishuChannel({
            "app_id": "cli_test",
            "app_secret": "secret_test",
            "allow_from": ["*"],
        }, MessageBus())

        if ch.app_id == "cli_test":
            ok("FeishuChannel 配置读取正常")
        else:
            fail("配置读取", ch.app_id)

    except ImportError as e:
        fail("飞书渠道导入", str(e))
    except Exception as e:
        fail("飞书渠道", str(e))


def test_llm_router():
    """测试 LLM 自动路由"""
    print("\n[Test 7] LLMRouter 自动路由")
    try:
        from superclaw.llm_router import LLMRouter

        router = LLMRouter()

        # 添加 mock provider
        router.add_provider("mock", priority=1)
        router.add_provider("deepseek", api_key="", model="deepseek-chat", priority=2)

        # 验证 provider 注册
        if "mock" in router.providers and "deepseek" in router.providers:
            ok("Provider 注册正常")
        else:
            fail("Provider 注册", str(router.providers.keys()))

        # 自动路由 — low complexity
        order = router._route("low")
        if "mock" in order:
            ok(f"路由 low: {order[:3]}")
        else:
            fail("路由 low", str(order))

        # 自动路由 — high complexity
        order = router._route("high")
        if "mock" in order:
            ok(f"路由 high: {order[:3]}")
        else:
            fail("路由 high", str(order))

        # 调用 mock
        result = router.complete(
            [{"role": "user", "content": "测试问题"}],
            complexity="low"
        )
        if result.error is None and len(result.content) > 0:
            ok(f"Mock 调用成功: provider={result.provider}, "
               f"latency={result.latency_ms}ms")
        else:
            fail("Mock 调用", f"error={result.error}")

        # 指定 provider
        result2 = router.complete(
            [{"role": "user", "content": "测试"}],
            provider="mock"
        )
        if result2.provider == "mock":
            ok("指定 provider 调用正常")
        else:
            fail("指定 provider", f"got {result2.provider}")

    except Exception as e:
        fail("LLMRouter", str(e))
        import traceback
        traceback.print_exc()


def test_llm_router_fallback():
    """测试 LLM 故障转移"""
    print("\n[Test 8] LLM 故障转移")
    try:
        from superclaw.llm_router import LLMRouter

        router = LLMRouter()
        # deepseek 没有 API key → 会失败
        router.add_provider("deepseek", api_key="", model="deepseek-chat", priority=1)
        # mock 作为兜底
        router.add_provider("mock", priority=2)

        # 调用 — deepseek 失败，应该 fallback 到 mock
        result = router.complete(
            [{"role": "user", "content": "测试故障转移"}],
            complexity="low"
        )

        if result.provider == "mock" and result.error is None:
            ok("故障转移成功: deepseek 失败 → mock 兜底")
        elif result.error:
            fail("故障转移", f"error={result.error}, provider={result.provider}")
        else:
            fail("故障转移", f"provider={result.provider}")

    except Exception as e:
        fail("故障转移", str(e))


def test_llm_router_status():
    """测试路由器状态"""
    print("\n[Test 9] 路由器状态")
    try:
        from superclaw.llm_router import LLMRouter

        router = LLMRouter()
        router.add_provider("mock", priority=1)
        router.add_provider("deepseek", api_key="test", model="deepseek-chat", priority=2)

        status = router.status()

        if "providers" in status and "mock" in status["providers"]:
            ok("状态包含 providers")
        else:
            fail("状态", str(status))

        if "litellm_available" in status:
            ok(f"litellm_available: {status['litellm_available']}")

        # 验证失败计数
        mock_status = status["providers"]["mock"]
        if mock_status["failures"] == 0:
            ok("初始失败计数为 0")
        else:
            fail("失败计数", str(mock_status["failures"]))

        # 重置
        router.reset_failures()
        ok("reset_failures 正常")

    except Exception as e:
        fail("路由器状态", str(e))


def test_add_from_env():
    """测试从环境变量自动添加 Provider"""
    print("\n[Test 10] 从环境变量自动配置")
    try:
        from superclaw.llm_router import LLMRouter
        import os

        # 设置测试环境变量
        os.environ["DEEPSEEK_API_KEY"] = "test-key-deepseek"
        os.environ["GROQ_API_KEY"] = "test-key-groq"

        router = LLMRouter()
        router.add_from_env()

        if "deepseek" in router.providers:
            ok("从 DEEPSEEK_API_KEY 自动添加 deepseek")
        else:
            fail("deepseek 自动添加", "未找到")

        if "groq" in router.providers:
            ok("从 GROQ_API_KEY 自动添加 groq")
        else:
            fail("groq 自动添加", "未找到")

        if "mock" in router.providers:
            ok("mock 兜底自动添加")
        else:
            fail("mock 兜底", "未找到")

        # 清理
        del os.environ["DEEPSEEK_API_KEY"]
        del os.environ["GROQ_API_KEY"]

    except Exception as e:
        fail("环境变量配置", str(e))


def main():
    print("=" * 60)
    print("  🧪 superclaw 消息通道 + LLM 自动路由 验证")
    print("=" * 60)

    test_message_bus()
    test_events()
    test_base_channel()
    test_console_channel()
    test_channel_manager()
    test_feishu_channel_import()
    test_llm_router()
    test_llm_router_fallback()
    test_llm_router_status()
    test_add_from_env()

    print(f"\n{'=' * 60}")
    print(f"  结果:  ✓ {passed} 通过  |  ✗ {failed} 失败")
    if errors:
        print("\n  失败详情:")
        for e in errors:
            print(f"    - {e}")
    print(f"{'=' * 60}")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
