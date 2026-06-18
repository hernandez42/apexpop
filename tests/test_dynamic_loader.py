"""测试 superclaw.dynamic_loader — 动态工具加载 / 模块热加载 / 增强 skill"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from superclaw.dynamic_loader import (
    DynamicToolLoader,
    EnhancedSkillLoader,
    ModuleHotReloader,
)
from superclaw.tools import ToolRegistry


# ============================================================
# 公共 fixture：清理 sys.modules 中动态加载的模块，避免测试间污染
# ============================================================

@pytest.fixture(autouse=True)
def _cleanup_dynamic_modules():
    yield
    to_remove = [k for k in list(sys.modules) if k.startswith("_superclaw_dynamic_")]
    for k in to_remove:
        del sys.modules[k]


# ============================================================
# 简易 fake agent，用于测试 EnhancedSkillLoader 的 prompt 注入
# ============================================================

class _FakeAgent:
    """模拟 Agent 的 add_skill / system_prompt 行为"""

    def __init__(self):
        self.system_prompt = "base prompt"
        self.added_skills = []

    def add_skill(self, skill_file: str) -> bool:
        path = Path(skill_file)
        if not path.exists():
            return False
        try:
            md = path.read_text(encoding="utf-8")
        except Exception:
            return False
        lines = md.strip().splitlines()
        if not lines:
            return False
        title = lines[0].lstrip("#").strip()
        body = "\n".join(lines[1:]).strip()
        self.system_prompt = f"{self.system_prompt}\n\n## Skill: {title}\n{body}"
        self.added_skills.append(skill_file)
        return True


# ============================================================
# DynamicToolLoader — 从文件加载
# ============================================================

def test_load_from_file_registers_and_calls(tmp_workspace):
    """从真实 .py 文件加载函数并注册成工具、调用成功"""
    tools_dir = tmp_workspace / "dynamic-tools"
    tools_dir.mkdir()
    py_file = tools_dir / "weather.py"
    py_file.write_text(
        "def fetch_weather(city):\n"
        "    return f'{city} 晴天 25°C'\n",
        encoding="utf-8",
    )

    reg = ToolRegistry()
    loader = DynamicToolLoader(reg, tools_dir=tools_dir)

    ok = loader.load_from_file(
        file_path=py_file,
        tool_name="weather",
        function_name="fetch_weather",
        description="查询天气",
        params=[{"name": "city", "type": "string", "description": "城市名"}],
    )
    assert ok is True
    assert reg.has("weather")
    result = reg.call("weather", city="北京")
    assert result.error is False
    assert "北京" in result.content
    assert "25°C" in result.content


def test_load_from_code_loads_from_string(tmp_workspace):
    """load_from_code 从字符串加载"""
    reg = ToolRegistry()
    loader = DynamicToolLoader(reg, tools_dir=tmp_workspace / "dt")

    code = (
        "def add(a, b):\n"
        "    return str(int(a) + int(b))\n"
    )
    ok = loader.load_from_code(
        code=code,
        module_name="adder",
        tool_name="add_tool",
        function_name="add",
        description="加法",
        params=[
            {"name": "a", "type": "string", "description": "数字1"},
            {"name": "b", "type": "string", "description": "数字2"},
        ],
    )
    assert ok is True
    assert reg.has("add_tool")
    # 文件应被写入 tools_dir
    assert (tmp_workspace / "dt" / "adder.py").exists()
    result = reg.call("add_tool", a="3", b="5")
    assert result.error is False
    assert "8" in result.content


def test_unload_removes_tool(tmp_workspace):
    """unload 注销工具"""
    tools_dir = tmp_workspace / "dt"
    tools_dir.mkdir()
    py_file = tools_dir / "greet.py"
    py_file.write_text("def hi(name):\n    return f'hi {name}'\n", encoding="utf-8")

    reg = ToolRegistry()
    loader = DynamicToolLoader(reg, tools_dir=tools_dir)
    loader.load_from_file(py_file, "greet", "hi", "问候", [])

    assert reg.has("greet")
    assert loader.unload("greet") is True
    assert not reg.has("greet")
    # 再次 unload 返回 False
    assert loader.unload("greet") is False
    assert loader.list_dynamic() == []


def test_list_dynamic_returns_metadata(tmp_workspace):
    """list_dynamic 返回所有动态工具元信息"""
    tools_dir = tmp_workspace / "dt"
    tools_dir.mkdir()
    f1 = tools_dir / "t1.py"
    f1.write_text("def f1():\n    return '1'\n", encoding="utf-8")
    f2 = tools_dir / "t2.py"
    f2.write_text("def f2():\n    return '2'\n", encoding="utf-8")

    reg = ToolRegistry()
    loader = DynamicToolLoader(reg, tools_dir=tools_dir)
    loader.load_from_file(f1, "tool_one", "f1", "desc1", [])
    loader.load_from_file(f2, "tool_two", "f2", "desc2", [])

    listed = loader.list_dynamic()
    assert len(listed) == 2
    names = {item["tool_name"] for item in listed}
    assert names == {"tool_one", "tool_two"}
    # 元信息字段完整
    for item in listed:
        assert "file_path" in item
        assert "function_name" in item
        assert "description" in item
        assert "module_name" in item


def test_reload_picks_up_changes(tmp_workspace):
    """reload 重新加载，能拿到文件最新内容"""
    tools_dir = tmp_workspace / "dt"
    tools_dir.mkdir()
    py_file = tools_dir / "counter.py"
    py_file.write_text(
        "def get_value():\n    return 'v1'\n", encoding="utf-8"
    )

    reg = ToolRegistry()
    loader = DynamicToolLoader(reg, tools_dir=tools_dir)
    loader.load_from_file(py_file, "counter", "get_value", "计数", [])

    r1 = reg.call("counter")
    assert "v1" in r1.content

    # 修改文件
    py_file.write_text(
        "def get_value():\n    return 'v2'\n", encoding="utf-8"
    )

    assert loader.reload("counter") is True
    r2 = reg.call("counter")
    assert "v2" in r2.content

    # reload 不存在的工具返回 False
    assert loader.reload("not_exist") is False


# ============================================================
# DynamicToolLoader — 错误处理
# ============================================================

def test_load_from_file_not_exist(tmp_workspace):
    """文件不存在返回 False"""
    tools_dir = tmp_workspace / "dt"
    tools_dir.mkdir()
    reg = ToolRegistry()
    loader = DynamicToolLoader(reg, tools_dir=tools_dir)

    ok = loader.load_from_file(
        tools_dir / "nope.py", "t", "f", "d", []
    )
    assert ok is False
    assert not reg.has("t")


def test_load_from_file_function_not_exist(tmp_workspace):
    """函数不存在返回 False"""
    tools_dir = tmp_workspace / "dt"
    tools_dir.mkdir()
    py_file = tools_dir / "mod.py"
    py_file.write_text("def real_func():\n    return 'x'\n", encoding="utf-8")

    reg = ToolRegistry()
    loader = DynamicToolLoader(reg, tools_dir=tools_dir)
    ok = loader.load_from_file(py_file, "t", "missing_func", "d", [])
    assert ok is False
    assert not reg.has("t")


def test_load_from_file_syntax_error(tmp_workspace):
    """模块语法错误返回 False"""
    tools_dir = tmp_workspace / "dt"
    tools_dir.mkdir()
    py_file = tools_dir / "bad.py"
    py_file.write_text("def broken(:\n    pass\n", encoding="utf-8")

    reg = ToolRegistry()
    loader = DynamicToolLoader(reg, tools_dir=tools_dir)
    ok = loader.load_from_file(py_file, "t", "f", "d", [])
    assert ok is False
    assert not reg.has("t")


def test_load_from_file_path_traversal_rejected(tmp_workspace):
    """路径穿越防护：../etc/passwd 被拒绝"""
    tools_dir = tmp_workspace / "dt"
    tools_dir.mkdir()
    reg = ToolRegistry()
    loader = DynamicToolLoader(reg, tools_dir=tools_dir)

    # 相对路径穿越
    with pytest.raises(ValueError, match="路径穿越|安全错误"):
        loader.load_from_file(
            Path("../etc/passwd"), "t", "f", "d", []
        )

    # 绝对路径在 tools_dir 之外
    outside = tmp_workspace.parent / "outside.py"
    with pytest.raises(ValueError, match="路径穿越|安全错误"):
        loader.load_from_file(outside, "t", "f", "d", [])


def test_load_from_code_invalid_module_name(tmp_workspace):
    """模块名校验：非法模块名抛 ValueError"""
    reg = ToolRegistry()
    loader = DynamicToolLoader(reg, tools_dir=tmp_workspace / "dt")

    with pytest.raises(ValueError, match="非法模块名"):
        loader.load_from_code(
            "def f():\n    pass\n",
            module_name="123bad",
            tool_name="t",
            function_name="f",
            description="d",
            params=[],
        )

    with pytest.raises(ValueError, match="非法模块名"):
        loader.load_from_code(
            "def f():\n    pass\n",
            module_name="../escape",
            tool_name="t",
            function_name="f",
            description="d",
            params=[],
        )


def test_load_from_file_invalid_function_name(tmp_workspace):
    """函数名校验：非法函数名抛 ValueError"""
    tools_dir = tmp_workspace / "dt"
    tools_dir.mkdir()
    py_file = tools_dir / "m.py"
    py_file.write_text("def f():\n    pass\n", encoding="utf-8")

    reg = ToolRegistry()
    loader = DynamicToolLoader(reg, tools_dir=tools_dir)

    with pytest.raises(ValueError, match="非法函数名"):
        loader.load_from_file(py_file, "t", "bad-func!", "d", [])

    with pytest.raises(ValueError, match="非法工具名"):
        loader.load_from_file(py_file, "bad tool", "f", "d", [])


def test_load_from_file_refuses_overwrite_builtin(tmp_workspace):
    """不能覆盖已有内置工具（除非 force=True）"""
    tools_dir = tmp_workspace / "dt"
    tools_dir.mkdir()
    py_file = tools_dir / "override.py"
    py_file.write_text(
        "def shell(cmd):\n    return 'hijacked'\n", encoding="utf-8"
    )

    reg = ToolRegistry()
    # 先注册一个名为 shell 的"内置"工具
    reg.register("shell", lambda cmd: "builtin", "内置 shell", {})
    loader = DynamicToolLoader(reg, tools_dir=tools_dir)
    # shell 在 builtin 快照里
    assert "shell" in loader._builtin_tool_names

    # 不带 force → 拒绝
    with pytest.raises(ValueError, match="内置工具"):
        loader.load_from_file(py_file, "shell", "shell", "d", [])

    # 带 force → 允许
    ok = loader.load_from_file(py_file, "shell", "shell", "d", [], force=True)
    assert ok is True
    result = reg.call("shell", cmd="ls")
    assert "hijacked" in result.content


def test_load_from_code_invalid_tool_name(tmp_workspace):
    """load_from_code 也校验工具名"""
    reg = ToolRegistry()
    loader = DynamicToolLoader(reg, tools_dir=tmp_workspace / "dt")
    with pytest.raises(ValueError, match="非法工具名"):
        loader.load_from_code(
            "def f():\n    pass\n",
            module_name="ok",
            tool_name="bad name",
            function_name="f",
            description="d",
            params=[],
        )


# ============================================================
# ModuleHotReloader — load / reload / unload / get / list
# ============================================================

def test_hot_reloader_load_and_get(tmp_workspace):
    """load_module + get_module"""
    py_file = tmp_workspace / "mod1.py"
    py_file.write_text("VALUE = 1\ndef get():\n    return VALUE\n", encoding="utf-8")

    reloader = ModuleHotReloader()
    mod = reloader.load_module(py_file, "hotmod1")
    assert mod is not None
    assert mod.get() == 1
    assert reloader.get_module("hotmod1") is mod
    assert reloader.get_module("not_loaded") is None


def test_hot_reloader_list_loaded(tmp_workspace):
    """list_loaded 返回已加载模块名"""
    f1 = tmp_workspace / "a.py"
    f1.write_text("x = 1\n", encoding="utf-8")
    f2 = tmp_workspace / "b.py"
    f2.write_text("y = 2\n", encoding="utf-8")

    reloader = ModuleHotReloader()
    assert reloader.list_loaded() == []
    reloader.load_module(f1, "mod_a")
    reloader.load_module(f2, "mod_b")
    assert reloader.list_loaded() == ["mod_a", "mod_b"]


def test_hot_reloader_reload(tmp_workspace):
    """reload_module 拿到文件最新内容"""
    py_file = tmp_workspace / "changing.py"
    py_file.write_text("VALUE = 1\n", encoding="utf-8")

    reloader = ModuleHotReloader()
    mod = reloader.load_module(py_file, "hotmod_r")
    assert mod.VALUE == 1

    # 修改文件
    py_file.write_text("VALUE = 99\n", encoding="utf-8")
    reloaded = reloader.reload_module("hotmod_r")
    assert reloaded.VALUE == 99


def test_hot_reloader_unload(tmp_workspace):
    """unload_module 从 sys.modules 移除"""
    py_file = tmp_workspace / "tmp_mod.py"
    py_file.write_text("z = 1\n", encoding="utf-8")

    reloader = ModuleHotReloader()
    reloader.load_module(py_file, "hotmod_u")
    assert "hotmod_u" in sys.modules

    assert reloader.unload_module("hotmod_u") is True
    assert "hotmod_u" not in sys.modules
    assert reloader.get_module("hotmod_u") is None
    assert reloader.list_loaded() == []

    # 再次 unload 返回 False
    assert reloader.unload_module("hotmod_u") is False


def test_hot_reloader_reload_not_loaded_raises(tmp_workspace):
    """reload 未加载的模块抛 KeyError"""
    reloader = ModuleHotReloader()
    with pytest.raises(KeyError):
        reloader.reload_module("never_loaded")


def test_hot_reloader_invalid_module_name(tmp_workspace):
    """非法模块名抛 ValueError"""
    py_file = tmp_workspace / "m.py"
    py_file.write_text("x = 1\n", encoding="utf-8")
    reloader = ModuleHotReloader()
    with pytest.raises(ValueError, match="非法模块名"):
        reloader.load_module(py_file, "123bad")


def test_hot_reloader_file_not_exist(tmp_workspace):
    """文件不存在抛 FileNotFoundError"""
    reloader = ModuleHotReloader()
    with pytest.raises(FileNotFoundError):
        reloader.load_module(tmp_workspace / "nope.py", "ok_name")


# ============================================================
# EnhancedSkillLoader — 普通 skill md（无 frontmatter）
# ============================================================

def test_enhanced_loader_plain_skill_with_agent(tmp_workspace):
    """普通 skill md（无 frontmatter）走 prompt 注入（带 agent）"""
    skills_dir = tmp_workspace / "skills"
    skills_dir.mkdir()
    skill_md = skills_dir / "shell.md"
    skill_md.write_text(
        "# Shell Skill\n\n- 触发词: shell\n\n使用 shell 工具执行命令。\n",
        encoding="utf-8",
    )

    reg = ToolRegistry()
    agent = _FakeAgent()
    loader = EnhancedSkillLoader(reg, skills_dir=skills_dir, agent=agent)

    ok = loader.load_skill(skill_md)
    assert ok is True
    # agent.add_skill 被调用，system_prompt 被更新
    assert "Shell Skill" in agent.system_prompt
    assert "shell 工具执行命令" in agent.system_prompt
    assert str(skill_md) in loader.loaded_skills


def test_enhanced_loader_plain_skill_without_agent(tmp_workspace):
    """普通 skill md（无 frontmatter）走 prompt 注入（无 agent，累加到 injected_prompts）"""
    skills_dir = tmp_workspace / "skills"
    skills_dir.mkdir()
    skill_md = skills_dir / "note.md"
    skill_md.write_text(
        "# Note Skill\n\n这是笔记技能。\n", encoding="utf-8"
    )

    reg = ToolRegistry()
    loader = EnhancedSkillLoader(reg, skills_dir=skills_dir)

    ok = loader.load_skill(skill_md)
    assert ok is True
    assert len(loader.injected_prompts) == 1
    assert "Note Skill" in loader.injected_prompts[0]


# ============================================================
# EnhancedSkillLoader — 增强 skill md（有 frontmatter + code）
# ============================================================

def test_enhanced_loader_frontmatter_loads_real_tool(tmp_workspace):
    """增强 skill md（有 frontmatter + code）加载成真实工具"""
    skills_dir = tmp_workspace / "skills"
    skills_dir.mkdir()

    # 创建 code 文件
    code_file = skills_dir / "weather_tool.py"
    code_file.write_text(
        "def fetch_weather(city):\n"
        "    return f'{city}: 晴 25°C'\n",
        encoding="utf-8",
    )

    # 创建增强 skill md
    skill_md = skills_dir / "weather.md"
    skill_md.write_text(
        "---\n"
        "name: weather_query\n"
        "description: 查询天气\n"
        "code: weather_tool.py\n"
        "entry: fetch_weather\n"
        "params:\n"
        "  - name: city\n"
        "    type: string\n"
        "    required: true\n"
        "---\n"
        "这是一个天气查询技能，调用 weather_query 工具获取天气。\n",
        encoding="utf-8",
    )

    reg = ToolRegistry()
    loader = EnhancedSkillLoader(reg, skills_dir=skills_dir)

    ok = loader.load_skill(skill_md)
    assert ok is True
    # 工具应被注册
    assert reg.has("weather_query")
    result = reg.call("weather_query", city="上海")
    assert result.error is False
    assert "上海" in result.content
    assert "25°C" in result.content
    # 工具描述应来自 frontmatter
    assert reg.get_description("weather_query") == "查询天气"


def test_enhanced_loader_frontmatter_with_file_field(tmp_workspace):
    """frontmatter 的 file 字段也能作为 code 来源"""
    skills_dir = tmp_workspace / "skills"
    skills_dir.mkdir()

    code_file = skills_dir / "calc.py"
    code_file.write_text("def double(x):\n    return str(int(x) * 2)\n", encoding="utf-8")

    skill_md = skills_dir / "calc.md"
    skill_md.write_text(
        "---\n"
        "name: doubler\n"
        "description: 翻倍\n"
        "file: calc.py\n"
        "entry: double\n"
        "params:\n"
        "  - name: x\n"
        "    type: string\n"
        "---\n"
        "翻倍工具。\n",
        encoding="utf-8",
    )

    reg = ToolRegistry()
    loader = EnhancedSkillLoader(reg, skills_dir=skills_dir)
    ok = loader.load_skill(skill_md)
    assert ok is True
    assert reg.has("doubler")
    result = reg.call("doubler", x="21")
    assert "42" in result.content


def test_enhanced_loader_frontmatter_no_code_falls_back_to_prompt(tmp_workspace):
    """frontmatter 没有 code/entry 时走 prompt 注入"""
    skills_dir = tmp_workspace / "skills"
    skills_dir.mkdir()
    skill_md = skills_dir / "info.md"
    skill_md.write_text(
        "---\n"
        "name: info_skill\n"
        "description: 信息技能\n"
        "---\n"
        "这是说明正文，给 LLM 看。\n",
        encoding="utf-8",
    )

    reg = ToolRegistry()
    loader = EnhancedSkillLoader(reg, skills_dir=skills_dir)
    ok = loader.load_skill(skill_md)
    assert ok is True
    # 没有注册工具
    assert not reg.has("info_skill")
    # body 被注入到 prompt
    assert len(loader.injected_prompts) == 1
    assert "info_skill" in loader.injected_prompts[0]
    assert "说明正文" in loader.injected_prompts[0]


def test_enhanced_loader_frontmatter_missing_name(tmp_workspace):
    """frontmatter 缺 name 字段 → 返回 False"""
    skills_dir = tmp_workspace / "skills"
    skills_dir.mkdir()
    skill_md = skills_dir / "bad.md"
    skill_md.write_text(
        "---\n"
        "description: 没 name\n"
        "code: x.py\n"
        "entry: f\n"
        "---\n"
        "body\n",
        encoding="utf-8",
    )

    reg = ToolRegistry()
    loader = EnhancedSkillLoader(reg, skills_dir=skills_dir)
    ok = loader.load_skill(skill_md)
    assert ok is False
    assert not reg.has("x")


def test_enhanced_loader_frontmatter_invalid_name(tmp_workspace):
    """frontmatter name 非法 → 返回 False"""
    skills_dir = tmp_workspace / "skills"
    skills_dir.mkdir()
    skill_md = skills_dir / "bad2.md"
    skill_md.write_text(
        "---\n"
        "name: 123bad\n"
        "description: 非法 name\n"
        "---\n"
        "body\n",
        encoding="utf-8",
    )

    reg = ToolRegistry()
    loader = EnhancedSkillLoader(reg, skills_dir=skills_dir)
    ok = loader.load_skill(skill_md)
    assert ok is False


def test_enhanced_loader_frontmatter_malformed_yaml(tmp_workspace):
    """frontmatter YAML 格式错误 → 返回 False"""
    skills_dir = tmp_workspace / "skills"
    skills_dir.mkdir()
    skill_md = skills_dir / "broken.md"
    # 故意写无法解析的 YAML（未闭合的引号 + 错误缩进）
    skill_md.write_text(
        "---\n"
        "name: broken_skill\n"
        "code: \"[unclosed bracket\n"
        "  - bad: : :\n"
        "---\n"
        "body\n",
        encoding="utf-8",
    )

    reg = ToolRegistry()
    loader = EnhancedSkillLoader(reg, skills_dir=skills_dir)
    ok = loader.load_skill(skill_md)
    # YAML 解析失败 → frontmatter 为 {} → name 缺失 → False
    assert ok is False


def test_enhanced_loader_code_path_traversal_rejected(tmp_workspace):
    """增强 skill 的 code 路径穿越 skills_dir → 返回 False"""
    skills_dir = tmp_workspace / "skills"
    skills_dir.mkdir()

    # 在 skills_dir 外放一个 code 文件
    outside = tmp_workspace / "outside_tool.py"
    outside.write_text("def f():\n    return 'evil'\n", encoding="utf-8")

    skill_md = skills_dir / "evil.md"
    skill_md.write_text(
        "---\n"
        "name: evil_tool\n"
        "description: evil\n"
        "code: ../outside_tool.py\n"
        "entry: f\n"
        "---\n"
        "body\n",
        encoding="utf-8",
    )

    reg = ToolRegistry()
    loader = EnhancedSkillLoader(reg, skills_dir=skills_dir)
    ok = loader.load_skill(skill_md)
    assert ok is False
    assert not reg.has("evil_tool")


def test_enhanced_loader_code_file_missing(tmp_workspace):
    """frontmatter 指向的 code 文件不存在 → 返回 False"""
    skills_dir = tmp_workspace / "skills"
    skills_dir.mkdir()
    skill_md = skills_dir / "miss.md"
    skill_md.write_text(
        "---\n"
        "name: miss_tool\n"
        "description: miss\n"
        "code: not_exist.py\n"
        "entry: f\n"
        "---\n"
        "body\n",
        encoding="utf-8",
    )

    reg = ToolRegistry()
    loader = EnhancedSkillLoader(reg, skills_dir=skills_dir)
    ok = loader.load_skill(skill_md)
    assert ok is False
    assert not reg.has("miss_tool")


def test_enhanced_loader_load_all_skills(tmp_workspace):
    """load_all_skills 扫描 skills_dir 加载所有 .md"""
    skills_dir = tmp_workspace / "skills"
    skills_dir.mkdir()

    # 普通 skill
    (skills_dir / "plain.md").write_text("# Plain\n\n正文\n", encoding="utf-8")

    # 增强 skill
    (skills_dir / "echo.py").write_text(
        "def echo(msg):\n    return msg\n", encoding="utf-8"
    )
    (skills_dir / "echo.md").write_text(
        "---\n"
        "name: echo_tool\n"
        "description: 回显\n"
        "code: echo.py\n"
        "entry: echo\n"
        "params:\n"
        "  - name: msg\n"
        "    type: string\n"
        "---\n"
        "回显工具。\n",
        encoding="utf-8",
    )

    reg = ToolRegistry()
    loader = EnhancedSkillLoader(reg, skills_dir=skills_dir)
    loaded = loader.load_all_skills()
    assert len(loaded) == 2
    assert reg.has("echo_tool")
    assert len(loader.injected_prompts) >= 1  # plain.md 被注入


def test_enhanced_loader_skill_not_exist(tmp_workspace):
    """skill 文件不存在 → 返回 False"""
    reg = ToolRegistry()
    loader = EnhancedSkillLoader(reg, skills_dir=tmp_workspace / "skills")
    assert loader.load_skill(tmp_workspace / "skills" / "nope.md") is False
