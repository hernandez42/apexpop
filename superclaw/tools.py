"""
superclaw 工具系统
类似 nanobot 的 ToolRegistry — 简单但真实
"""
import sys
import shlex
import subprocess
import time
import logging
import re
import os
import json
from pathlib import Path
from urllib import request as _ureq
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


def tool(func: Callable) -> Callable:
    """标记函数为工具"""
    setattr(func, "_is_tool", True)
    return func


class ToolResult:
    """工具执行结果"""
    def __init__(self, content: str, error: bool = False, tool_name: str = ""):
        self.content = content
        self.error = error
        self.tool_name = tool_name

    def __str__(self) -> str:
        return self.content


class ToolRegistry:
    """工具注册表
    注册的工具会被 Agent 在工具调用循环中调用
    """

    def __init__(self):
        self._tools: Dict[str, Dict[str, Any]] = {}
        self._history: List[Dict[str, Any]] = []
        self._workspace: Path = Path.cwd()

    def register(self, name: str, func: Callable, description: str = "",
                 params: Optional[Dict[str, Any]] = None) -> None:
        """注册工具
        name: 工具名（如 "file_read"）
        func: 可调用对象，接收 **kwargs，返回 str
        description: 告诉 LLM 这个工具做什么
        params: 参数描述（name -> {"type": "string", "description": "..."}）
        """
        self._tools[name] = {
            "func": func,
            "description": description,
            "params": params or {},
        }

    def set_workspace(self, path: str) -> None:
        self._workspace = Path(path)

    @property
    def names(self) -> List[str]:
        return sorted(self._tools.keys())

    @property
    def history(self) -> List[Dict[str, Any]]:
        return list(self._history)

    def get_description(self, name: str) -> Optional[str]:
        return self._tools.get(name, {}).get("description")

    def get_params(self, name: str) -> Dict[str, Any]:
        return self._tools.get(name, {}).get("params", {})

    def has(self, name: str) -> bool:
        return name in self._tools

    def unregister(self, name: str) -> bool:
        """注销工具（从注册表移除）

        Returns:
            True 如果工具存在并已移除，False 如果工具不存在
        """
        if name in self._tools:
            del self._tools[name]
            return True
        return False

    def call(self, name: str, **kwargs) -> ToolResult:
        """调用工具"""
        if name not in self._tools:
            return ToolResult(f"[错误] 未知工具: {name}", True, name)

        t0 = time.time()
        try:
            result = self._tools[name]["func"](**kwargs)
            dt = time.time() - t0
            self._history.append({
                "tool": name,
                "args": kwargs,
                "result": str(result)[:500],
                "time_ms": int(dt * 1000),
                "error": False,
            })
            return ToolResult(str(result), False, name)
        except Exception as e:
            self._history.append({
                "tool": name,
                "args": kwargs,
                "error": True,
                "message": str(e),
            })
            return ToolResult(f"[执行错误] {name}: {e}", True, name)

    def to_llm_instructions(self) -> str:
        """生成给 LLM 的工具说明文本"""
        lines = ["你可以调用以下工具。每次调用一个，格式: <tool name> <param1=value> <param2=value></tool>"]
        lines.append("")
        for name in sorted(self._tools):
            info = self._tools[name]
            params_str = ", ".join(f"{k}: {v.get('type', 'string')}" for k, v in info["params"].items())
            lines.append(f"- {name}({params_str}) — {info['description']}")
        lines.append("")
        lines.append("示例:")
        lines.append('  <tool file_read> <path>README.md</path></tool>')
        lines.append('  <tool shell> <cmd>ls -la</cmd></tool>')
        lines.append("")
        lines.append("如果你不需要工具，直接回答用户问题。")
        return "\n".join(lines)


# ========== 内置工具 ==========

def build_default_tools(cfg_workspace: str,
                        shell: bool = True, file_tools: bool = True,
                        dynamic_tools: bool = False,  # L3+ 2026-06-19 装 11 旁路工具开关
                        web: bool = False, think: bool = True,
                        github: bool = False) -> ToolRegistry:
    """创建默认工具

    github=True 时额外注册 GitHub 能力获取工具（github_search / github_clone /
    github_download / pip_install），默认不启用。
    """
    tools = ToolRegistry()
    tools.set_workspace(cfg_workspace)
    # L3+ 2026-06-19 装 11 旁路工具 (file_edit/file_grep/file_list/http_post/json_query/system_info/process_list/env_get/sleep_ms/current_time/file_append)
    if dynamic_tools:
        # L3+ 2026-06-19 直接内联 11 旁路工具 (避免 v250 path 优先冲突)
        if dynamic_tools:
            _install_inline_dynamic_tools(tools)


    # think 工具 — 用于让 Agent 自己思考、推理、分析
    if think:
        def _think(prompt: str) -> str:
            """让 Agent 思考一段内容，不产生副作用"""
            return f"[思考] {prompt}\n→ 已记录推理过程"
        tools.register("think", _think,
                       "思考/推理/分析，用于记录自己的推理过程，不会改变系统状态",
                       {"prompt": {"type": "string", "description": "思考内容"}})

    # 文件读取工具
    if file_tools:
        def _file_read(path: str) -> str:
            p = Path(path)
            if not p.is_absolute():
                p = tools._workspace / p
            if not p.exists():
                return f"[错误] 文件不存在: {p}"
            try:
                with open(p, encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                # 大文件截断
                if len(content) > 8000:
                    content = content[:4000] + "\n...[文件已截断]...\n" + content[-4000:]
                return content
            except Exception as e:
                return f"[读取错误] {e}"

        tools.register("file_read", _file_read,
                       "读取文件内容。path 是相对工作区路径或绝对路径。",
                       {"path": {"type": "string", "description": "文件路径"}})

        def _file_write(path: str, content: str) -> str:
            p = Path(path)
            if not p.is_absolute():
                p = tools._workspace / p
            p.parent.mkdir(parents=True, exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                f.write(content)
            return f"[成功] 已写入 {len(content)} 字符到 {p}"

        tools.register("file_write", _file_write,
                       "写入文件内容。path 是文件路径，content 是要写的内容。",
                       {"path": {"type": "string", "description": "文件路径"},
                        "content": {"type": "string", "description": "文件内容"}})

    # Shell 命令工具
    if shell:
        def _shell(cmd: str) -> str:
            """执行 shell 命令，返回输出

            安全说明：使用 shlex.split + shell=False 避免 shell 注入，
            不支持管道/重定向等 shell 元字符（agent 工具不应暴露完整 shell）。
            """
            try:
                # shlex.split 将命令字符串解析为参数列表，配合 shell=False
                # 避免 shell 元字符注入风险
                argv = shlex.split(cmd)
                if not argv:
                    return "[错误] 空命令"
                result = subprocess.run(
                    argv, shell=False, capture_output=True,
                    text=True, timeout=30, cwd=str(tools._workspace)
                )
                output = ""
                if result.stdout:
                    output += result.stdout[:3000]
                if result.stderr:
                    output += "\n[stderr]\n" + result.stderr[:3000]
                if not output.strip():
                    output = "[命令无输出]"
                return f"[exit={result.returncode}]\n{output.strip()[:5000]}"
            except subprocess.TimeoutExpired:
                return "[错误] 命令执行超时(30s)"
            except ValueError as e:
                return f"[命令解析错误] {e}"
            except Exception as e:
                return f"[执行错误] {e}"

        tools.register("shell", _shell,
                       "执行 shell 命令。在工作目录下运行，返回 stdout+stderr。",
                       {"cmd": {"type": "string", "description": "shell 命令"}})

    # 网络搜索（需要额外库 urllib — 已内置）
    if web:
        def _web_get(url: str) -> str:
            try:
                # 校验 URL scheme，仅允许 http/https，防止 file:// 等读取本地文件
                parsed = urlparse(url)
                if parsed.scheme not in ("http", "https"):
                    return f"[安全错误] 仅允许 http/https URL，拒绝 scheme={parsed.scheme!r}"
                from urllib import request as _r
                req = _r.Request(url, headers={"User-Agent": "superclaw/2.0"})
                with _r.urlopen(req, timeout=15) as resp:  # nosec B310 - URL scheme 已校验为 http/https
                    data = resp.read().decode("utf-8", errors="ignore")
                return data[:5000]
            except Exception as e:
                return f"[网络错误] {e}"

        tools.register("web_get", _web_get,
                       "获取 URL 的文本内容（网页/API 返回）",
                       {"url": {"type": "string", "description": "URL 地址"}})

    # memory 工具 — 查询记忆系统（APEX 融合）
    # 延迟导入避免循环依赖
    def _memory_query(query: str) -> str:
        """查询记忆系统 — 自然语言检索 md 知识/反思/进化历史"""
        store = _get_memory_store(tools._workspace)
        return store.query(query)

    tools.register("memory", _memory_query,
                   "查询记忆系统。用自然语言提问，如 '什么没做'、'进化历史'、'查找 进化'、'列出所有'。"
                   "可检索 SOUL/AGENTS/MEMORY 等 md 知识、反思日志、进化历史。",
                   {"query": {"type": "string", "description": "自然语言查询"}})

    def _memory_read(path: str) -> str:
        """读取记忆库中的指定 md 文件"""
        store = _get_memory_store(tools._workspace)
        return store.read_file(path)

    tools.register("memory_read", _memory_read,
                   "读取记忆库中指定路径的 md 文件完整内容。先用 memory 工具检索找到路径。",
                   {"path": {"type": "string", "description": "md 文件路径（如 SOUL.md）"}})

    # GitHub 能力获取工具 — 自进化的能力获取环节（默认不启用）
    if github:
        from .github_tools import (
            DependencyInstaller,
            FileDownloader,
            GitHubSearcher,
            RepoCloner,
        )

        _gh_searcher = GitHubSearcher()
        _gh_cloner = RepoCloner(target_dir=tools._workspace / "skills" / "repos")
        _gh_downloader = FileDownloader()
        _gh_installer = DependencyInstaller()

        def _github_search(query: str, language: str = "python",
                           kind: str = "repos", limit: int = 5) -> str:
            """搜索 GitHub 仓库或代码"""
            limit = max(1, min(int(limit), 10))
            if kind == "code":
                results = _gh_searcher.search_code(query, language=language, limit=limit)
            else:
                results = _gh_searcher.search_repos(query, language=language, limit=limit)
            # 结构化错误检测
            if results and "error" in results[0]:
                return f"[错误] {results[0]['error']}"
            if not results:
                return "[无结果] 未找到匹配项"
            lines = []
            for i, r in enumerate(results, 1):
                if kind == "code":
                    lines.append(f"{i}. {r.get('repository', '')}/{r.get('path', '')} "
                                 f"— {r.get('html_url', '')}")
                else:
                    lines.append(f"{i}. {r.get('full_name', '')} "
                                 f"({r.get('stargazers_count', 0)}★) — "
                                 f"{r.get('description', '')}")
                    lines.append(f"   clone: {r.get('clone_url', '')}")
            return "\n".join(lines)

        tools.register("github_search", _github_search,
                       "搜索 GitHub 仓库或代码。kind=repos 搜仓库，kind=code 搜代码。"
                       "返回名称/描述/星数/clone_url 等。",
                       {"query": {"type": "string", "description": "搜索关键词"},
                        "language": {"type": "string", "description": "编程语言（默认 python）"},
                        "kind": {"type": "string", "description": "repos 或 code（默认 repos）"},
                        "limit": {"type": "integer", "description": "返回数量（1-10，默认 5）"}})

        def _github_clone(url: str, name: str = "") -> str:
            """浅克隆 GitHub 仓库到 skills/repos/"""
            target = _gh_cloner.clone(url, name=name or None)
            if target is None:
                return f"[错误] 克隆失败：{url}（URL 须以 https://github.com/ 开头，目录不能已存在）"
            return f"[成功] 已克隆到 {target}"
        tools.register("github_clone", _github_clone,
                       "浅克隆 GitHub 仓库（--depth 1）到 skills/repos/。"
                       "URL 必须以 https://github.com/ 开头。",
                       {"url": {"type": "string", "description": "仓库 URL（https://github.com/user/repo.git）"},
                        "name": {"type": "string", "description": "本地目录名（可选）"}})

        def _github_download(url: str, target_path: str) -> str:
            """下载 GitHub raw 文件"""
            target = _gh_downloader.download_raw(url, Path(target_path))
            if target is None:
                return f"[错误] 下载失败：{url}（URL 须为 raw.githubusercontent.com 或 github.com/*/raw/，限 1MB）"
            return f"[成功] 已下载到 {target}"
        tools.register("github_download", _github_download,
                       "下载 GitHub raw 单文件。URL 须为 raw.githubusercontent.com 或 "
                       "github.com/*/raw/ 格式，文件限 1MB。",
                       {"url": {"type": "string", "description": "GitHub raw 文件 URL"},
                        "target_path": {"type": "string", "description": "本地保存路径"}})

        def _pip_install(package: str) -> str:
            """安装白名单内的 pip 包"""
            ok = _gh_installer.install(package)
            if ok:
                return f"[成功] 已安装 {package}"
            allowed = ", ".join(sorted(_gh_installer.allowed_packages))
            return f"[错误] 安装失败：{package}（包名须合法且在白名单内：{allowed}）"
        tools.register("pip_install", _pip_install,
                       "安装 pip 包（仅限白名单：requests/httpx/aiohttp/beautifulsoup4/"
                       "lxml/pyyaml/tomli）。包名须匹配 ^[a-zA-Z0-9_-]+$。",
                       {"package": {"type": "string", "description": "包名"}})

    return tools


# 全局 MemoryStore 缓存（按 workspace 路径）
_MEMORY_STORE_CACHE: Dict[str, Any] = {}


def _get_memory_store(workspace: Path):
    """获取或创建 MemoryStore（带缓存）"""
    key = str(workspace)
    if key not in _MEMORY_STORE_CACHE:
        from .memory import MemoryStore
        _MEMORY_STORE_CACHE[key] = MemoryStore(workspace)
    return _MEMORY_STORE_CACHE[key]


def scan_skills(skills_dir: Path) -> List[Dict[str, Any]]:
    """扫描 skills 目录，返回所有 skill 信息
    兼容 skill 生态：任何 .md 文件都可以是 skill

    返回: [{"path": ..., "title": ..., "triggers": [...], "preview": ...}]
    """
    skills: List[Dict[str, Any]] = []
    if not skills_dir.exists():
        return skills

    for path in sorted(skills_dir.glob("*.md")):
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
        except IOError:
            continue

        lines = content.splitlines()
        title = path.stem
        for line in lines:
            if line.startswith("#"):
                title = line.lstrip("#").strip()
                break

        # 提取触发词
        triggers = []
        for line in lines[:10]:
            if "触发词" in line or "trigger" in line.lower():
                # "触发词: a, b, c" → ["a", "b", "c"]
                parts = line.split(":", 1)
                if len(parts) == 2:
                    triggers = [t.strip() for t in parts[1].split(",") if t.strip()]
                break

        skills.append({
            "path": str(path),
            "title": title,
            "triggers": triggers,
            "preview": content[:150].replace("\n", " "),
        })

    return skills

def _install_inline_dynamic_tools(registry) -> int:
    """L3+ 2026-06-19 内联 11 旁路工具 (从 evolved.dynamic_tools 复制, 避免 sys.path 冲突)
    返回新工具数. 工具集:
    file_edit, file_grep, file_list, file_append, http_post, json_query,
    system_info, process_list, env_get, sleep_ms, current_time
    """
    def _file_edit(path: str, old_text: str, new_text: str) -> str:
        try:
            with open(path, encoding="utf-8", errors="ignore") as f:
                content = f.read()
            if old_text not in content:
                return f"[错误] 未找到 old_text (长度 {len(old_text)})"
            content = content.replace(old_text, new_text, 1)
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            return f"[成功] 已替换 {path}"
        except Exception as e:
            return f"[错误] {e}"

    def _file_grep(pattern: str, path: str = ".", max_results: int = 20) -> str:
        try:
            r = re.compile(pattern)
            results = []
            for root, _, files in os.walk(path):
                for fn in files:
                    fp = os.path.join(root, fn)
                    try:
                        with open(fp, encoding="utf-8", errors="ignore") as f:
                            for i, line in enumerate(f, 1):
                                if r.search(line):
                                    results.append(f"{fp}:{i}:{line.rstrip()}")
                                    if len(results) >= max_results:
                                        return "\n".join(results)
                    except (PermissionError, IsADirectoryError):
                        continue
            return "\n".join(results) if results else "[无匹配]"
        except Exception as e:
            return f"[错误] {e}"

    def _file_list(path: str = ".", pattern: str = "*", max_files: int = 50) -> str:
        try:
            from glob import glob
            results = glob(os.path.join(path, pattern))[:max_files]
            return "\n".join(results) if results else "[无文件]"
        except Exception as e:
            return f"[错误] {e}"

    def _http_post(url: str, body: str = "", headers: str = "") -> str:
        try:
            hdrs = {"User-Agent": "superclaw/0.1"}
            for line in headers.splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    hdrs[k.strip()] = v.strip()
            req = _ureq.Request(url, data=body.encode() if body else None,
                                headers=hdrs, method="POST")
            with _ureq.urlopen(req, timeout=30) as resp:
                return resp.read().decode(errors="ignore")[:5000]
        except Exception as e:
            return f"[错误] {e}"

    def _json_query(json_str: str, jq_like: str = ".") -> str:
        try:
            data = json.loads(json_str)
            for part in jq_like.split("."):
                if not part:
                    continue
                if "[" in part:
                    name, idx = part.split("[")
                    idx = int(idx.rstrip("]"))
                    data = data[name][idx] if name else data[idx]
                else:
                    data = data[part]
            return str(data)
        except Exception as e:
            return f"[错误] {e}"

    def _system_info() -> str:
        import platform as _p
        mem = os.popen("free -m").read() if os.path.exists("/proc/meminfo") else "N/A"
        return f"platform: {_p.platform()}\ncpu: {_p.processor() or os.uname().machine}\nmem:\n{mem}"

    def _process_list(max_n: int = 20) -> str:
        try:
            import subprocess as _sp
            out = _sp.check_output(["ps", "-eo", "pid,pcpu,pmem,comm", "--sort=-pcu"], text=True)
            lines = out.splitlines()[:max_n+1]
            return "\n".join(lines)
        except Exception as e:
            return f"[错误] {e}"

    def _env_get(name: str) -> str:
        val = os.environ.get(name, "")
        if any(k in name.upper() for k in ["KEY", "TOKEN", "SECRET", "PASSWORD"]):
            if val:
                return "[REDACTED]"
        return val

    def _sleep_ms(ms: int) -> str:
        time.sleep(ms / 1000)
        return f"[已睡眠 {ms}ms]"

    def _current_time() -> str:
        return time.strftime("%Y-%m-%d %H:%M:%S %Z", time.localtime())

    def _file_append(path: str, content: str) -> str:
        p = Path(path)
        try:
            with p.open("a", encoding="utf-8") as f:
                f.write(content)
            return f"[成功] 已追加 {len(content)} 字符到 {path}"
        except Exception as e:
            return f"[错误] {e}"

    funcs = {
        "file_edit": (_file_edit, "精确替换文件中的 old_text 为 new_text (单次替换).",
                      {"path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"}}),
        "file_grep": (_file_grep, "在 path 下递归 grep pattern (re 语法), 返回匹配行.",
                      {"pattern": {"type": "string"}, "path": {"type": "string"}, "max_results": {"type": "integer"}}),
        "file_list": (_file_list, "列出 path 下匹配 pattern 的文件.",
                      {"path": {"type": "string"}, "pattern": {"type": "string"}, "max_files": {"type": "integer"}}),
        "file_append": (_file_append, "追加内容到文件末尾.",
                        {"path": {"type": "string"}, "content": {"type": "string"}}),
        "http_post": (_http_post, "POST 请求 body 到 URL, 用 urllib.",
                      {"url": {"type": "string"}, "body": {"type": "string"}, "headers": {"type": "string"}}),
        "json_query": (_json_query, "简化 jq: .a.b[0] 路径查询 JSON.",
                       {"json_str": {"type": "string"}, "jq_like": {"type": "string"}}),
        "system_info": (_system_info, "返回系统基本信息 (platform/cpu/mem/disk).", {}),
        "process_list": (_process_list, "列出 top CPU 占用进程.", {"max_n": {"type": "integer"}}),
        "env_get": (_env_get, "获取环境变量 (敏感字段 redact).", {"name": {"type": "string"}}),
        "sleep_ms": (_sleep_ms, "睡眠 N 毫秒 (用于调度/测试).", {"ms": {"type": "integer"}}),
        "current_time": (_current_time, "返回当前时间字符串.", {}),
    }
    n = 0
    for name, (func, desc, params) in funcs.items():
        if name in registry.names:
            continue
        registry.register(name, func, desc, params)
        n += 1
    return n
