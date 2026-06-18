"""
superclaw 动态工具加载与模块热加载机制

让 superclaw 能在运行时加载新能力：
- DynamicToolLoader: 从 Python 文件/代码字符串加载函数并注册成工具
- ModuleHotReloader: 管理已加载模块的 reload/unload
- EnhancedSkillLoader: 解析增强版 skill md（带 frontmatter），把 code 字段加载成真实工具

安全：
- 路径穿越防护（文件必须在允许目录内）
- 模块名/函数名/工具名校验（^[a-zA-Z_][a-zA-Z0-9_]*$）
- 不能覆盖已有内置工具（除非显式 force=True）
"""
from __future__ import annotations

import importlib
import importlib.util
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from .tools import ToolRegistry


# 标识符合法字符：字母/下划线开头，后接字母/数字/下划线
_IDENT_RE = re.compile(r'^[a-zA-Z_][a-zA-Z0-9_]*$')


def _is_valid_identifier(name: str) -> bool:
    """校验是否是合法 Python 标识符（模块名/函数名/工具名）"""
    return isinstance(name, str) and bool(_IDENT_RE.match(name))


def _is_path_within(path: Path, base: Path) -> bool:
    """检查 path 是否在 base 目录内（防路径穿越）

    用 resolve() 解析符号链接和 .. 后比较。base 自身也算"在内"。
    """
    try:
        path_resolved = Path(path).resolve()
        base_resolved = Path(base).resolve()
        try:
            path_resolved.relative_to(base_resolved)
            return True
        except ValueError:
            return False
    except (OSError, RuntimeError):
        return False


def _invalidate_pyc(file_path: Path) -> None:
    """删除缓存的 .pyc 文件，确保下次加载从源码读取

    Python 默认会把编译后的字节码缓存到 __pycache__/，按源文件 mtime 判断
    是否过期。同一秒内重写源文件时 mtime 可能不变，导致 reload 拿到旧字节码。
    """
    try:
        pyc = importlib.util.cache_from_source(str(file_path))
        if pyc:
            pyc_path = Path(pyc)
            if pyc_path.exists():
                pyc_path.unlink()
    except (ImportError, ValueError, OSError):
        pass


# ============================================================
# ModuleHotReloader — 模块级热加载
# ============================================================

class ModuleHotReloader:
    """管理已加载的 Python 模块，支持 reload/unload

    不依赖 ToolRegistry，纯粹做模块加载/重载/卸载。
    """

    def __init__(self):
        # module_name -> module object
        self._loaded_modules: Dict[str, Any] = {}
        # module_name -> file_path（记录来源，便于排查）
        self._module_paths: Dict[str, Path] = {}

    def load_module(self, file_path: Path, module_name: str) -> Any:
        """加载模块，记录到 _loaded_modules

        Args:
            file_path: Python 文件路径
            module_name: 模块名（必须合法标识符）

        Returns:
            加载的模块对象

        Raises:
            ValueError: 模块名非法
            FileNotFoundError: 文件不存在
            ImportError/SyntaxError: 加载失败
        """
        if not _is_valid_identifier(module_name):
            raise ValueError(
                f"非法模块名: {module_name!r}（须匹配 ^[a-zA-Z_][a-zA-Z0-9_]*$）"
            )

        file_path = Path(file_path)
        if not file_path.is_file():
            raise FileNotFoundError(f"模块文件不存在: {file_path}")

        spec = importlib.util.spec_from_file_location(module_name, file_path)
        if spec is None or spec.loader is None:
            raise ImportError(f"无法为 {file_path} 创建模块 spec")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        try:
            spec.loader.exec_module(module)
        except Exception:
            # 加载失败，从 sys.modules 移除避免污染
            sys.modules.pop(module_name, None)
            raise

        self._loaded_modules[module_name] = module
        self._module_paths[module_name] = file_path
        return module

    def reload_module(self, module_name: str) -> Any:
        """用 importlib.reload 重新加载已加载的模块

        若 importlib.reload 无法找到 spec（文件型模块不在 sys.path 上时常见），
        回退到从原文件路径重新加载。

        Raises:
            KeyError: 模块未加载
            ImportError/SyntaxError: 重载失败
        """
        if module_name not in self._loaded_modules:
            raise KeyError(f"模块未加载: {module_name}")

        module = self._loaded_modules[module_name]
        file_path = self._module_paths[module_name]

        # 删除 .pyc 缓存，确保读到最新源码
        _invalidate_pyc(file_path)

        # 确保模块在 sys.modules 中（reload 需要）
        sys.modules[module_name] = module
        try:
            reloaded = importlib.reload(module)
            self._loaded_modules[module_name] = reloaded
            return reloaded
        except (ModuleNotFoundError, ImportError):
            # importlib.reload 找不到 spec（文件型模块不在 sys.path 上）
            # 回退：从原文件路径重新加载
            pass

        sys.modules.pop(module_name, None)
        spec = importlib.util.spec_from_file_location(module_name, file_path)
        if spec is None or spec.loader is None:
            raise ImportError(f"无法为 {file_path} 创建模块 spec")
        new_module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = new_module
        try:
            spec.loader.exec_module(new_module)
        except Exception:
            sys.modules.pop(module_name, None)
            raise

        self._loaded_modules[module_name] = new_module
        return new_module

    def unload_module(self, module_name: str) -> bool:
        """从 sys.modules 移除模块

        Returns:
            True 如果原本已加载并成功移除，False 如果模块未加载
        """
        if module_name not in self._loaded_modules:
            return False
        sys.modules.pop(module_name, None)
        del self._loaded_modules[module_name]
        self._module_paths.pop(module_name, None)
        return True

    def get_module(self, module_name: str) -> Optional[Any]:
        """获取已加载的模块，未加载返回 None"""
        return self._loaded_modules.get(module_name)

    def list_loaded(self) -> List[str]:
        """返回所有已加载模块名（排序）"""
        return sorted(self._loaded_modules.keys())


# ============================================================
# DynamicToolLoader — 从文件/代码加载工具
# ============================================================

class DynamicToolLoader:
    """动态工具加载器

    从 Python 文件或代码字符串加载函数，注册到 ToolRegistry。

    安全：
    - 文件路径必须在 tools_dir 内（防路径穿越）
    - 模块名/函数名/工具名必须匹配 ^[a-zA-Z_][a-zA-Z0-9_]*$
    - 不能覆盖已有内置工具（除非 force=True）
    """

    def __init__(self, tool_registry: ToolRegistry,
                 tools_dir: Optional[Path] = None):
        self.tool_registry = tool_registry
        if tools_dir is None:
            tools_dir = Path("superclaw-data/dynamic-tools")
        self.tools_dir = Path(tools_dir)
        self.tools_dir.mkdir(parents=True, exist_ok=True)

        # 已加载的动态工具：tool_name -> 元信息
        self._dynamic_tools: Dict[str, Dict[str, Any]] = {}
        # 内置工具名快照（初始化时已存在的工具视为内置，不可被覆盖）
        self._builtin_tool_names: set = set(self.tool_registry.names)

    # ---- 安全检查 ----

    def _check_path_safety(self, file_path: Path) -> None:
        """检查文件路径是否在允许目录内（防路径穿越）

        Raises:
            ValueError: 路径不在允许目录内
        """
        file_path = Path(file_path)
        if not file_path.is_absolute():
            file_path = Path.cwd() / file_path
        if not _is_path_within(file_path, self.tools_dir):
            raise ValueError(
                f"安全错误：文件路径 {file_path} 不在允许目录 "
                f"{self.tools_dir} 内（路径穿越被拒绝）"
            )

    def _check_overwrite_builtin(self, tool_name: str, force: bool) -> None:
        """检查是否覆盖内置工具

        Raises:
            ValueError: 覆盖内置工具且未 force
        """
        if (tool_name in self._builtin_tool_names
                and tool_name not in self._dynamic_tools):
            if not force:
                raise ValueError(
                    f"安全错误：工具名 {tool_name!r} 是内置工具，"
                    f"拒绝覆盖（force=True 可强制）"
                )

    # ---- 加载 ----

    def load_from_file(self, file_path: Path, tool_name: str,
                       function_name: str, description: str,
                       params: List[Dict], force: bool = False) -> bool:
        """从 Python 文件加载指定函数并注册成工具

        Args:
            file_path: Python 文件路径（必须在 tools_dir 内）
            tool_name: 注册的工具名
            function_name: 文件中要加载的函数名
            description: 工具描述
            params: 参数描述列表
                [{"name": ..., "type": ..., "description": ..., "required": ...}]
            force: 是否允许覆盖已有内置工具

        Returns:
            True 成功，False 失败（文件不存在/语法错误/函数不存在等）

        Raises:
            ValueError: 工具名/函数名非法、路径穿越、覆盖内置工具未 force
        """
        # 校验标识符
        if not _is_valid_identifier(tool_name):
            raise ValueError(
                f"非法工具名: {tool_name!r}（须匹配 ^[a-zA-Z_][a-zA-Z0-9_]*$）"
            )
        if not _is_valid_identifier(function_name):
            raise ValueError(
                f"非法函数名: {function_name!r}（须匹配 ^[a-zA-Z_][a-zA-Z0-9_]*$）"
            )

        file_path = Path(file_path)
        # 路径穿越防护
        self._check_path_safety(file_path)
        # 内置工具覆盖防护
        self._check_overwrite_builtin(tool_name, force)

        if not file_path.is_file():
            return False

        # 删除 .pyc 缓存，确保读到最新源码（reload 场景下尤其重要）
        _invalidate_pyc(file_path)

        # 用唯一模块名加载（避免 sys.modules 冲突）
        module_name = f"_superclaw_dynamic_{tool_name}"
        try:
            spec = importlib.util.spec_from_file_location(module_name, file_path)
            if spec is None or spec.loader is None:
                return False
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)
        except SyntaxError:
            # 语法错误：清理后返回 False
            sys.modules.pop(module_name, None)
            return False
        except Exception:
            # 其他加载异常：清理后返回 False
            sys.modules.pop(module_name, None)
            return False

        # 获取函数
        func = getattr(module, function_name, None)
        if func is None or not callable(func):
            sys.modules.pop(module_name, None)
            return False

        # 把 params 列表转成 ToolRegistry 期望的 dict 格式
        params_dict = self._normalize_params(params)

        # 注册
        self.tool_registry.register(tool_name, func, description, params_dict)

        # 记录元信息
        self._dynamic_tools[tool_name] = {
            "tool_name": tool_name,
            "function_name": function_name,
            "file_path": str(file_path),
            "module_name": module_name,
            "description": description,
            "params": list(params) if params else [],
            "force": force,
        }
        return True

    def _normalize_params(self, params: List[Dict]) -> Dict[str, Dict[str, Any]]:
        """把 params 列表转成 ToolRegistry 期望的 {name: {type, description}} 格式"""
        if not params:
            return {}
        result: Dict[str, Dict[str, Any]] = {}
        for p in params:
            if not isinstance(p, dict) or "name" not in p:
                continue
            name = p["name"]
            result[name] = {
                "type": p.get("type", "string"),
                "description": p.get("description", ""),
            }
        return result

    def load_from_code(self, code: str, module_name: str, tool_name: str,
                       function_name: str, description: str,
                       params: List[Dict], force: bool = False) -> bool:
        """从代码字符串加载工具

        把 code 写入 tools_dir/{module_name}.py，再调用 load_from_file。

        Args:
            code: Python 源代码
            module_name: 模块名（必须合法标识符，用作文件名）
            tool_name: 注册的工具名
            function_name: 代码中要加载的函数名
            description: 工具描述
            params: 参数描述
            force: 是否允许覆盖内置工具

        Raises:
            ValueError: 模块名/工具名/函数名非法
        """
        if not _is_valid_identifier(module_name):
            raise ValueError(
                f"非法模块名: {module_name!r}（须匹配 ^[a-zA-Z_][a-zA-Z0-9_]*$）"
            )
        if not _is_valid_identifier(tool_name):
            raise ValueError(
                f"非法工具名: {tool_name!r}（须匹配 ^[a-zA-Z_][a-zA-Z0-9_]*$）"
            )
        if not _is_valid_identifier(function_name):
            raise ValueError(
                f"非法函数名: {function_name!r}（须匹配 ^[a-zA-Z_][a-zA-Z0-9_]*$）"
            )

        file_path = self.tools_dir / f"{module_name}.py"
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(code, encoding="utf-8")

        return self.load_from_file(file_path, tool_name, function_name,
                                   description, params, force=force)

    # ---- 卸载 / 列表 / 重载 ----

    def unload(self, tool_name: str) -> bool:
        """注销工具

        Returns:
            True 如果是动态工具且成功注销，False 如果不是动态工具
        """
        if tool_name not in self._dynamic_tools:
            return False
        meta = self._dynamic_tools[tool_name]
        # 从 ToolRegistry 移除（ToolRegistry 没有 unregister，直接操作 _tools）
        tools_dict = getattr(self.tool_registry, "_tools", None)
        if isinstance(tools_dict, dict) and tool_name in tools_dict:
            del tools_dict[tool_name]
        # 从 sys.modules 移除模块
        module_name = meta.get("module_name", "")
        if module_name:
            sys.modules.pop(module_name, None)
        del self._dynamic_tools[tool_name]
        return True

    def list_dynamic(self) -> List[Dict]:
        """列出所有动态加载的工具元信息"""
        return [dict(meta) for meta in self._dynamic_tools.values()]

    def reload(self, tool_name: str) -> bool:
        """重新加载工具（先 unload 再 load）

        Returns:
            True 重载成功，False 失败（如工具不存在或文件已删除）
        """
        if tool_name not in self._dynamic_tools:
            return False
        meta = self._dynamic_tools[tool_name]
        file_path = Path(meta["file_path"])
        function_name = meta["function_name"]
        description = meta["description"]
        params = meta["params"]
        force = meta.get("force", False)

        # 先 unload
        self.unload(tool_name)

        # 再 load
        return self.load_from_file(file_path, tool_name, function_name,
                                   description, params, force=force)


# ============================================================
# EnhancedSkillLoader — 增强版 skill 加载器
# ============================================================

class EnhancedSkillLoader:
    """增强版 skill 加载器

    支持两种 skill md 格式：

    1. 普通 skill md（只有标题和正文）：走原逻辑，注入到 system prompt
       - 如果提供了 agent，调用 agent.add_skill(skill_path)
       - 否则把 skill 内容累加到 self.injected_prompts

    2. 增强版 skill md（带 frontmatter）：
       ---
       name: weather_query
       description: 查询天气
       code: weather_tool.py  # 相对路径（也可用 file: 字段）
       entry: fetch_weather
       params:
         - name: city
           type: string
           required: true
       ---
       skill 正文（给 LLM 看的说明）

       - 如果有 code/entry，用 DynamicToolLoader 加载成真实工具
       - 如果没有，走原 prompt 注入逻辑
    """

    def __init__(self, tool_registry: ToolRegistry, skills_dir: Path,
                 agent: Optional[Any] = None,
                 tools_dir: Optional[Path] = None):
        self.tool_registry = tool_registry
        self.skills_dir = Path(skills_dir)
        self.agent = agent
        # 内部 DynamicToolLoader：允许从 skills_dir 加载 code 文件
        # （code 字段是相对 skill md 的路径，最终落在 skills_dir 内）
        effective_tools_dir = tools_dir if tools_dir is not None else self.skills_dir
        self.dynamic_loader = DynamicToolLoader(
            tool_registry, tools_dir=effective_tools_dir
        )
        # 已注入的 prompt 内容（无 agent 时使用）
        self.injected_prompts: List[str] = []
        # 已加载的 skill 路径
        self.loaded_skills: List[str] = []

    def load_skill(self, skill_path: Path) -> bool:
        """解析增强版 skill md 文件

        Returns:
            True 加载成功，False 失败
        """
        skill_path = Path(skill_path)
        if not skill_path.exists():
            return False

        try:
            content = skill_path.read_text(encoding="utf-8")
        except Exception:
            return False

        frontmatter, body = self._parse_frontmatter(content)

        if frontmatter is None:
            # 普通 skill md：走 prompt 注入
            return self._inject_prompt(skill_path, content)

        # 增强版 skill md
        name = frontmatter.get("name")
        if not name or not _is_valid_identifier(str(name)):
            # frontmatter 格式错误（缺 name 或 name 非法）
            return False

        name = str(name)
        description = frontmatter.get("description", "")
        if not isinstance(description, str):
            description = str(description)
        # code 和 file 是同义字段
        code = frontmatter.get("code") or frontmatter.get("file")
        entry = frontmatter.get("entry")
        params = frontmatter.get("params", [])

        if code and entry:
            # 加载成真实工具
            if not _is_valid_identifier(str(entry)):
                return False
            entry = str(entry)
            code_path = (skill_path.parent / str(code)).resolve()
            # 路径穿越防护：code 必须在 skills_dir 内
            if not _is_path_within(code_path, self.skills_dir):
                return False
            if not isinstance(params, list):
                return False

            ok = self.dynamic_loader.load_from_file(
                file_path=code_path,
                tool_name=name,
                function_name=entry,
                description=description,
                params=params,
            )
            if not ok:
                return False
            # 工具加载成功
        else:
            # 没有 code/entry，走 prompt 注入
            if body and body.strip():
                self._append_prompt(name, body)
            # 没有正文也没什么可注入的，但仍视为加载成功

        self.loaded_skills.append(str(skill_path))
        return True

    def _parse_frontmatter(self, content: str):
        """解析 frontmatter，返回 (frontmatter_dict, body)

        - 没有 frontmatter：返回 (None, content)
        - frontmatter 格式错误（YAML 解析失败）：返回 ({}, body)
        - frontmatter 为空：返回 ({}, body)
        """
        if not content.startswith("---"):
            return None, content

        lines = content.splitlines()
        if not lines or lines[0].strip() != "---":
            return None, content

        # 找到结束的 ---
        end_idx = None
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                end_idx = i
                break

        if end_idx is None:
            # 没有结束符，frontmatter 格式错误
            return {}, content

        frontmatter_text = "\n".join(lines[1:end_idx])
        body = "\n".join(lines[end_idx + 1:]).strip()

        try:
            import yaml  # type: ignore[import-untyped]
            data = yaml.safe_load(frontmatter_text)
            if data is None:
                return {}, body
            if not isinstance(data, dict):
                return {}, body
            return data, body
        except Exception:
            # YAML 解析失败
            return {}, body

    def _inject_prompt(self, skill_path: Path, content: str) -> bool:
        """走原 prompt 注入逻辑（普通 skill md）"""
        if self.agent is not None and hasattr(self.agent, "add_skill"):
            ok = self.agent.add_skill(str(skill_path))
            if ok:
                self.loaded_skills.append(str(skill_path))
            return ok
        # 没有 agent，自己累加
        lines = content.strip().splitlines()
        if not lines:
            return False
        title = lines[0].lstrip("#").strip()
        body = "\n".join(lines[1:]).strip()
        self.injected_prompts.append(f"## Skill: {title}\n{body}")
        self.loaded_skills.append(str(skill_path))
        return True

    def _append_prompt(self, name: str, body: str) -> None:
        """把增强 skill 的 body 注入到 prompt"""
        if self.agent is not None and hasattr(self.agent, "system_prompt"):
            self.agent.system_prompt = (
                f"{self.agent.system_prompt}\n\n## Skill: {name}\n{body}"
            )
        else:
            self.injected_prompts.append(f"## Skill: {name}\n{body}")

    def load_all_skills(self) -> List[str]:
        """扫描 skills_dir 加载所有 .md skill

        Returns:
            成功加载的 skill 路径列表
        """
        loaded: List[str] = []
        if not self.skills_dir.exists():
            return loaded

        for path in sorted(self.skills_dir.glob("*.md")):
            if self.load_skill(path):
                loaded.append(str(path))
        return loaded
