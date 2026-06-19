"""Agent 自修改 (self-modify) 子系统。

流程:
    1. 分析目标（Agent 想修改 superclaw 哪个模块/哪个能力）
    2. CodeGenerator 生成候选代码
    3. SandboxExecutor 在隔离环境验证
    4. 结果归档到 self_modify_log.jsonl
    5. 只有验证通过才可能合并（默认 OFF，需要显式 auto_merge=True）

设计原则:
    - "失败安全": 任何自修改默认都只落到日志，不自动改现有代码
    - 可回溯: 所有尝试都落盘 (self_modify_log.jsonl)
    - 可审查: 每次尝试都包含 diff 文本 + 沙箱测试结果
"""

from __future__ import annotations

import ast
import difflib
import json
import shutil
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from .code_generator import (
        CodeGenerator, CodeSpec, GeneratedCode, SandboxExecutor, SandboxResult,
    )
except Exception:  # pragma: no cover
    CodeGenerator = None  # type: ignore
    CodeSpec = None       # type: ignore
    GeneratedCode = None  # type: ignore
    SandboxExecutor = None  # type: ignore
    SandboxResult = None  # type: ignore


@dataclass
class ModifyTarget:
    """Agent 想修改/新增的目标"""
    kind: str                           # "new_tool" | "patch_agent" | "fix_bug"
    target_name: str                    # 工具/函数名
    description: str                    # 功能描述一句话
    signature: str                      # def xxx(...) -> yyy
    reasoning: str                      # 为什么要做
    source_file_hint: Optional[str] = None   # 目标文件路径（可选）


@dataclass
class ModifyAttempt:
    ts: float
    target: Dict[str, Any]
    sandbox_passed: bool
    sandbox_detail: Dict[str, Any] = field(default_factory=dict)
    diff_preview: str = ""
    applied: bool = False
    error: Optional[str] = None


class SelfModifier:
    """自修改协调器。

    核心方法:
        - plan()     → 根据用户意图产生 ModifyTarget
        - attempt()  → 生成 + 沙箱验证，返回 ModifyAttempt
        - apply()    → （谨慎）把验证过的代码合并到代码库
    """

    def __init__(self, workspace: str = ".",
                 llm_router: Optional[Any] = None,
                 auto_merge: bool = False):
        self.workspace = Path(workspace)
        self.log_path = self.workspace / "self_modify_log.jsonl"
        self.auto_merge = auto_merge

        if CodeGenerator is None or SandboxExecutor is None:
            self.generator = None  # type: ignore
            self.sandbox = None    # type: ignore
        else:
            # 如果没有 llm_router，CodeGenerator 也会用 mock fallback 生成模板代码
            self.generator = CodeGenerator(llm_router=llm_router)
            self.sandbox = SandboxExecutor(sandbox_dir=str(self.workspace / ".sandbox"))

        self.modules_dir = self.workspace / "superclaw"

    # --------------------------------------------------------
    # 1. plan: Agent 本地规划
    # --------------------------------------------------------
    def plan(self, intent_text: str) -> ModifyTarget:
        """根据简短意图生成修改目标。无 LLM 依赖 —— 关键词规则。"""
        text = (intent_text or "").strip().lower()
        name_hint = _extract_first_name(intent_text or "")
        name = name_hint or "auto_generated_tool"
        signature = f"def {name}(context: dict = None) -> dict"
        kind = "new_tool"
        description = intent_text or "agent 自生成工具"
        reasoning = f"意图识别: {intent_text or '新增通用工具'}"

        if any(k in text for k in ("修复", "bug", "问题", "fix")):
            kind = "fix_bug"
            description = f"修复: {intent_text}"
        elif any(k in text for k in ("agent", "循环", "思考", "reasoning")):
            kind = "patch_agent"
            description = f"增强 Agent 循环: {intent_text}"

        return ModifyTarget(
            kind=kind, target_name=name,
            description=description, signature=signature,
            reasoning=reasoning,
        )

    # --------------------------------------------------------
    # 2. attempt: 生成 + 沙箱验证
    # --------------------------------------------------------
    def attempt(self, target: ModifyTarget) -> ModifyAttempt:
        """尝试生成并验证一段代码。失败安全，总是返回 ModifyAttempt。"""
        attempt = ModifyAttempt(
            ts=time.time(),
            target=asdict(target),
            sandbox_passed=False,
        )

        if self.generator is None or self.sandbox is None:
            attempt.error = "code_generator/SandboxExecutor 未正确导入"
            self._log(attempt)
            return attempt

        # 构造 CodeSpec
        try:
            spec = CodeSpec(
                name=target.target_name,
                description=target.description,
                signature=target.signature,
                parameters=[],
            )
        except Exception as e:
            attempt.error = f"spec_build: {e}"
            self._log(attempt)
            return attempt

        # 生成代码
        try:
            generated: Any = self.generator.generate(spec)
        except Exception as e:
            attempt.error = f"generate: {e}"
            self._log(attempt)
            return attempt

        code_str = getattr(generated, "code", "") or ""
        if not code_str.strip():
            attempt.error = "generate: 生成了空代码"
            self._log(attempt)
            return attempt

        # 语法检查（纯本地，不依赖沙箱）
        try:
            ast.parse(code_str)
        except SyntaxError as e:
            attempt.error = f"syntax: {e}"
            attempt.diff_preview = code_str[:400]
            self._log(attempt)
            return attempt

        # 沙箱执行
        try:
            result: Any = self.sandbox.execute(generated, cleanup=True)
            attempt.sandbox_passed = bool(getattr(result, "passed", False))
            attempt.sandbox_detail = {
                "import_ok": bool(getattr(result, "import_ok", False)),
                "call_ok": bool(getattr(result, "call_ok", False)),
                "test_ok": bool(getattr(result, "test_ok", False)),
                "output": (getattr(result, "output", "") or "")[:600],
                "errors": list(getattr(result, "errors", []) or [])[:10],
                "duration_ms": int((getattr(result, "duration", 0) or 0) * 1000),
            }
        except Exception as e:
            attempt.error = f"sandbox: {e}"
            attempt.diff_preview = code_str[:400]
            self._log(attempt)
            return attempt

        # diff: 把生成的代码和"空"对比，作为代码预览
        attempt.diff_preview = _diff_snippet("", code_str)

        # 自动合并（默认关闭，用户要显式开启）
        if self.auto_merge and attempt.sandbox_passed:
            try:
                applied = self._apply_new_tool(target.target_name, code_str)
                attempt.applied = applied
            except Exception as e:
                attempt.error = f"apply: {e}"
                attempt.applied = False

        self._log(attempt)
        return attempt

    # --------------------------------------------------------
    # 3. apply: 把代码落到 superclaw 目录（显式调用才执行）
    # --------------------------------------------------------
    def apply(self, target: ModifyTarget, code_str: str) -> bool:
        """显式申请把代码合并。会再做一次语法验证。"""
        try:
            ast.parse(code_str)
            return self._apply_new_tool(target.target_name, code_str)
        except Exception as e:
            attempt = ModifyAttempt(
                ts=time.time(), target=asdict(target),
                sandbox_passed=False, error=f"apply_syntax_or_write: {e}",
            )
            self._log(attempt)
            return False

    # --------------------------------------------------------
    # 4. 历史查询
    # --------------------------------------------------------
    def history(self, limit: int = 20) -> List[Dict[str, Any]]:
        if not self.log_path.exists():
            return []
        out: List[Dict[str, Any]] = []
        try:
            with open(self.log_path, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        out.append(json.loads(line))
                    except Exception:
                        continue
        except Exception:
            pass
        return out[-limit:]

    def summary(self) -> Dict[str, Any]:
        hist = self.history(limit=1000)
        passed = sum(1 for h in hist if h.get("sandbox_passed"))
        applied = sum(1 for h in hist if h.get("applied"))
        return {
            "total": len(hist),
            "passed": passed,
            "applied": applied,
            "auto_merge": self.auto_merge,
            "modules_dir": str(self.modules_dir),
        }

    # ========================================================
    # 内部: 写文件 / 日志
    # ========================================================
    def _apply_new_tool(self, name: str, code_str: str) -> bool:
        target_file = self.modules_dir / f"_gen_{name}.py"
        try:
            self.modules_dir.mkdir(parents=True, exist_ok=True)
            # 备份已存在的文件
            if target_file.exists():
                backup = target_file.with_suffix(target_file.suffix + ".bak")
                shutil.copy2(str(target_file), str(backup))
            target_file.write_text(code_str, encoding="utf-8")
            return True
        except Exception:
            return False

    def _log(self, attempt: ModifyAttempt) -> None:
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(asdict(attempt), ensure_ascii=False, default=str) + "\n")
        except Exception:
            pass


# ============================================================
# 工具函数
# ============================================================

def _extract_first_name(text: str) -> str:
    """从自然语言中抽取第一个可能的标识符"""
    import re
    m = re.search(r"([A-Za-z_][A-Za-z0-9_]{2,})", text)
    if not m:
        return ""
    name = m.group(1)
    # 避免常见停用词
    stop = {"def", "the", "for", "and", "not", "this", "that", "你好"}
    if name.lower() in stop:
        return ""
    return name.lower()


def _diff_snippet(a: str, b: str, n: int = 20) -> str:
    """产生一个简短的 unified diff 预览"""
    try:
        diff = difflib.unified_diff(
            (a or "").splitlines(keepends=True),
            (b or "").splitlines(keepends=True),
            fromfile="before", tofile="after",
        )
        lines = list(diff)[:n]
        return "".join(lines)
    except Exception:
        return (b or "")[:400]


def get_self_modifier(workspace: str = ".", llm_router: Any = None,
                      auto_merge: bool = False) -> SelfModifier:
    return SelfModifier(workspace=workspace, llm_router=llm_router, auto_merge=auto_merge)


if __name__ == "__main__":  # pragma: no cover
    m = get_self_modifier()
    target = m.plan("新增一个 fetch_weather 工具")
    print(target)
    attempt = m.attempt(target)
    print("passed:", attempt.sandbox_passed, "error:", attempt.error)
    print(m.summary())
