#!/usr/bin/env python3
"""
superclaw CLI 入口
用法:
  python3 -m superclaw                         交互式对话
  python3 -m superclaw --provider mock         指定 Provider
  python3 -m superclaw --provider deepseek     使用 DeepSeek
  python3 -m superclaw run "你的问题"            单次运行
  python3 -m superclaw --providers              列出可用 Provider
  python3 -m superclaw --config path.json       指定配置
  python3 -m superclaw --test                   运行内置测试
"""
import argparse
import sys
from pathlib import Path

# 支持 `python3 cli.py` 或 `python3 -m superclaw`
PROJECT_ROOT = Path(__file__).parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))

from superclaw import Agent, get_provider, list_providers, load_config
from superclaw.config import SuperclawConfig


def _print_header(cfg: SuperclawConfig) -> None:
    print("=" * 60)
    print("  🦖 superclaw v2.0.0  —  轻量级 AI Agent 框架")
    print("=" * 60)
    print(f"  Provider:  {cfg.llm.provider}")
    print(f"  Model:     {cfg.llm.model}")
    print(f"  API Key:   {'已配置' if cfg.llm.api_key else '未配置'}")
    print(f"  Workspace: {cfg.workspace}")
    print(f"  Max Iter:  {cfg.tools.max_tool_iterations}")
    print("-" * 60)


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="superclaw",
        description="superclaw — 轻量级 AI Agent 框架",
    )
    parser.add_argument("run_input", nargs="?", help="单次问答模式的输入文本")
    parser.add_argument("--provider", "-p",
                       choices=list_providers(),
                       help="指定 LLM Provider")
    parser.add_argument("--model", "-m", help="指定模型名")
    parser.add_argument("--config", "-c", help="指定 config.json 路径")
    parser.add_argument("--providers", action="store_true",
                       help="列出所有可用 Provider")
    parser.add_argument("--session", "-s", default="cli", help="会话 key (默认 cli)")
    parser.add_argument("--verbose", "-v", action="store_true", help="显示详细信息")
    parser.add_argument("--test", action="store_true", help="运行内置测试")
    parser.add_argument("--skill", help="加载一个 .md skill 文件")

    args = parser.parse_args()

    # 列出 providers
    if args.providers:
        print("可用 Provider:")
        for name in list_providers():
            notes = ""
            if name == "mock":
                notes = "（模拟，无需 API Key）"
            elif name == "ollama":
                notes = "（本地，需启动 ollama serve）"
            else:
                notes = f"（需 {name.upper()}_API_KEY）"
            print(f"  - {name:<12} {notes}")
        return 0

    # 测试模式
    if args.test:
        return _run_tests()

    # 加载配置
    cfg = load_config(args.config)
    if args.provider:
        cfg.llm.provider = args.provider
        # provider 切换后需要重新读对应环境变量
        import os
        env_key = f"{args.provider.upper()}_API_KEY"
        if os.environ.get(env_key):
            cfg.llm.api_key = os.environ[env_key]
    if args.model:
        cfg.llm.model = args.model

    _print_header(cfg)

    # 创建 Agent
    provider = get_provider(cfg.llm)
    agent = Agent(cfg=cfg, provider=provider)

    # 加载 skill
    if args.skill:
        if agent.add_skill(args.skill):
            print(f"  ✓ Skill 已加载: {args.skill}")
        else:
            print(f"  ✗ Skill 加载失败: {args.skill}")

    # 单次问答
    if args.run_input:
        result = agent.run(args.run_input, session_key=args.session, verbose=args.verbose)
        print()
        print(result.content)
        if args.verbose and result.tools_used:
            print(f"\n  工具: {', '.join(set(result.tools_used))}")
            print(f"  迭代: {result.iterations} | 用时: {result.total_time_ms}ms")
        return 0

    # 交互式对话
    print("  交互式对话模式")
    print("  命令: exit/quit/q 退出, /clear 清会话, /tools 看工具, /skill <file> 加载 skill")
    print("-" * 60)
    print()

    try:
        agent.chat(session_key=args.session)
    except KeyboardInterrupt:
        print("\n  再见 👋")

    return 0


def _run_tests() -> int:
    """极简内置测试 — 不依赖外部"""
    print("\n=== superclaw 内置测试 ===")
    passed = 0
    failed = 0

    # 1. 配置加载
    try:
        cfg = load_config()
        assert cfg.llm.provider  # 必须有默认 provider
        assert cfg.workspace
        print("  ✓ 配置加载正常")
        passed += 1
    except Exception as e:
        print(f"  ✗ 配置加载失败: {e}")
        failed += 1

    # 2. Provider 系统
    try:
        from superclaw import list_providers
        providers = list_providers()
        assert "mock" in providers
        print(f"  ✓ {len(providers)} 个 Provider 可用: {', '.join(providers)}")
        passed += 1
    except Exception as e:
        print(f"  ✗ Provider 系统错误: {e}")
        failed += 1

    # 3. Mock Provider 调用
    try:
        from superclaw.config import LLMConfig
        provider = get_provider(LLMConfig(provider="mock", model="test"))
        msg = [{"role": "user", "content": "测试问题"}]
        resp = provider.call(msg)
        assert isinstance(resp, str) and len(resp) > 0
        print(f"  ✓ Mock Provider 返回: {resp[:50]}...")
        passed += 1
    except Exception as e:
        print(f"  ✗ Mock Provider 失败: {e}")
        failed += 1

    # 4. 工具系统
    try:
        from superclaw.tools import build_default_tools
        tools = build_default_tools(str(PROJECT_ROOT),
                                    shell=True, file_tools=True, web=False, think=True)  # nosec B604 - 传给 build_default_tools 的布尔开关，非 subprocess 调用
        assert "think" in tools.names
        assert "file_read" in tools.names
        assert "shell" in tools.names
        print(f"  ✓ {len(tools.names)} 个工具注册成功: {', '.join(tools.names)}")
        passed += 1
    except Exception as e:
        print(f"  ✗ 工具系统错误: {e}")
        failed += 1

    # 5. 工具实际调用（think + shell 简单命令）
    try:
        r1 = tools.call("think", prompt="我在测试")
        assert not r1.error, f"think error: {r1.content}"
        r2 = tools.call("shell", cmd="echo hello-world")
        assert "hello-world" in r2.content, f"shell unexpected: {r2.content}"
        print("  ✓ think 和 shell 工具正常工作")
        passed += 1
    except Exception as e:
        print(f"  ✗ 工具调用失败: {e}")
        failed += 1

    # 6. 会话管理
    try:
        from superclaw.session import SessionManager
        sm = SessionManager()
        s = sm.get("test")
        s.add("user", "你好")
        s.add("assistant", "你好")
        msgs = s.to_messages()
        assert any(m["role"] == "user" for m in msgs)
        print("  ✓ 会话管理正常")
        passed += 1
    except Exception as e:
        print(f"  ✗ 会话管理失败: {e}")
        failed += 1

    # 7. 完整 Agent 运行（mock provider）
    try:
        cfg = load_config()
        cfg.llm.provider = "mock"
        from superclaw import Agent
        a = Agent(cfg=cfg)
        result = a.run("简单测试问题，不需要调用工具，直接回答",
                      session_key="test-integration",
                      verbose=False)
        assert isinstance(result.content, str) and len(result.content) > 0
        print(f"  ✓ Agent 完整运行: {result.content[:60]}...")
        passed += 1
    except Exception as e:
        print(f"  ✗ Agent 运行失败: {e}")
        failed += 1

    # 总结
    print(f"\n{'='*60}")
    print(f"  结果:  ✓ {passed}  通过  |  ✗ {failed}  失败")
    print(f"{'='*60}\n")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
