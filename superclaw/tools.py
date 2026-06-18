"""
superclaw 工具系统
类似 nanobot 的 ToolRegistry — 简单但真实
"""
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


def tool(func: Callable) -> Callable:
    """标记函数为工具"""
    func._is_tool = True
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
                        web: bool = False, think: bool = True) -> ToolRegistry:
    """创建默认工具"""
    tools = ToolRegistry()
    tools.set_workspace(cfg_workspace)

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
            """执行 shell 命令，返回输出"""
            try:
                result = subprocess.run(
                    cmd, shell=True, capture_output=True,
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
            except Exception as e:
                return f"[执行错误] {e}"

        tools.register("shell", _shell,
                       "执行 shell 命令。在工作目录下运行，返回 stdout+stderr。",
                       {"cmd": {"type": "string", "description": "shell 命令"}})

    # 网络搜索（需要额外库 urllib — 已内置）
    if web:
        def _web_get(url: str) -> str:
            try:
                from urllib import request as _r
                req = _r.Request(url, headers={"User-Agent": "superclaw/2.0"})
                with _r.urlopen(req, timeout=15) as resp:
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


def scan_skills(skills_dir: Path) -> List[Dict[str, str]]:
    """扫描 skills 目录，返回所有 skill 信息
    兼容 skill 生态：任何 .md 文件都可以是 skill

    返回: [{"path": ..., "title": ..., "triggers": [...], "preview": ...}]
    """
    skills = []
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
