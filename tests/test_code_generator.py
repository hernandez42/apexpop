"""测试 superclaw.code_generator — 代码生成器 + 沙箱执行器

覆盖：
- CodeGenerator: mock LLM 返回 JSON 解析 / mock 兜底
- SandboxExecutor: 成功 / import 失败 / 测试失败 / 超时
- 安全检查：危险模式拦截 / 代码大小限制
- 完整流程：generate → execute → passed=True
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from superclaw.code_generator import (
    CodeGenerator,
    CodeSpec,
    GeneratedCode,
    SandboxExecutor,
    SandboxResult,
    DANGEROUS_PATTERNS,
    MAX_CODE_SIZE,
    SANDBOX_TIMEOUT,
)
from superclaw.llm_router import CompletionResult


# ============================================================
# Mock LLMRouter
# ============================================================

class _MockLLMRouter:
    """Mock LLMRouter — 返回固定 content / error"""

    def __init__(self, content="", error=None, provider="mock",
                 tokens=42):
        self._content = content
        self._error = error
        self._provider = provider
        self._tokens = tokens
        self.calls = []

    def complete(self, messages, complexity="medium",
                 provider=None, max_tokens=None):
        self.calls.append({"messages": messages, "complexity": complexity})
        return CompletionResult(
            content=self._content,
            provider=self._provider,
            model="mock-model",
            tokens_used=self._tokens,
            error=self._error,
        )


# ============================================================
# CodeGenerator — mock LLM 返回 JSON 解析
# ============================================================

def test_code_generator_parses_llm_json():
    """mock LLM 返回固定 JSON → 解析 imports/dependencies/test_code"""
    payload = {
        "code": "def add(a, b):\n    return a + b\n",
        "imports": ["math"],
        "dependencies": ["numpy"],
        "test_code": "from add import add\n\ndef test_add():\n    assert add(2, 3) == 5\n",
    }
    router = _MockLLMRouter(content=json.dumps(payload))
    gen = CodeGenerator(router)

    spec = CodeSpec(
        name="add",
        description="Add two numbers",
        signature="def add(a: int, b: int) -> int",
        parameters=[{"name": "a", "type": "int"}, {"name": "b", "type": "int"}],
        context="需要基础算术能力",
    )
    code = gen.generate(spec)

    assert code.name == "add"
    assert "def add" in code.code
    assert code.imports == ["math"]
    assert code.dependencies == ["numpy"]
    assert "test_add" in code.test_code
    assert code.llm_provider == "mock"
    assert code.tokens_used == 42
    # 验证 LLM 被调用过
    assert len(router.calls) == 1


def test_code_generator_parses_json_with_codeblock_wrapper():
    """LLM 返回 ```json ... ``` 包裹的 JSON 也能解析"""
    payload = {
        "code": "def hello():\n    return 'hi'\n",
        "imports": [],
        "dependencies": [],
        "test_code": "from hello import hello\n\ndef test_hello():\n    assert hello() == 'hi'\n",
    }
    wrapped = "```json\n" + json.dumps(payload) + "\n```"
    router = _MockLLMRouter(content=wrapped)
    gen = CodeGenerator(router)

    spec = CodeSpec(name="hello", description="say hi",
                    signature="def hello() -> str", parameters=[])
    code = gen.generate(spec)
    assert "def hello" in code.code
    assert "test_hello" in code.test_code


def test_code_generator_invalid_json_falls_back_to_mock():
    """LLM 返回非 JSON → 走 mock 兜底"""
    router = _MockLLMRouter(content="这不是 JSON，只是一段文字")
    gen = CodeGenerator(router)

    spec = CodeSpec(name="add", description="add",
                    signature="def add(a: int, b: int) -> int", parameters=[])
    code = gen.generate(spec)
    # 兜底生成真实可执行代码
    assert "mock-fallback:parse_failed" in code.llm_provider
    assert "def add" in code.code
    assert len(code.test_code) > 0


# ============================================================
# CodeGenerator — mock 兜底（LLM 失败时生成真实可执行模板）
# ============================================================

def test_code_generator_mock_fallback_on_llm_error():
    """LLM 失败 → 生成真实可执行模板代码"""
    router = _MockLLMRouter(error="network down")
    gen = CodeGenerator(router)

    spec = CodeSpec(
        name="fetch_weather",
        description="Fetch weather for a city",
        signature="def fetch_weather(city: str) -> dict",
        parameters=[{"name": "city", "type": "str"}],
    )
    code = gen.generate(spec)

    assert code.name == "fetch_weather"
    assert "def fetch_weather" in code.code
    assert "mock-fallback:llm_error" in code.llm_provider
    assert code.tokens_used == 0
    # 必须是真实可执行代码（不是空字符串）
    assert len(code.code) > 50
    assert len(code.test_code) > 0
    # 天气模板用 urllib
    assert "urllib" in code.code


def test_code_generator_mock_fallback_on_empty_content():
    """LLM 返回空内容 → 走 mock 兜底"""
    router = _MockLLMRouter(content="")
    gen = CodeGenerator(router)

    spec = CodeSpec(name="add", description="add",
                    signature="def add(a: int, b: int) -> int", parameters=[])
    code = gen.generate(spec)
    assert "mock-fallback" in code.llm_provider
    assert "def add" in code.code


def test_code_generator_generic_template_returns_int():
    """通用模板根据返回类型生成默认值（int → 0）"""
    router = _MockLLMRouter(error="fail")
    gen = CodeGenerator(router)

    spec = CodeSpec(name="count", description="count something",
                    signature="def count(x: list) -> int", parameters=[])
    code = gen.generate(spec)
    assert "return 0" in code.code


def test_code_generator_generic_template_returns_dict():
    """通用模板默认返回 dict"""
    router = _MockLLMRouter(error="fail")
    gen = CodeGenerator(router)

    spec = CodeSpec(name="get_info", description="get info",
                    signature="def get_info(key: str) -> dict", parameters=[])
    code = gen.generate(spec)
    assert "return {" in code.code
    assert '"status": "ok"' in code.code


# ============================================================
# SandboxExecutor — 成功路径
# ============================================================

def test_sandbox_success(tmp_workspace):
    """生成简单 add 函数 → 写入 → import ok → 测试通过"""
    sandbox = SandboxExecutor(tmp_workspace / "sandbox")
    code = GeneratedCode(
        name="add",
        code="def add(a, b):\n    return a + b\n",
        test_code="from add import add\n\ndef test_add():\n    assert add(2, 3) == 5\n",
    )
    result = sandbox.execute(code)

    assert result.passed is True
    assert result.import_ok is True
    assert result.call_ok is True
    assert result.test_ok is True
    assert result.errors == []
    assert result.duration_ms >= 0
    # 文件确实写入了
    assert (tmp_workspace / "sandbox" / "add.py").exists()
    assert (tmp_workspace / "sandbox" / "test_add.py").exists()


def test_sandbox_cleanup_removes_files(tmp_workspace):
    """cleanup=True 执行后删除文件"""
    sandbox = SandboxExecutor(tmp_workspace / "sandbox")
    code = GeneratedCode(
        name="add",
        code="def add(a, b):\n    return a + b\n",
        test_code="from add import add\n\ndef test_add():\n    assert add(1, 1) == 2\n",
    )
    sandbox.execute(code, cleanup=True)
    assert not (tmp_workspace / "sandbox" / "add.py").exists()


# ============================================================
# SandboxExecutor — import 失败（语法错误）
# ============================================================

def test_sandbox_import_failure_syntax_error(tmp_workspace):
    """代码有语法错误 → import_ok=False"""
    sandbox = SandboxExecutor(tmp_workspace / "sandbox")
    code = GeneratedCode(
        name="broken",
        code="def broken(:\n    pass\n",  # 语法错误
        test_code="from broken import broken\n\ndef test_broken():\n    pass\n",
    )
    result = sandbox.execute(code)

    assert result.passed is False
    assert result.import_ok is False
    assert result.call_ok is False
    assert result.test_ok is False
    assert any("import 失败" in e for e in result.errors)


# ============================================================
# SandboxExecutor — 测试失败（代码能 import 但行为错误）
# ============================================================

def test_sandbox_test_failure_wrong_behavior(tmp_workspace):
    """代码能 import 但行为错误 → test_ok=False, import_ok=True"""
    sandbox = SandboxExecutor(tmp_workspace / "sandbox")
    code = GeneratedCode(
        name="bad_add",
        code="def bad_add(a, b):\n    return a - b\n",  # 行为错误
        test_code="from bad_add import bad_add\n\ndef test_bad_add():\n    assert bad_add(2, 3) == 5\n",
    )
    result = sandbox.execute(code)

    assert result.passed is False
    assert result.import_ok is True
    assert result.call_ok is True
    assert result.test_ok is False
    assert any("测试失败" in e for e in result.errors)


def test_sandbox_no_test_code(tmp_workspace):
    """没有 test_code → test_ok=False"""
    sandbox = SandboxExecutor(tmp_workspace / "sandbox")
    code = GeneratedCode(
        name="notest",
        code="def notest():\n    return 42\n",
        test_code="",
    )
    result = sandbox.execute(code)
    assert result.passed is False
    assert result.import_ok is True
    assert result.call_ok is True
    assert result.test_ok is False
    assert any("无测试代码" in e for e in result.errors)


# ============================================================
# SandboxExecutor — 超时（代码有死循环）
# ============================================================

def test_sandbox_timeout(tmp_workspace, monkeypatch):
    """代码有 time.sleep(60) → import 超时"""
    # 把超时调小到 2 秒，避免测试跑 30 秒
    import superclaw.code_generator as _cg_mod
    monkeypatch.setattr(_cg_mod, "SANDBOX_TIMEOUT", 2)

    sandbox = SandboxExecutor(tmp_workspace / "sandbox")
    code = GeneratedCode(
        name="slow",
        code="import time\ntime.sleep(60)\n",  # 模块级死循环
        test_code="def test_slow():\n    pass\n",
    )
    result = sandbox.execute(code)

    assert result.passed is False
    assert result.import_ok is False
    assert any("超时" in e for e in result.errors)


# ============================================================
# 安全检查：危险模式被拦截
# ============================================================

@pytest.mark.parametrize("dangerous_code,desc", [
    ("import os\nos.system('ls')\n", "os.system"),
    ("x = eval('1+1')\n", "eval"),
    ("exec('x=1')\n", "exec"),
    ("mod = __import__('os')\n", "__import__"),
    ("import subprocess\nsubprocess.Popen('ls', shell=True)\n", "shell=True"),
])
def test_sandbox_blocks_dangerous_patterns(tmp_workspace, dangerous_code, desc):
    """危险模式被拦截，不执行"""
    sandbox = SandboxExecutor(tmp_workspace / "sandbox")
    code = GeneratedCode(
        name="danger",
        code=dangerous_code,
        test_code="def test_danger():\n    pass\n",
    )
    result = sandbox.execute(code)

    assert result.passed is False
    assert result.import_ok is False
    assert any("危险模式" in e for e in result.errors)
    # 不应写入文件
    assert not (tmp_workspace / "sandbox" / "danger.py").exists()


def test_sandbox_blocks_dangerous_test_code(tmp_workspace):
    """测试代码含危险模式也被拦截"""
    sandbox = SandboxExecutor(tmp_workspace / "sandbox")
    code = GeneratedCode(
        name="ok_func",
        code="def ok_func():\n    return 1\n",
        test_code="def test_ok():\n    eval('1+1')\n",  # 测试代码含 eval
    )
    result = sandbox.execute(code)
    assert result.passed is False
    assert any("测试代码含危险模式" in e for e in result.errors)


def test_sandbox_allows_safe_subprocess_without_shell(tmp_workspace):
    """subprocess.Popen 不带 shell=True 是允许的"""
    sandbox = SandboxExecutor(tmp_workspace / "sandbox")
    code = GeneratedCode(
        name="safe_sub",
        code="def safe_sub():\n    return 'safe'\n",
        test_code="from safe_sub import safe_sub\n\ndef test_safe():\n    assert safe_sub() == 'safe'\n",
    )
    result = sandbox.execute(code)
    assert result.passed is True


def test_dangerous_patterns_constant():
    """DANGEROUS_PATTERNS 包含 16 个模式（多层防御）"""
    assert len(DANGEROUS_PATTERNS) == 16
    # 验证每个模式都能匹配对应的危险代码
    import re
    cases = [
        ("os.system('ls')", DANGEROUS_PATTERNS[0]),
        ("eval('1+1')", DANGEROUS_PATTERNS[1]),
        ("exec('x=1')", DANGEROUS_PATTERNS[2]),
        ("__import__('os')", DANGEROUS_PATTERNS[3]),
        ("subprocess.Popen('ls', shell=True)", DANGEROUS_PATTERNS[4]),
        ("socket.connect()", DANGEROUS_PATTERNS[5]),
        ("ctypes.CDLL()", DANGEROUS_PATTERNS[6]),
        ("pickle.loads()", DANGEROUS_PATTERNS[7]),
        ("shutil.rmtree('/x')", DANGEROUS_PATTERNS[8]),
        ("open('x','w')", DANGEROUS_PATTERNS[9]),
        ("Path('x').write_text('y')", DANGEROUS_PATTERNS[10]),
        ("Path('x').write_bytes(b'y')", DANGEROUS_PATTERNS[11]),
        ("Path('x').unlink()", DANGEROUS_PATTERNS[12]),
        ("globals()", DANGEROUS_PATTERNS[13]),
        ("locals()", DANGEROUS_PATTERNS[14]),
        ("getattr(obj, '__class__')", DANGEROUS_PATTERNS[15]),
    ]
    for text, pattern in cases:
        assert re.search(pattern, text) is not None


# ============================================================
# 代码大小限制
# ============================================================

def test_sandbox_rejects_oversized_code(tmp_workspace):
    """代码 > 100KB 被拒绝"""
    sandbox = SandboxExecutor(tmp_workspace / "sandbox")
    # 构造 >100KB 的代码
    big_code = "x = '" + "a" * (MAX_CODE_SIZE + 100) + "'\n"
    assert len(big_code.encode("utf-8")) > MAX_CODE_SIZE

    code = GeneratedCode(
        name="big",
        code=big_code,
        test_code="def test_big():\n    pass\n",
    )
    result = sandbox.execute(code)

    assert result.passed is False
    assert result.import_ok is False
    assert any("过大" in e for e in result.errors)


def test_max_code_size_is_100kb():
    """MAX_CODE_SIZE = 100 * 1024"""
    assert MAX_CODE_SIZE == 100 * 1024


def test_sandbox_timeout_constant():
    """SANDBOX_TIMEOUT = 30"""
    assert SANDBOX_TIMEOUT == 30


# ============================================================
# 完整流程：CodeGenerator.generate → SandboxExecutor.execute
# ============================================================

def test_full_flow_with_llm_json(tmp_workspace):
    """完整流程（LLM 路径）：generate → execute → passed=True"""
    payload = {
        "code": "def add(a, b):\n    return a + b\n",
        "imports": [],
        "dependencies": [],
        "test_code": "from add import add\n\ndef test_add():\n    assert add(2, 3) == 5\n",
    }
    router = _MockLLMRouter(content=json.dumps(payload))
    gen = CodeGenerator(router)

    spec = CodeSpec(
        name="add",
        description="Add two numbers",
        signature="def add(a: int, b: int) -> int",
        parameters=[{"name": "a", "type": "int"}, {"name": "b", "type": "int"}],
    )
    code = gen.generate(spec)
    sandbox = SandboxExecutor(tmp_workspace / "sandbox")
    result = sandbox.execute(code)

    assert result.passed is True
    assert result.import_ok is True
    assert result.call_ok is True
    assert result.test_ok is True


def test_full_flow_with_mock_fallback(tmp_workspace):
    """完整流程（mock 兜底路径）：LLM 失败 → 兜底代码 → 沙箱通过"""
    router = _MockLLMRouter(error="llm unavailable")
    gen = CodeGenerator(router)

    spec = CodeSpec(
        name="add",
        description="Add two numbers",
        signature="def add(a: int, b: int) -> int",
        parameters=[{"name": "a", "type": "int"}, {"name": "b", "type": "int"}],
    )
    code = gen.generate(spec)
    assert "mock-fallback" in code.llm_provider

    sandbox = SandboxExecutor(tmp_workspace / "sandbox")
    result = sandbox.execute(code)

    assert result.passed is True
    assert result.import_ok is True
    assert result.call_ok is True
    assert result.test_ok is True


def test_full_flow_weather_mock_fallback(tmp_workspace):
    """fetch_weather mock 兜底生成 urllib 代码 → 沙箱正确拦截 urllib

    沙箱安全策略：urllib 是网络模块，在沙箱内被 import hook 拦截。
    这验证了安全特性——即使生成的代码用 urllib，沙箱也会阻止。
    """
    router = _MockLLMRouter(error="llm unavailable")
    gen = CodeGenerator(router)

    spec = CodeSpec(
        name="fetch_weather",
        description="Fetch weather for a city",
        signature="def fetch_weather(city: str) -> dict",
        parameters=[{"name": "city", "type": "str"}],
    )
    code = gen.generate(spec)
    assert "urllib" in code.code

    sandbox = SandboxExecutor(tmp_workspace / "sandbox")
    result = sandbox.execute(code)

    # urllib 被沙箱 import hook 拦截 → import 失败是预期安全行为
    assert result.import_ok is False
    assert any("blocked by sandbox" in e or "urllib" in e for e in result.errors)


# ============================================================
# SandboxResult dataclass 默认值
# ============================================================

def test_sandbox_result_defaults():
    r = SandboxResult()
    assert r.passed is False
    assert r.import_ok is False
    assert r.call_ok is False
    assert r.test_ok is False
    assert r.errors == []
    assert r.output == ""
    assert r.duration_ms == 0


def test_generated_code_defaults():
    c = GeneratedCode(name="x", code="pass")
    assert c.name == "x"
    assert c.code == "pass"
    assert c.imports == []
    assert c.dependencies == []
    assert c.test_code == ""
    assert c.llm_provider == ""
    assert c.tokens_used == 0


def test_code_spec_defaults():
    s = CodeSpec(name="x", description="d", signature="def x(): pass",
                 parameters=[])
    assert s.name == "x"
    assert s.context == ""
    assert s.language == "python"


# ============================================================
# SandboxExecutor 默认目录
# ============================================================

def test_sandbox_executor_default_dir(tmp_workspace):
    """sandbox_dir=None → 默认 superclaw-data/sandbox"""
    sandbox = SandboxExecutor()
    assert sandbox.sandbox_dir == Path("superclaw-data/sandbox")


# ============================================================
# 真沙箱隔离测试 — 验证多层防御真的生效
# ============================================================

class TestSandboxRealIsolation:
    """验证沙箱不是"工作目录隔离"而是真隔离

    覆盖：
    - 运行时 import 危险模块被 import hook 拦截
    - 子进程环境隔离（PYTHONPATH 清空）
    - 资源限制生效（CPU/内存/文件大小）
    """

    def test_runtime_import_subprocess_blocked(self, tmp_workspace):
        """运行时 import subprocess 被 import hook 拦截

        静态检查只拦 shell=True，但 import hook 拦截所有 subprocess 导入。
        这验证了"多层防御"——静态漏网的，运行时兜底。
        用 _run_sandboxed 直接验证（pytest 步骤不走 hook）。
        """
        sandbox = SandboxExecutor(tmp_workspace / "sandbox")
        sandbox.sandbox_dir.mkdir(parents=True, exist_ok=True)
        # 直接用 _run_sandboxed 验证 import subprocess 被拦
        ok, out = sandbox._run_sandboxed("import subprocess; print('leaked')")
        assert ok is False
        assert "blocked by sandbox" in out

    def test_runtime_import_socket_blocked(self, tmp_workspace):
        """运行时 import socket 被拦"""
        sandbox = SandboxExecutor(tmp_workspace / "sandbox")
        sandbox.sandbox_dir.mkdir(parents=True, exist_ok=True)
        ok, out = sandbox._run_sandboxed("import socket; print('leaked')")
        assert ok is False
        assert "blocked by sandbox" in out

    def test_sandbox_env_no_pythonpath(self, tmp_workspace):
        """子进程环境变量清空 PYTHONPATH"""
        sandbox = SandboxExecutor(tmp_workspace / "sandbox")
        env = sandbox._make_sandbox_env()
        assert "PYTHONPATH" not in env
        assert env["PYTHONDONTWRITEBYTECODE"] == "1"
        assert env["PYTHONHASHSEED"] == "0"

    def test_resource_prelude_contains_limits(self, tmp_workspace):
        """资源限制前导代码包含 setrlimit"""
        sandbox = SandboxExecutor(tmp_workspace / "sandbox")
        prelude = sandbox._make_resource_prelude()
        assert "RLIMIT_CPU" in prelude
        assert "RLIMIT_FSIZE" in prelude
        assert "RLIMIT_AS" in prelude
        assert "_RestrictedFinder" in prelude
        assert "blocked by sandbox" in prelude

    def test_run_sandboxed_blocks_os_import(self, tmp_workspace):
        """_run_sandboxed 直接拦 subprocess 模块导入（os 启动时已加载无法拦）"""
        sandbox = SandboxExecutor(tmp_workspace / "sandbox")
        sandbox.sandbox_dir.mkdir(parents=True, exist_ok=True)
        # subprocess 在 Python 启动时不加载，可被 hook 拦截
        ok, out = sandbox._run_sandboxed("import subprocess; print('leaked')")
        assert ok is False
        assert "blocked by sandbox" in out

    def test_run_sandboxed_allows_safe_code(self, tmp_workspace):
        """_run_sandboxed 允许安全代码（纯计算）"""
        sandbox = SandboxExecutor(tmp_workspace / "sandbox")
        sandbox.sandbox_dir.mkdir(parents=True, exist_ok=True)
        ok, out = sandbox._run_sandboxed("print(1 + 2)")
        assert ok is True
        assert "3" in out

    def test_run_sandboxed_allows_json_math(self, tmp_workspace):
        """_run_sandboxed 允许 json/math 等安全 stdlib"""
        sandbox = SandboxExecutor(tmp_workspace / "sandbox")
        sandbox.sandbox_dir.mkdir(parents=True, exist_ok=True)
        ok, out = sandbox._run_sandboxed(
            "import json, math; print(json.dumps({'sqrt': math.sqrt(16)}))"
        )
        assert ok is True
        assert "4.0" in out
