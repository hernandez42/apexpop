"""测试 superclaw.agent / superclaw.tools / superclaw.session / superclaw.config

目标：将 agent.py / tools.py / session.py 三个核心模块覆盖率提升到 80%+。
使用 ScriptedProvider（脚本化 LLM）+ tmp_workspace 隔离文件系统。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from superclaw.agent import Agent, AgentResult, _parse_tool_call
from superclaw.config import (
    LLMConfig,
    SessionConfig,
    SuperclawConfig,
    ToolsConfig,
    load_config,
)
from superclaw.providers import BaseProvider, MockProvider, get_provider
from superclaw.session import Session, SessionManager
from superclaw.tools import (
    ToolRegistry,
    ToolResult,
    build_default_tools,
    scan_skills,
    tool,
)


# ============================================================
# 辅助：脚本化 Provider + 配置工厂
# ============================================================

class ScriptedProvider(BaseProvider):
    """按预设脚本顺序返回响应，用于测试 Agent 工具调用循环"""

    def __init__(self, responses, cfg=None):
        self.responses = list(responses)
        self.calls = []
        self.cfg = cfg

    def call(self, messages):
        self.calls.append(messages)
        if not self.responses:
            return "（脚本已耗尽）"
        return self.responses.pop(0)


def _make_cfg(workspace, *, web=True, max_it=5, shell=True,
              file_tools=True, think=True):
    """构建隔离的 SuperclawConfig，所有路径指向 tmp workspace"""
    return SuperclawConfig(
        llm=LLMConfig(provider="mock", model="mock-model"),
        session=SessionConfig(max_messages=50, path=str(workspace / "sessions")),
        tools=ToolsConfig(
            shell=shell, file=file_tools, web=web, think=think,
            max_tool_iterations=max_it,
        ),
        workspace=str(workspace),
    )


@pytest.fixture(autouse=True)
def _clear_memory_cache():
    """每个测试前后清空 MemoryStore 缓存，避免跨测试污染"""
    import superclaw.tools as _tm
    _tm._MEMORY_STORE_CACHE.clear()
    yield
    _tm._MEMORY_STORE_CACHE.clear()


# ============================================================
# _parse_tool_call — 工具调用解析
# ============================================================

def test_parse_tool_call_tag_format():
    name, args = _parse_tool_call("<tool shell><cmd>ls -la</cmd></tool>")
    assert name == "shell"
    assert args["cmd"] == "ls -la"


def test_parse_tool_call_tag_multiple_args():
    name, args = _parse_tool_call(
        "<tool file_write><path>a.txt</path><content>hi</content></tool>"
    )
    assert name == "file_write"
    assert args["path"] == "a.txt"
    assert args["content"] == "hi"


def test_parse_tool_call_json_tool_format():
    name, args = _parse_tool_call('{"tool": "shell", "args": {"cmd": "ls"}}')
    assert name == "shell"
    assert args["cmd"] == "ls"


def test_parse_tool_call_json_name_format():
    name, args = _parse_tool_call('{"name": "shell", "input": {"cmd": "pwd"}}')
    assert name == "shell"
    assert args["cmd"] == "pwd"


def test_parse_tool_call_no_match_plain_text():
    assert _parse_tool_call("普通文本，没有工具调用") is None


def test_parse_tool_call_no_match_empty():
    assert _parse_tool_call("") is None


def test_parse_tool_call_invalid_json():
    # 非 JSON 文本且无 tool 标签 → None
    assert _parse_tool_call("{这不是 json") is None


# ============================================================
# Agent — 实例化与基础
# ============================================================

def test_agent_init_with_mock_provider(tmp_workspace):
    cfg = _make_cfg(tmp_workspace)
    agent = Agent(cfg=cfg)
    assert isinstance(agent.provider, MockProvider)
    assert agent.max_tool_iterations == 5
    assert agent.system_prompt  # 非空
    assert agent.loaded_skills == []  # 无 skills 目录


def test_agent_init_autoload_skills(tmp_workspace):
    """workspace 下有 skills/ 目录时应自动加载"""
    skills_dir = tmp_workspace / "skills"
    skills_dir.mkdir()
    (skills_dir / "auto.md").write_text(
        "# Auto Skill\n- 触发词: auto\n自动加载的技能\n", encoding="utf-8"
    )
    cfg = _make_cfg(tmp_workspace)
    agent = Agent(cfg=cfg, provider=ScriptedProvider([]))
    assert len(agent.loaded_skills) == 1
    assert "Auto Skill" in agent.system_prompt


def test_agent_set_system_prompt(tmp_workspace):
    cfg = _make_cfg(tmp_workspace)
    agent = Agent(cfg=cfg, provider=ScriptedProvider([]))
    agent.set_system_prompt("自定义提示")
    assert agent.system_prompt == "自定义提示"


def test_agent_add_skill_success(tmp_workspace):
    skill_file = tmp_workspace / "my_skill.md"
    skill_file.write_text("# My Skill\n技能正文内容\n", encoding="utf-8")
    cfg = _make_cfg(tmp_workspace)
    agent = Agent(cfg=cfg, provider=ScriptedProvider([]))
    assert agent.add_skill(str(skill_file)) is True
    assert "My Skill" in agent.system_prompt
    assert "技能正文内容" in agent.system_prompt


def test_agent_add_skill_nonexistent(tmp_workspace):
    cfg = _make_cfg(tmp_workspace)
    agent = Agent(cfg=cfg, provider=ScriptedProvider([]))
    assert agent.add_skill("/nonexistent/skill.md") is False


def test_agent_add_skill_empty_file(tmp_workspace):
    skill_file = tmp_workspace / "empty.md"
    skill_file.write_text("", encoding="utf-8")
    cfg = _make_cfg(tmp_workspace)
    agent = Agent(cfg=cfg, provider=ScriptedProvider([]))
    assert agent.add_skill(str(skill_file)) is False


# ============================================================
# Agent.run — 单轮 / 多轮 / 边界
# ============================================================

def test_agent_run_single_turn_no_tool(tmp_workspace):
    """单轮对话：LLM 直接给最终答案，不调工具"""
    cfg = _make_cfg(tmp_workspace)
    provider = ScriptedProvider(["你好，我是 superclaw"])
    agent = Agent(cfg=cfg, provider=provider)
    result = agent.run("你好")
    assert isinstance(result, AgentResult)
    assert result.content == "你好，我是 superclaw"
    assert result.tools_used == []
    assert result.iterations == 1
    assert result.total_time_ms >= 0


def test_agent_run_multi_turn_tool_call(tmp_workspace):
    """多轮：先调 think 工具，再给最终答案"""
    cfg = _make_cfg(tmp_workspace)
    provider = ScriptedProvider([
        "<tool think><prompt>分析用户问题</prompt></tool>",
        "分析完成，答案是 42",
    ])
    agent = Agent(cfg=cfg, provider=provider)
    result = agent.run("分析一下")
    assert result.iterations == 2
    assert "think" in result.tools_used
    assert result.content == "分析完成，答案是 42"
    assert len(result.tool_outputs) == 1


def test_agent_run_unknown_tool(tmp_workspace):
    """LLM 请求未知工具 → 注入错误消息后继续"""
    cfg = _make_cfg(tmp_workspace)
    provider = ScriptedProvider([
        "<tool nonexistent_tool><x>1</x></tool>",
        "最终答案",
    ])
    agent = Agent(cfg=cfg, provider=provider)
    result = agent.run("测试未知工具")
    assert result.iterations == 2
    assert "nonexistent_tool" not in result.tools_used
    assert result.content == "最终答案"


def test_agent_run_max_iterations(tmp_workspace):
    """达到 max_tool_iterations 后让 LLM 总结"""
    cfg = _make_cfg(tmp_workspace, max_it=2)
    provider = ScriptedProvider([
        "<tool think><prompt>思考1</prompt></tool>",
        "<tool think><prompt>思考2</prompt></tool>",
        "已达上限的总结回答",
    ])
    agent = Agent(cfg=cfg, provider=provider)
    result = agent.run("无限工具调用")
    assert result.iterations == 2
    assert len(result.tools_used) == 2
    assert result.content == "已达上限的总结回答"


def test_agent_run_empty_input(tmp_workspace):
    cfg = _make_cfg(tmp_workspace)
    agent = Agent(cfg=cfg, provider=ScriptedProvider([]))
    result = agent.run("")
    assert result.content == "你说什么？"
    assert result.iterations == 0


def test_agent_run_whitespace_input(tmp_workspace):
    cfg = _make_cfg(tmp_workspace)
    agent = Agent(cfg=cfg, provider=ScriptedProvider([]))
    result = agent.run("   \n\t  ")
    assert result.content == "你说什么？"


def test_agent_run_with_mock_provider(tmp_workspace):
    """使用真实 MockProvider 验证集成"""
    cfg = _make_cfg(tmp_workspace)
    agent = Agent(cfg=cfg)  # 默认 MockProvider
    result = agent.run("分析这个问题")
    assert isinstance(result, AgentResult)
    assert result.content
    assert result.iterations >= 1


def test_agent_run_verbose(tmp_workspace, capsys):
    """verbose=True 时打印调试信息"""
    cfg = _make_cfg(tmp_workspace)
    provider = ScriptedProvider([
        "<tool think><prompt>分析</prompt></tool>",
        "最终答案",
    ])
    agent = Agent(cfg=cfg, provider=provider)
    agent.run("测试", verbose=True)
    captured = capsys.readouterr()
    assert "[turn" in captured.out
    assert "调用工具" in captured.out


def test_agent_run_unknown_tool_verbose(tmp_workspace, capsys):
    cfg = _make_cfg(tmp_workspace)
    provider = ScriptedProvider([
        "<tool badtool><x>1</x></tool>",
        "最终答案",
    ])
    agent = Agent(cfg=cfg, provider=provider)
    agent.run("测试", verbose=True)
    captured = capsys.readouterr()
    assert "未知工具" in captured.out


def test_agent_run_tool_error_verbose(tmp_workspace, capsys):
    """工具执行异常 + verbose 时打印错误"""
    cfg = _make_cfg(tmp_workspace)
    tools = build_default_tools(
        str(tmp_workspace), shell=False, file_tools=False, web=False, think=False
    )
    def _boom(**kwargs):
        raise RuntimeError("工具爆炸")
    tools.register("boom", _boom, "会爆炸", {})

    provider = ScriptedProvider([
        "<tool boom><x>1</x></tool>",
        "最终答案",
    ])
    agent = Agent(cfg=cfg, provider=provider, tools=tools)
    agent.run("测试", verbose=True)
    captured = capsys.readouterr()
    assert "错误" in captured.out
    assert "爆炸" in captured.out


def test_agent_run_json_tool_call_format(tmp_workspace):
    """LLM 用 JSON 格式调用工具"""
    cfg = _make_cfg(tmp_workspace)
    provider = ScriptedProvider([
        '{"tool": "think", "args": {"prompt": "json 方式思考"}}',
        "json 工具调用完成",
    ])
    agent = Agent(cfg=cfg, provider=provider)
    result = agent.run("测试 json 工具")
    assert result.iterations == 2
    assert "think" in result.tools_used


def test_agent_session_persistence_across_instances(tmp_workspace):
    """跨 Agent 实例恢复对话历史"""
    cfg = _make_cfg(tmp_workspace)
    provider1 = ScriptedProvider(["第一次回答"])
    agent1 = Agent(cfg=cfg, provider=provider1)
    agent1.run("你好", session_key="persist_test")

    # 新实例，同一 session 存储
    provider2 = ScriptedProvider(["第二次回答"])
    agent2 = Agent(cfg=cfg, provider=provider2)
    session = agent2.sessions.get("persist_test")
    # 历史应从磁盘恢复
    assert len(session) > 0
    assert any(m["content"] == "你好" for m in session.messages)
    assert any(m["content"] == "第一次回答" for m in session.messages)


# ============================================================
# Agent.chat — 交互命令
# ============================================================

def _feed_inputs(monkeypatch, inputs):
    """模拟 input() 依次返回 inputs 中的字符串"""
    it = iter(inputs)
    monkeypatch.setattr("builtins.input", lambda prompt="": next(it))


def test_agent_chat_tools_command(tmp_workspace, capsys, monkeypatch):
    cfg = _make_cfg(tmp_workspace)
    agent = Agent(cfg=cfg, provider=ScriptedProvider([]))
    _feed_inputs(monkeypatch, ["/tools", "exit"])
    agent.chat()
    captured = capsys.readouterr()
    assert "think" in captured.out or "shell" in captured.out


def test_agent_chat_skills_empty(tmp_workspace, capsys, monkeypatch):
    cfg = _make_cfg(tmp_workspace)
    agent = Agent(cfg=cfg, provider=ScriptedProvider([]))
    _feed_inputs(monkeypatch, ["/skills", "exit"])
    agent.chat()
    captured = capsys.readouterr()
    assert "无已加载 skill" in captured.out


def test_agent_chat_skills_loaded(tmp_workspace, capsys, monkeypatch):
    skills_dir = tmp_workspace / "skills"
    skills_dir.mkdir()
    (skills_dir / "chat_skill.md").write_text(
        "# Chat Skill\n内容\n", encoding="utf-8"
    )
    cfg = _make_cfg(tmp_workspace)
    agent = Agent(cfg=cfg, provider=ScriptedProvider([]))
    _feed_inputs(monkeypatch, ["/skills", "exit"])
    agent.chat()
    captured = capsys.readouterr()
    assert "chat_skill.md" in captured.out


def test_agent_chat_memory_command(tmp_workspace, capsys, monkeypatch):
    """/memory 命令触发记忆查询"""
    cfg = _make_cfg(tmp_workspace)
    agent = Agent(cfg=cfg, provider=ScriptedProvider([]))
    _feed_inputs(monkeypatch, ["/memory", "exit"])
    agent.chat()
    captured = capsys.readouterr()
    assert "记忆系统" in captured.out or "状态" in captured.out


def test_agent_chat_skill_load_command(tmp_workspace, capsys, monkeypatch):
    skill_file = tmp_workspace / "loaded.md"
    skill_file.write_text("# Loaded Skill\n正文\n", encoding="utf-8")
    cfg = _make_cfg(tmp_workspace)
    agent = Agent(cfg=cfg, provider=ScriptedProvider([]))
    _feed_inputs(monkeypatch, [f"/skill {skill_file}", "/skills", "exit"])
    agent.chat()
    captured = capsys.readouterr()
    assert "已加载 skill" in captured.out
    assert "loaded.md" in captured.out


def test_agent_chat_skill_load_failure(tmp_workspace, capsys, monkeypatch):
    cfg = _make_cfg(tmp_workspace)
    agent = Agent(cfg=cfg, provider=ScriptedProvider([]))
    _feed_inputs(monkeypatch, ["/skill /nonexistent.md", "exit"])
    agent.chat()
    captured = capsys.readouterr()
    assert "加载失败" in captured.out


def test_agent_chat_clear_command(tmp_workspace, capsys, monkeypatch):
    cfg = _make_cfg(tmp_workspace)
    agent = Agent(cfg=cfg, provider=ScriptedProvider(["回答"]))
    agent.run("你好", session_key="default")  # 先有历史
    _feed_inputs(monkeypatch, ["/clear", "exit"])
    agent.chat()
    captured = capsys.readouterr()
    assert "会话已清除" in captured.out


def test_agent_chat_with_run(tmp_workspace, capsys, monkeypatch):
    cfg = _make_cfg(tmp_workspace)
    provider = ScriptedProvider(["这是回答"])
    agent = Agent(cfg=cfg, provider=provider)
    _feed_inputs(monkeypatch, ["你好", "exit"])
    agent.chat()
    captured = capsys.readouterr()
    assert "这是回答" in captured.out


def test_agent_chat_quit_command(tmp_workspace, monkeypatch):
    cfg = _make_cfg(tmp_workspace)
    agent = Agent(cfg=cfg, provider=ScriptedProvider([]))
    _feed_inputs(monkeypatch, ["quit"])
    agent.chat()  # 应正常退出


def test_agent_chat_empty_input(tmp_workspace, monkeypatch):
    cfg = _make_cfg(tmp_workspace)
    agent = Agent(cfg=cfg, provider=ScriptedProvider([]))
    _feed_inputs(monkeypatch, ["", "exit"])
    agent.chat()  # 空输入应跳过，不崩溃


def test_agent_chat_eof(tmp_workspace, monkeypatch):
    cfg = _make_cfg(tmp_workspace)
    agent = Agent(cfg=cfg, provider=ScriptedProvider([]))
    def _raise_eof(prompt=""):
        raise EOFError()
    monkeypatch.setattr("builtins.input", _raise_eof)
    agent.chat()  # EOF 应正常退出


# ============================================================
# ToolRegistry — 注册 / 调用 / schema
# ============================================================

def test_tool_registry_register_and_call():
    reg = ToolRegistry()
    reg.register("echo", lambda msg: msg, "回显工具",
                 {"msg": {"type": "string", "description": "消息"}})
    assert reg.has("echo")
    assert "echo" in reg.names
    result = reg.call("echo", msg="hello")
    assert isinstance(result, ToolResult)
    assert result.content == "hello"
    assert result.error is False
    assert result.tool_name == "echo"


def test_tool_registry_unknown_tool():
    reg = ToolRegistry()
    result = reg.call("nonexistent")
    assert result.error is True
    assert "未知工具" in result.content


def test_tool_registry_tool_exception():
    def _bad(**kwargs):
        raise ValueError("boom")
    reg = ToolRegistry()
    reg.register("bad", _bad, "会出错", {})
    result = reg.call("bad")
    assert result.error is True
    assert "boom" in result.content
    # 异常也应记入历史
    assert len(reg.history) == 1
    assert reg.history[0]["error"] is True


def test_tool_registry_history_success():
    reg = ToolRegistry()
    reg.register("echo", lambda msg: msg)
    reg.call("echo", msg="hi")
    assert len(reg.history) == 1
    assert reg.history[0]["tool"] == "echo"
    assert reg.history[0]["error"] is False
    assert "hi" in reg.history[0]["result"]


def test_tool_registry_to_llm_instructions():
    reg = ToolRegistry()
    reg.register("echo", lambda msg: msg, "回显工具",
                 {"msg": {"type": "string", "description": "消息"}})
    instr = reg.to_llm_instructions()
    assert "echo" in instr
    assert "回显工具" in instr
    assert "msg" in instr
    assert "tool" in instr  # 格式说明


def test_tool_registry_get_description():
    reg = ToolRegistry()
    reg.register("echo", lambda msg: msg, "回显", {})
    assert reg.get_description("echo") == "回显"
    assert reg.get_description("nonexistent") is None


def test_tool_registry_get_params():
    reg = ToolRegistry()
    params = {"x": {"type": "string"}}
    reg.register("echo", lambda x: x, "回显", params)
    assert reg.get_params("echo") == params
    assert reg.get_params("nonexistent") == {}


def test_tool_registry_set_workspace():
    reg = ToolRegistry()
    reg.set_workspace("/tmp/test_ws")
    assert str(reg._workspace) == "/tmp/test_ws"


def test_tool_registry_empty_names():
    reg = ToolRegistry()
    assert reg.names == []


def test_tool_decorator():
    @tool
    def my_tool(x):
        return x
    assert getattr(my_tool, "_is_tool", False) is True


# ============================================================
# 内置工具 — shell / file / think / web / memory
# ============================================================

def test_shell_tool_echo(tmp_workspace):
    tools = build_default_tools(
        str(tmp_workspace), shell=True, file_tools=False, web=False, think=False
    )
    result = tools.call("shell", cmd="echo hello")
    assert "hello" in result.content
    assert "exit=0" in result.content


def test_shell_tool_shlex_quoted_args(tmp_workspace):
    """shlex.split + shell=False 路径：带引号的参数应正确解析"""
    tools = build_default_tools(
        str(tmp_workspace), shell=True, file_tools=False, web=False, think=False
    )
    result = tools.call("shell", cmd="echo 'hello world'")
    assert "hello world" in result.content


def test_shell_tool_failing_command(tmp_workspace):
    tools = build_default_tools(
        str(tmp_workspace), shell=True, file_tools=False, web=False, think=False
    )
    result = tools.call("shell", cmd="ls /nonexistent_dir_xyz_12345")
    assert "exit=" in result.content
    # 非零退出码
    assert "exit=0" not in result.content


def test_shell_tool_empty_command(tmp_workspace):
    tools = build_default_tools(
        str(tmp_workspace), shell=True, file_tools=False, web=False, think=False
    )
    result = tools.call("shell", cmd="")
    assert "空命令" in result.content


def test_shell_tool_no_output(tmp_workspace):
    """命令成功但无输出"""
    tools = build_default_tools(
        str(tmp_workspace), shell=True, file_tools=False, web=False, think=False
    )
    result = tools.call("shell", cmd="true")
    assert "无输出" in result.content or "exit=0" in result.content


def test_file_read_tool(tmp_workspace):
    (tmp_workspace / "test.txt").write_text("hello file content", encoding="utf-8")
    tools = build_default_tools(
        str(tmp_workspace), shell=False, file_tools=True, web=False, think=False
    )
    result = tools.call("file_read", path="test.txt")
    assert "hello file content" in result.content


def test_file_read_tool_absolute_path(tmp_workspace):
    f = tmp_workspace / "abs.txt"
    f.write_text("absolute path content", encoding="utf-8")
    tools = build_default_tools(
        str(tmp_workspace), shell=False, file_tools=True, web=False, think=False
    )
    result = tools.call("file_read", path=str(f))
    assert "absolute path content" in result.content


def test_file_read_tool_nonexistent(tmp_workspace):
    tools = build_default_tools(
        str(tmp_workspace), shell=False, file_tools=True, web=False, think=False
    )
    result = tools.call("file_read", path="nonexistent.txt")
    assert "不存在" in result.content


def test_file_write_tool(tmp_workspace):
    tools = build_default_tools(
        str(tmp_workspace), shell=False, file_tools=True, web=False, think=False
    )
    result = tools.call("file_write", path="output.txt", content="written content")
    assert "成功" in result.content
    assert (tmp_workspace / "output.txt").read_text(encoding="utf-8") == "written content"


def test_file_write_tool_creates_parent_dirs(tmp_workspace):
    tools = build_default_tools(
        str(tmp_workspace), shell=False, file_tools=True, web=False, think=False
    )
    tools.call("file_write", path="sub/dir/file.txt", content="nested")
    assert (tmp_workspace / "sub" / "dir" / "file.txt").read_text() == "nested"


def test_think_tool(tmp_workspace):
    tools = build_default_tools(
        str(tmp_workspace), shell=False, file_tools=False, web=False, think=True
    )
    result = tools.call("think", prompt="分析这个问题")
    assert "思考" in result.content
    assert "分析这个问题" in result.content


def test_web_get_http(tmp_workspace, monkeypatch):
    tools = build_default_tools(
        str(tmp_workspace), shell=False, file_tools=False, web=True, think=False
    )

    class _FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def read(self):
            return b"<html>hello web</html>"

    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=15: _FakeResp())
    result = tools.call("web_get", url="http://example.com")
    assert "hello web" in result.content


def test_web_get_https(tmp_workspace, monkeypatch):
    tools = build_default_tools(
        str(tmp_workspace), shell=False, file_tools=False, web=True, think=False
    )

    class _FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def read(self):
            return b"secure content"

    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=15: _FakeResp())
    result = tools.call("web_get", url="https://secure.example.com")
    assert "secure content" in result.content


def test_web_get_file_scheme_blocked(tmp_workspace):
    """file:// scheme 应被安全拦截（bandit 修复后的安全逻辑）"""
    tools = build_default_tools(
        str(tmp_workspace), shell=False, file_tools=False, web=True, think=False
    )
    result = tools.call("web_get", url="file:///etc/passwd")
    assert "安全错误" in result.content
    assert "file" in result.content


def test_web_get_network_error(tmp_workspace, monkeypatch):
    tools = build_default_tools(
        str(tmp_workspace), shell=False, file_tools=False, web=True, think=False
    )

    def _raise(*a, **kw):
        raise ConnectionError("network down")

    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", _raise)
    result = tools.call("web_get", url="http://example.com")
    assert "网络错误" in result.content


def test_memory_tool_query(tmp_workspace):
    """memory 工具：自然语言查询记忆系统"""
    (tmp_workspace / "SOUL.md").write_text(
        "# 灵魂\nsuperclaw 核心身份定义\n", encoding="utf-8"
    )
    tools = build_default_tools(
        str(tmp_workspace), shell=False, file_tools=False, web=False, think=False
    )
    result = tools.call("memory", query="灵魂")
    assert "灵魂" in result.content or "SOUL" in result.content


def test_memory_tool_status_query(tmp_workspace):
    tools = build_default_tools(
        str(tmp_workspace), shell=False, file_tools=False, web=False, think=False
    )
    result = tools.call("memory", query="状态")
    assert "状态" in result.content or "记忆系统" in result.content


def test_memory_read_tool_existing(tmp_workspace):
    (tmp_workspace / "SOUL.md").write_text(
        "# 灵魂\n核心身份\n", encoding="utf-8"
    )
    tools = build_default_tools(
        str(tmp_workspace), shell=False, file_tools=False, web=False, think=False
    )
    result = tools.call("memory_read", path="SOUL.md")
    assert "灵魂" in result.content


def test_memory_read_tool_nonexistent(tmp_workspace):
    tools = build_default_tools(
        str(tmp_workspace), shell=False, file_tools=False, web=False, think=False
    )
    result = tools.call("memory_read", path="nonexistent.md")
    assert "不存在" in result.content


def test_build_default_tools_all_disabled(tmp_workspace):
    """所有工具开关关闭时只注册 memory + memory_read"""
    tools = build_default_tools(
        str(tmp_workspace), shell=False, file_tools=False, web=False, think=False
    )
    names = tools.names
    assert "memory" in names
    assert "memory_read" in names
    assert "shell" not in names
    assert "file_read" not in names
    assert "web_get" not in names
    assert "think" not in names


def test_build_default_tools_all_enabled(tmp_workspace):
    tools = build_default_tools(
        str(tmp_workspace), shell=True, file_tools=True, web=True, think=True
    )
    names = tools.names
    for expected in ["shell", "file_read", "file_write", "web_get", "think",
                     "memory", "memory_read"]:
        assert expected in names


# ============================================================
# scan_skills
# ============================================================

def test_scan_skills(tmp_workspace):
    skills_dir = tmp_workspace / "skills"
    skills_dir.mkdir()
    (skills_dir / "shell.md").write_text(
        "# Shell Skill\n- 触发词: 执行, 命令\n正文\n", encoding="utf-8"
    )
    (skills_dir / "think.md").write_text(
        "# Think Skill\n- 触发词: 思考\n正文\n", encoding="utf-8"
    )
    skills = scan_skills(skills_dir)
    assert len(skills) == 2
    titles = [s["title"] for s in skills]
    assert "Shell Skill" in titles
    assert "Think Skill" in titles
    shell_skill = [s for s in skills if s["title"] == "Shell Skill"][0]
    assert "执行" in shell_skill["triggers"]
    assert "命令" in shell_skill["triggers"]
    assert shell_skill["preview"]


def test_scan_skills_nonexistent_dir(tmp_workspace):
    assert scan_skills(tmp_workspace / "no_such_dir") == []


def test_scan_skills_uses_filename_as_title(tmp_workspace):
    """无 # 标题的文件用文件名作为 title"""
    skills_dir = tmp_workspace / "skills"
    skills_dir.mkdir()
    (skills_dir / "notitle.md").write_text("没有标题的 skill\n", encoding="utf-8")
    skills = scan_skills(skills_dir)
    assert len(skills) == 1
    assert skills[0]["title"] == "notitle"


# ============================================================
# Session / SessionManager
# ============================================================

def test_session_add_and_len():
    s = Session("test", max_messages=50)
    s.add("user", "hello")
    s.add("assistant", "hi")
    assert len(s) == 2


def test_session_to_messages_with_system_prompt():
    s = Session("test")
    s.add("user", "hello")
    msgs = s.to_messages("system prompt")
    assert msgs[0]["role"] == "system"
    assert msgs[0]["content"] == "system prompt"
    assert len(msgs) == 2


def test_session_to_messages_without_system_prompt():
    s = Session("test")
    s.add("user", "hello")
    msgs = s.to_messages("")
    assert len(msgs) == 1
    assert msgs[0]["role"] == "user"


def test_session_last_user():
    s = Session("test")
    s.add("user", "first")
    s.add("assistant", "resp")
    s.add("user", "second")
    assert s.last_user() == "second"


def test_session_last_user_empty():
    s = Session("test")
    assert s.last_user() == ""


def test_session_add_with_extra_metadata():
    s = Session("test")
    s.add("user", "hello", extra_field="value")
    assert s.messages[0]["extra_field"] == "value"


def test_session_truncation():
    """超过 max_messages 时截断，保留 system + 最近消息"""
    s = Session("test", max_messages=3)
    for i in range(5):
        s.add("user", f"msg{i}")
    assert len(s) <= 3


def test_session_truncation_with_system():
    s = Session("test", max_messages=4)
    s.add("system", "sys_prompt")
    for i in range(6):
        s.add("user", f"msg{i}")
    assert len(s) <= 4
    assert s.messages[0]["role"] == "system"


def test_session_manager_get_creates():
    mgr = SessionManager()
    s1 = mgr.get("a")
    s2 = mgr.get("a")
    assert s1 is s2  # 同一实例
    s3 = mgr.get("b")
    assert s3 is not s1
    assert "a" in mgr.keys()
    assert "b" in mgr.keys()


def test_session_manager_save_and_load(tmp_workspace):
    storage = tmp_workspace / "sessions"
    mgr1 = SessionManager(storage_path=str(storage), max_messages=50)
    s = mgr1.get("test")
    s.add("user", "hello")
    s.add("assistant", "world")
    assert mgr1.save("test") is True
    assert (storage / "test.json").exists()

    # 新 manager 从磁盘加载
    mgr2 = SessionManager(storage_path=str(storage), max_messages=50)
    s2 = mgr2.get("test")
    assert len(s2) == 2
    assert s2.messages[0]["content"] == "hello"
    assert s2.messages[1]["content"] == "world"


def test_session_manager_save_no_storage():
    mgr = SessionManager(storage_path=None)
    s = mgr.get("test")
    s.add("user", "hi")
    assert mgr.save("test") is False


def test_session_manager_save_no_session(tmp_workspace):
    mgr = SessionManager(storage_path=str(tmp_workspace / "sessions"))
    assert mgr.save("nonexistent") is False


def test_session_manager_save_failure(tmp_workspace, monkeypatch):
    """save 异常时返回 False"""
    storage = tmp_workspace / "sessions"
    mgr = SessionManager(storage_path=str(storage), max_messages=50)
    s = mgr.get("test")
    s.add("user", "hi")

    import superclaw.session as session_mod
    def _failing_dump(*args, **kwargs):
        raise IOError("disk full")
    monkeypatch.setattr(session_mod.json, "dump", _failing_dump)
    assert mgr.save("test") is False


def test_session_manager_load_nonexistent(tmp_workspace):
    """_load 对不存在的文件返回 False"""
    mgr = SessionManager(
        storage_path=str(tmp_workspace / "sessions"), max_messages=50
    )
    s = mgr.get("never_saved")
    assert len(s) == 0


def test_session_manager_load_corrupt_json(tmp_workspace):
    """_load 对损坏的 JSON 返回 False，不崩溃"""
    storage = tmp_workspace / "sessions"
    storage.mkdir()
    (storage / "bad.json").write_text("not valid json {{{", encoding="utf-8")
    mgr = SessionManager(storage_path=str(storage), max_messages=50)
    s = mgr.get("bad")
    assert len(s) == 0  # 加载失败，空会话


def test_session_manager_clear(tmp_workspace):
    storage = tmp_workspace / "sessions"
    mgr = SessionManager(storage_path=str(storage), max_messages=50)
    s = mgr.get("test")
    s.add("user", "hello")
    mgr.save("test")
    assert (storage / "test.json").exists()
    mgr.clear("test")
    assert not (storage / "test.json").exists()
    s2 = mgr.get("test")
    assert len(s2) == 0


def test_session_manager_clear_no_storage():
    """无 storage_path 时 clear 不崩溃"""
    mgr = SessionManager(storage_path=None)
    mgr.get("test")
    mgr.clear("test")  # 不应崩溃


def test_session_manager_keys(tmp_workspace):
    mgr = SessionManager(storage_path=str(tmp_workspace / "sessions"))
    mgr.get("a")
    mgr.get("b")
    mgr.get("c")
    assert mgr.keys() == ["a", "b", "c"]


# ============================================================
# Config — load_config / SuperclawConfig / 环境变量
# ============================================================

def test_load_config_default(tmp_workspace, monkeypatch):
    """默认配置：provider=mock, shell=True 等"""
    for var in ["SUPERCLAW_PROVIDER", "SUPERCLAW_MODEL", "API_KEY",
                "MOCK_API_KEY", "MOCK_BASE_URL"]:
        monkeypatch.delenv(var, raising=False)
    cfg = load_config()
    assert cfg.llm.provider == "mock"
    assert cfg.llm.model == "mock-model"
    assert cfg.tools.shell is True
    assert cfg.tools.file is True
    assert cfg.tools.web is False
    assert cfg.tools.think is True
    assert cfg.tools.max_tool_iterations == 5
    assert cfg.session.max_messages == 50


def test_load_config_from_explicit_json(tmp_workspace):
    config_file = tmp_workspace / "my_config.json"
    config_file.write_text(json.dumps({
        "llm": {"provider": "deepseek", "model": "deepseek-chat"},
        "tools": {"max_tool_iterations": 10, "web": True},
    }), encoding="utf-8")
    cfg = load_config(str(config_file))
    assert cfg.llm.provider == "deepseek"
    assert cfg.llm.model == "deepseek-chat"
    assert cfg.tools.max_tool_iterations == 10
    assert cfg.tools.web is True
    # 未覆盖的字段保持默认
    assert cfg.tools.shell is True


def test_load_config_from_cwd(tmp_workspace):
    """从 cwd 的 config.json 加载"""
    (tmp_workspace / "config.json").write_text(json.dumps({
        "llm": {"provider": "ollama"},
    }), encoding="utf-8")
    cfg = load_config()
    assert cfg.llm.provider == "ollama"


def test_load_config_corrupt_json(tmp_workspace):
    """损坏的 JSON 应跳过，回退默认"""
    config_file = tmp_workspace / "bad.json"
    config_file.write_text("not valid json {{{", encoding="utf-8")
    cfg = load_config(str(config_file))
    assert cfg.llm.provider == "mock"  # 默认


def test_load_config_env_override(tmp_workspace, monkeypatch):
    monkeypatch.setenv("SUPERCLAW_PROVIDER", "groq")
    monkeypatch.setenv("SUPERCLAW_MODEL", "llama-3")
    monkeypatch.setenv("GROQ_API_KEY", "test-key-123")
    monkeypatch.setenv("GROQ_BASE_URL", "https://custom.api.com")
    cfg = load_config()
    assert cfg.llm.provider == "groq"
    assert cfg.llm.model == "llama-3"
    assert cfg.llm.api_key == "test-key-123"
    assert cfg.llm.base_url == "https://custom.api.com"


def test_load_config_env_api_key_generic(tmp_workspace, monkeypatch):
    """无 PROVIDER_API_KEY 时回退到 API_KEY"""
    monkeypatch.setenv("SUPERCLAW_PROVIDER", "mock")
    monkeypatch.setenv("API_KEY", "generic-key")
    monkeypatch.delenv("MOCK_API_KEY", raising=False)
    cfg = load_config()
    assert cfg.llm.api_key == "generic-key"


def test_superclaw_config_fields():
    cfg = SuperclawConfig()
    assert isinstance(cfg.llm, LLMConfig)
    assert isinstance(cfg.session, SessionConfig)
    assert isinstance(cfg.tools, ToolsConfig)
    assert cfg.llm.provider == "mock"
    assert cfg.llm.temperature == 0.7
    assert cfg.llm.max_tokens == 2048
    assert cfg.llm.timeout == 60
    assert cfg.session.max_messages == 50
    assert cfg.session.path == "~/.superclaw/sessions"
    assert cfg.tools.think is True


# ============================================================
# Provider — get_provider / MockProvider
# ============================================================

def test_get_provider_mock():
    cfg = LLMConfig(provider="mock", model="m")
    p = get_provider(cfg)
    assert isinstance(p, MockProvider)


def test_get_provider_unknown_falls_back_to_mock():
    cfg = LLMConfig(provider="unknown_provider", model="m")
    p = get_provider(cfg)
    assert isinstance(p, MockProvider)


def test_mock_provider_call_returns_text():
    p = MockProvider(LLMConfig(provider="mock", model="m"))
    messages = [{"role": "user", "content": "分析这个问题"}]
    result = p.call(messages)
    assert isinstance(result, str)
    assert len(result) > 0
