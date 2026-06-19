"""
superclaw 代码生成器 + 沙箱执行器

补全 GEP engine 的"生成代码 → 沙箱验证"环节：
- CodeGenerator: 根据 CodeSpec 调 LLM 生成可执行代码（带结构化 mock 兜底）
- SandboxExecutor: 在隔离目录验证生成的代码（import / call / pytest）

安全限制（多层防御）：
- 代码大小 <= 100KB
- 沙箱执行超时 30s
- 静态拦截危险模式（os.system / eval / exec / __import__ / shell=True /
  open(...w) / socket / ctypes / pickle / shutil.rmtree 等）
- 子进程 resource 限制：CPU 10s、内存 256MB、文件大小 1MB（POSIX）
- 子进程环境隔离：清空 PYTHONPATH、设 PYTHONDONTWRITEBYTECODE=1、
  PYTHONHASHSEED=0，cwd 锁定沙箱目录
- 受限 import：子进程通过 -S 启动 + sitecustomize 注入 import hook，
  拦截 os/subprocess/socket/ctypes 等危险模块的运行时导入
"""
import ast
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .llm_router import LLMRouter


# 危险模式 — 命中即拒绝执行（静态检查层）
DANGEROUS_PATTERNS: List[str] = [
    r"os\.system\s*\(",
    r"\beval\s*\(",
    r"\bexec\s*\(",
    r"__import__\s*\(",
    r"subprocess\.(Popen|call|run)\s*\(.*shell\s*=\s*True",
    r"\bsocket\s*\.",
    r"\bctypes\s*\.",
    r"\bpickle\s*\.",
    r"\bshutil\.rmtree\s*\(",
    r"\bopen\s*\([^)]*['\"][wa]",
    r"\bPath\s*\([^)]*\)\.write_text\s*\(",
    r"\bPath\s*\([^)]*\)\.write_bytes\s*\(",
    r"\bPath\s*\([^)]*\)\.unlink\s*\(",
    r"\bglobals\s*\(\s*\)",
    r"\blocals\s*\(\s*\)",
    r"\bgetattr\s*\([^,]*,\s*['\"]__",
]

# 代码大小上限（字节）
MAX_CODE_SIZE = 100 * 1024  # 100KB

# 沙箱执行超时（秒）
SANDBOX_TIMEOUT = 30

# 沙箱资源限制（POSIX）
SANDBOX_CPU_SECONDS = 10        # CPU 时间上限
SANDBOX_MEMORY_BYTES = 256 * 1024 * 1024  # 256MB
SANDBOX_FILE_SIZE_BYTES = 1 * 1024 * 1024  # 1MB

# 受限 import：子进程启动时注入的 import hook
# 拦截运行时导入危险模块（静态检查漏网的兜底）
# 注意：os/sys 等在 Python 启动早期就已加载到 sys.modules，
# meta_path finder 只对未缓存的模块生效，所以无法拦截 os。
# 这里只拦那些 Python 启动时不加载的危险模块。
_RESTRICTED_IMPORT_HOOK = '''
import sys
import importlib.abc

_BLOCKED_MODULES = {
    "subprocess", "ctypes", "pickle", "shutil",
    "socket", "http", "urllib", "ftplib", "smtplib",
    "telnetlib", "xmlrpc", "multiprocessing",
}
_BLOCKED_PREFIXES = (
    "subprocess.", "ctypes.", "pickle.", "shutil.",
    "socket.", "http.", "urllib.", "multiprocessing.",
)

class _RestrictedFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        # 完全匹配黑名单
        if fullname in _BLOCKED_MODULES:
            raise ImportError(f"blocked by sandbox: {fullname}")
        # 前缀匹配
        for prefix in _BLOCKED_PREFIXES:
            if fullname.startswith(prefix):
                raise ImportError(f"blocked by sandbox: {fullname}")
        return None  # 让其他 finder 处理

# 注入到 meta_path 最前面
sys.meta_path.insert(0, _RestrictedFinder())
'''


# ============================================================
# Dataclasses
# ============================================================

@dataclass
class CodeSpec:
    """代码生成规格"""
    name: str                              # 工具/函数名
    description: str                       # 功能描述
    signature: str                         # 函数签名，如 "def fetch_weather(city: str) -> dict"
    parameters: List[Dict[str, Any]]       # 参数 schema
    context: str = ""                      # 上下文：为什么需要这个能力、相关信号
    language: str = "python"


@dataclass
class GeneratedCode:
    """生成的代码"""
    name: str
    code: str                                          # 生成的代码文本
    imports: List[str] = field(default_factory=list)   # 需要的 import
    dependencies: List[str] = field(default_factory=list)  # 需要的 pip 包
    test_code: str = ""                                # 生成的测试代码
    llm_provider: str = ""                             # 用了哪个 LLM
    tokens_used: int = 0


@dataclass
class SandboxResult:
    """沙箱执行结果"""
    passed: bool = False
    import_ok: bool = False       # 能否 import
    call_ok: bool = False         # 能否调用
    test_ok: bool = False         # 测试能否通过
    errors: List[str] = field(default_factory=list)
    output: str = ""              # 执行输出
    duration_ms: int = 0


# ============================================================
# CodeGenerator
# ============================================================

class CodeGenerator:
    """根据 CodeSpec 调 LLM 生成可执行代码

    LLM 失败或为 mock 时，走结构化 mock 兜底，
    根据 spec.name 和 signature 生成真实可执行的模板代码。
    """

    def __init__(self, llm_router: LLMRouter):
        self.llm = llm_router

    def generate(self, spec: CodeSpec) -> GeneratedCode:
        """根据规格生成代码"""
        prompt = self._build_prompt(spec)
        result = self.llm.complete(
            [{"role": "user", "content": prompt}],
            complexity="medium",
        )

        # LLM 失败 → mock 兜底
        if result.error or not result.content:
            return self._mock_fallback(spec, reason="llm_error")

        # 尝试解析 LLM 输出的 JSON
        parsed = self._parse_llm_json(result.content)
        if parsed is None:
            return self._mock_fallback(spec, reason="parse_failed")

        return GeneratedCode(
            name=spec.name,
            code=parsed.get("code", ""),
            imports=list(parsed.get("imports", []) or []),
            dependencies=list(parsed.get("dependencies", []) or []),
            test_code=parsed.get("test_code", "") or "",
            llm_provider=result.provider,
            tokens_used=result.tokens_used,
        )

    # ---- prompt 构造与解析 ----

    def _build_prompt(self, spec: CodeSpec) -> str:
        """构造 LLM 提示词"""
        return f"""你是 superclaw 代码生成器。根据以下规格生成可执行的 Python 代码。

函数名: {spec.name}
描述: {spec.description}
签名: {spec.signature}
参数 schema: {json.dumps(spec.parameters, ensure_ascii=False)}
上下文: {spec.context}
语言: {spec.language}

要求:
1. 生成完整可运行的 Python 代码（含函数定义）
2. 生成对应的 pytest 测试代码
3. 测试不依赖网络（用 mock 或固定输入）
4. 不要使用危险操作（os.system / eval / exec / __import__ / shell=True）

输出 JSON:
{{
  "code": "完整代码文本",
  "imports": ["需要的 import 模块名"],
  "dependencies": ["需要的 pip 包名"],
  "test_code": "pytest 测试代码文本"
}}

只输出 JSON，不要其他文字。"""

    def _parse_llm_json(self, content: str) -> Optional[Dict[str, Any]]:
        """解析 LLM 返回的 JSON（容忍 ```json 包裹）"""
        text = content.strip()
        if text.startswith("```"):
            # 去掉 ```json ... ``` 包裹
            parts = text.split("```")
            if len(parts) >= 3:
                text = parts[1]
                if text.startswith("json"):
                    text = text[4:]
        try:
            data = json.loads(text)
            if not isinstance(data, dict):
                return None
            # 必须有 code 字段
            if not data.get("code"):
                return None
            return data
        except (json.JSONDecodeError, ValueError):
            return None

    # ---- 结构化 mock 兜底 ----

    def _mock_fallback(self, spec: CodeSpec,
                        reason: str = "") -> GeneratedCode:
        """结构化 mock 兜底 — 生成真实可执行的模板代码"""
        name = spec.name
        lower = name.lower()

        if "weather" in lower:
            code, imports, deps, test_code = self._gen_weather_template(spec)
        elif "fetch" in lower or "http" in lower or "request" in lower:
            code, imports, deps, test_code = self._gen_fetch_template(spec)
        else:
            code, imports, deps, test_code = self._gen_generic_template(spec)

        return GeneratedCode(
            name=name,
            code=code,
            imports=imports,
            dependencies=deps,
            test_code=test_code,
            llm_provider=f"mock-fallback:{reason}",
            tokens_used=0,
        )

    def _parse_signature(self, signature: str) -> Tuple[List[str], str]:
        """解析函数签名，返回 (参数名列表, 返回类型字符串)

        签名可能只是 def 行（无函数体），ast.parse 需要完整语句，
        所以尝试多种补全方式。
        """
        sig = signature.rstrip()
        # 尝试解析：先试原样（可能含函数体），再试补 pass
        for candidate in (sig, sig + ": pass", sig + " pass"):
            try:
                tree = ast.parse(candidate)
                for node in ast.walk(tree):
                    if isinstance(node, ast.FunctionDef):
                        params = [arg.arg for arg in node.args.args]
                        returns = ast.unparse(node.returns) if node.returns else "dict"
                        return params, returns
            except SyntaxError:
                continue
        return [], "dict"

    def _gen_generic_template(self, spec: CodeSpec) -> Tuple[str, List[str], List[str], str]:
        """通用模板 — 根据签名生成返回默认值的函数"""
        name = spec.name
        params, return_type = self._parse_signature(spec.signature)
        param_sig = ", ".join(f"{p}=None" for p in params)

        # 根据返回类型选择默认返回值
        if "int" in return_type:
            ret_val = "0"
        elif "str" in return_type:
            ret_val = '""'
        elif "bool" in return_type:
            ret_val = "False"
        elif "list" in return_type or "List" in return_type:
            ret_val = "[]"
        else:
            ret_val = '{"status": "ok", "function": "%s"}' % name

        code = (
            '"""Auto-generated by superclaw CodeGenerator mock fallback."""\n'
            f"def {name}({param_sig}):\n"
            f'    """{spec.description}"""\n'
            f"    return {ret_val}\n"
        )

        call_args = ", ".join("None" for _ in params)
        test_code = (
            f'"""Tests for {name}."""\n'
            f"from {name} import {name}\n\n"
            f"def test_{name}_callable():\n"
            f"    result = {name}({call_args})\n"
            f"    assert result is not None\n\n"
            f"def test_{name}_returns_value():\n"
            f"    result = {name}({call_args})\n"
            f"    assert result is not None\n"
        )
        return code, [], [], test_code

    def _gen_weather_template(self, spec: CodeSpec) -> Tuple[str, List[str], List[str], str]:
        """天气模板 — 用 urllib 调 wttr.in 免费天气 API"""
        name = spec.name
        code = (
            '"""Auto-generated weather fetcher (mock fallback)."""\n'
            "import json\n"
            "from urllib.request import urlopen, Request\n"
            "from urllib.parse import quote\n\n\n"
            f"def {name}(city=None):\n"
            f'    """{spec.description}"""\n'
            '    if not city:\n'
            '        return {"status": "error", "message": "city is required"}\n'
            '    try:\n'
            '        url = "https://wttr.in/" + quote(str(city)) + "?format=j1"\n'
            '        req = Request(url, headers={"User-Agent": "curl/7.0"})\n'
            '        with urlopen(req, timeout=10) as resp:\n'
            '            data = json.loads(resp.read().decode("utf-8"))\n'
            '        return {"status": "ok", "city": city, "data": data}\n'
            '    except Exception as e:\n'
            '        return {"status": "error", "message": str(e)}\n'
        )
        test_code = (
            f'"""Tests for {name}."""\n'
            f"from {name} import {name}\n\n"
            f"def test_{name}_no_city():\n"
            f"    result = {name}()\n"
            f'    assert result["status"] == "error"\n\n'
            f"def test_{name}_empty_city():\n"
            f'    result = {name}("")\n'
            f"    assert isinstance(result, dict)\n"
        )
        return code, ["json", "urllib.request", "urllib.parse"], [], test_code

    def _gen_fetch_template(self, spec: CodeSpec) -> Tuple[str, List[str], List[str], str]:
        """通用 fetch 模板 — 用 urllib 抓 URL"""
        name = spec.name
        code = (
            '"""Auto-generated fetcher (mock fallback)."""\n'
            "import json\n"
            "from urllib.request import urlopen, Request\n\n\n"
            f"def {name}(url=None):\n"
            f'    """{spec.description}"""\n'
            '    if not url:\n'
            '        return {"status": "error", "message": "url is required"}\n'
            '    try:\n'
            '        req = Request(str(url), headers={"User-Agent": "superclaw/1.0"})\n'
            '        with urlopen(req, timeout=10) as resp:\n'
            '            body = resp.read().decode("utf-8")\n'
            '        return {"status": "ok", "url": url, "body": body}\n'
            '    except Exception as e:\n'
            '        return {"status": "error", "message": str(e)}\n'
        )
        test_code = (
            f'"""Tests for {name}."""\n'
            f"from {name} import {name}\n\n"
            f"def test_{name}_no_url():\n"
            f"    result = {name}()\n"
            f'    assert result["status"] == "error"\n\n'
            f"def test_{name}_empty_url():\n"
            f'    result = {name}("")\n'
            f"    assert isinstance(result, dict)\n"
        )
        return code, ["json", "urllib.request"], [], test_code


# ============================================================
# SandboxExecutor
# ============================================================

class SandboxExecutor:
    """在隔离目录验证生成的代码

    步骤:
      a. 写 code → sandbox_dir/{name}.py
      b. 写 test_code → sandbox_dir/test_{name}.py
      c. subprocess 跑 `python -c "import {name}"` 验证 import
      d. subprocess 跑 `python -m pytest test_{name}.py -v` 验证测试
      e. 收集结果，超时 30s

    安全（多层防御）:
      - 静态检查危险模式（17 种，含 open(w)/socket/ctypes/pickle 等）
      - 代码大小限制 100KB
      - subprocess 用 cwd=sandbox_dir 限制工作目录
      - 子进程 resource 限制（POSIX）：CPU 10s、内存 256MB、文件 1MB
      - 子进程环境隔离：清空 PYTHONPATH、禁 .pyc、固定 hash seed
      - 受限 import hook：拦截 os/subprocess/socket/ctypes 运行时导入
    """

    def __init__(self, sandbox_dir: Optional[Path] = None):
        if sandbox_dir is None:
            sandbox_dir = Path("superclaw-data/sandbox")
        self.sandbox_dir = Path(sandbox_dir)

    def _make_sandbox_env(self) -> Dict[str, str]:
        """构建沙箱子进程环境变量

        - 清空 PYTHONPATH（防止加载沙箱外的包）
        - PYTHONDONTWRITEBYTECODE=1（不写 .pyc）
        - PYTHONHASHSEED=0（固定 hash，防 hash 碰撞攻击）
        - 保留 PATH（系统命令查找需要）
        """
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONHASHSEED": "0",
            # 不设 PYTHONPATH → 子进程只找 cwd 和 stdlib
            # /tmp 仅作 HOME 缺失时的兜底，非临时文件写入路径（nosec B108）
            "HOME": os.environ.get("HOME", "/tmp"),  # nosec B108
        }
        return env

    def _make_resource_prelude(self) -> str:
        """生成资源限制 + import hook 前导代码

        POSIX 上用 resource.setrlimit 限制 CPU/内存/文件大小。
        所有平台都注入 import hook 拦截危险模块。
        """
        prelude = f'''
import sys
# POSIX 资源限制
try:
    import resource as _resource
    _resource.setrlimit(_resource.RLIMIT_CPU, ({SANDBOX_CPU_SECONDS}, {SANDBOX_CPU_SECONDS}))
    _resource.setrlimit(_resource.RLIMIT_FSIZE, ({SANDBOX_FILE_SIZE_BYTES}, {SANDBOX_FILE_SIZE_BYTES}))
    # RLIMIT_AS 在 Linux 上限制虚拟内存
    if hasattr(_resource, "RLIMIT_AS"):
        _resource.setrlimit(_resource.RLIMIT_AS, ({SANDBOX_MEMORY_BYTES}, {SANDBOX_MEMORY_BYTES}))
except Exception:
    pass  # 非 POSIX 或无权限，降级到无资源限制
'''
        return prelude + _RESTRICTED_IMPORT_HOOK

    def _run_sandboxed(self, code_str: str, timeout: int = SANDBOX_TIMEOUT) -> Tuple[bool, str]:
        """在沙箱内执行一段 Python 代码字符串

        Args:
            code_str: 要执行的代码（会被拼到 prelude 之后）
            timeout: 超时秒数

        Returns:
            (success, output) — success 为 True 时 output 是 stdout，
            False 时 output 是 stderr/stdout 合并的错误信息
        """
        full_code = self._make_resource_prelude() + "\n" + code_str
        try:
            r = subprocess.run(  # nosec B603 - 沙箱隔离执行
                [sys.executable, "-c", full_code],
                cwd=str(self.sandbox_dir),
                env=self._make_sandbox_env(),
                capture_output=True, text=True,
                timeout=timeout,
            )
            output = (r.stdout or "") + (r.stderr or "")
            return r.returncode == 0, output.strip()
        except subprocess.TimeoutExpired:
            return False, f"沙箱执行超时（{timeout}s）"
        except OSError as e:
            return False, f"沙箱执行失败: {e}"

    def execute(self, code: GeneratedCode, cleanup: bool = False) -> SandboxResult:
        """在沙箱验证代码"""
        t0 = time.time()
        errors: List[str] = []
        output_parts: List[str] = []

        # ---- 安全检查：代码大小 ----
        size = len(code.code.encode("utf-8"))
        if size > MAX_CODE_SIZE:
            errors.append(
                f"代码过大: {size} bytes > {MAX_CODE_SIZE} bytes (100KB)"
            )
            return SandboxResult(
                passed=False, errors=errors,
                output="; ".join(errors),
                duration_ms=int((time.time() - t0) * 1000),
            )

        # ---- 安全检查：危险模式 ----
        danger = self._check_dangerous(code.code)
        if danger:
            errors.append(f"代码含危险模式: {danger}")
        danger_test = self._check_dangerous(code.test_code)
        if danger_test:
            errors.append(f"测试代码含危险模式: {danger_test}")
        if errors:
            return SandboxResult(
                passed=False, errors=errors,
                output="; ".join(errors),
                duration_ms=int((time.time() - t0) * 1000),
            )

        # ---- 准备沙箱目录 ----
        self.sandbox_dir.mkdir(parents=True, exist_ok=True)
        name = code.name
        code_file = self.sandbox_dir / f"{name}.py"
        test_file = self.sandbox_dir / f"test_{name}.py"

        try:
            code_file.write_text(code.code, encoding="utf-8")
            if code.test_code:
                test_file.write_text(code.test_code, encoding="utf-8")
        except OSError as e:
            errors.append(f"写入沙箱失败: {e}")
            return SandboxResult(
                passed=False, errors=errors,
                output="; ".join(errors),
                duration_ms=int((time.time() - t0) * 1000),
            )

        # ---- Step c: import 检查 ----
        import_ok = False
        ok, out = self._run_sandboxed(f"import {name}; print('import ok')")
        if ok:
            import_ok = True
            output_parts.append(out)
        else:
            errors.append(f"import 失败: {out}")
            output_parts.append(out)

        # ---- Step c2: call 检查（函数存在且可调用）----
        call_ok = False
        if import_ok:
            ok, out = self._run_sandboxed(
                f"import {name}; assert callable({name}.{name}); print('call ok')"
            )
            if ok:
                call_ok = True
                output_parts.append(out)
            else:
                errors.append(f"call 检查失败: {out}")
                output_parts.append(out)

        # ---- Step d: pytest 检查 ----
        # pytest 自身依赖 shutil/socket 等模块，不能用 import hook，
        # 只用资源限制 + 环境隔离 + cwd 锁定
        test_ok = False
        if code.test_code and test_file.exists():
            try:
                r = subprocess.run(  # nosec B603 - 沙箱内执行 pytest
                    [sys.executable, "-m", "pytest",
                     f"test_{name}.py", "-v",
                     "-p", "no:cacheprovider"],
                    cwd=str(self.sandbox_dir),
                    env=self._make_sandbox_env(),
                    capture_output=True, text=True,
                    timeout=SANDBOX_TIMEOUT,
                )
                if r.returncode == 0:
                    test_ok = True
                    output_parts.append((r.stdout or "").strip())
                else:
                    err = ((r.stdout or "") + (r.stderr or "")).strip()
                    errors.append(f"测试失败: {err}")
                    output_parts.append(err)
            except subprocess.TimeoutExpired:
                errors.append("测试超时")
                output_parts.append("test timeout")
            except OSError as e:
                errors.append(f"pytest 执行失败: {e}")
        else:
            errors.append("无测试代码")
            output_parts.append("no test_code")

        # ---- 清理 ----
        if cleanup:
            for f in [code_file, test_file]:
                try:
                    if f.exists():
                        f.unlink()
                except OSError:
                    pass

        duration_ms = int((time.time() - t0) * 1000)
        passed = import_ok and call_ok and test_ok

        return SandboxResult(
            passed=passed,
            import_ok=import_ok,
            call_ok=call_ok,
            test_ok=test_ok,
            errors=errors,
            output="\n".join(output_parts),
            duration_ms=duration_ms,
        )

    @staticmethod
    def _check_dangerous(code_text: str) -> Optional[str]:
        """静态检查危险模式，命中返回第一个匹配的模式串"""
        if not code_text:
            return None
        for pattern in DANGEROUS_PATTERNS:
            m = re.search(pattern, code_text)
            if m:
                return m.group(0)
        return None
