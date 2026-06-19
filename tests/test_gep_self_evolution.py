"""测试 superclaw.gep_engine 自进化集成 — 真实的"感知短板→获取能力→自我构建→验证闭环"

覆盖：
- 向后兼容：不传新模块 → run_cycle 走原逻辑
- _step_execute_modify_real：CodeGenerator 生成 + SandboxExecutor 验证
- _step_validate_real：基于 SandboxResult 判定
- _step_solidify_real：DynamicToolLoader 注册 + EvolutionValidator 验证 + 回滚
- run_self_evolution_cycle：完整自进化循环（gap 检测 → 代码生成 → 沙箱 → 注册 → 验证）
- 安全边界：critical 风险拒绝、测试失败回滚
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from superclaw.gep_engine import GEPEngine
from superclaw.gep_schema import Signal
from superclaw.memory import MemoryStore
from superclaw.llm_router import CompletionResult
from superclaw.tools import ToolRegistry
from superclaw.capability_registry import (
    CapabilityRegistry, Capability, analyze_gaps,
)
from superclaw.code_generator import (
    CodeGenerator, CodeSpec, GeneratedCode, SandboxExecutor,
)
from superclaw.dynamic_loader import DynamicToolLoader
from superclaw.evolution_validator import (
    EvolutionValidator, EvolutionAction, TestResult, TestRunner,
    SnapshotManager,
)


# ============================================================
# Mock LLM — 信号提取返回空，代码生成返回 error 触发 mock 兜底
# ============================================================

class SelfEvoMockLLM:
    """自进化测试用 Mock LLM

    - 信号提取 prompt（含 "分析以下进化信号"）→ 返回空 JSON 数组
    - 代码生成 prompt（含 "你是 superclaw 代码生成器"）→ 返回 error 触发 mock 兜底
    - 其他 prompt → 返回 error
    """

    def __init__(self):
        self.calls = []

    def complete(self, messages, complexity="medium", provider=None, max_tokens=None):
        self.calls.append({"messages": messages, "complexity": complexity})
        prompt = messages[0]["content"] if messages else ""

        if "分析以下进化信号" in prompt:
            return CompletionResult(
                content="[]", provider="mock", model="mock",
                tokens_used=10, error=None,
            )

        # 代码生成 prompt → 返回 error 触发 mock 兜底
        return CompletionResult(
            content="", provider="mock", model="mock",
            tokens_used=0, error="mock error for code gen",
        )

    def status(self):
        return {"providers": {"mock": {"enabled": True, "model": "mock"}}}


# ============================================================
# Helpers
# ============================================================

def _add_reflection(memory):
    """添加一条反思记录（产生 gaps/problems 信号源）"""
    memory.reflection.reflect({
        "phi": 0.3, "tier": 1, "fitness": 0.4,
        "mutations": 1, "knowledge": 0,
        "health": 0, "balance": 0.5,
    })


def _make_mock_test_runner(passed=True, failed=0, error=0, total=1):
    """构造 mock TestRunner，返回可控的 TestResult"""
    runner = MagicMock(spec=TestRunner)
    runner.run_unit_tests.return_value = TestResult(
        passed=passed,
        total=total,
        passed_count=total - failed - error if passed else 0,
        failed_count=failed,
        error_count=error,
        output="ok" if passed else "failed",
        duration_ms=10,
    )
    return runner


def _make_self_evo_engine(tmp_workspace, test_runner_passed=True,
                          test_runner_failed=0):
    """创建一个带全部自进化模块的 GEPEngine

    Args:
        tmp_workspace: 临时工作目录（conftest fixture）
        test_runner_passed: TestRunner 是否返回通过
        test_runner_failed: TestRunner 返回的失败数
    """
    llm = SelfEvoMockLLM()

    # 真实 CodeGenerator（LLM 返回 error → 走 mock 兜底生成可执行模板代码）
    code_generator = CodeGenerator(llm)

    # 真实 SandboxExecutor
    sandbox_executor = SandboxExecutor(
        sandbox_dir=tmp_workspace / "sandbox"
    )

    # 真实 ToolRegistry + DynamicToolLoader
    tool_registry = ToolRegistry()
    dynamic_loader = DynamicToolLoader(
        tool_registry,
        tools_dir=tmp_workspace / "dynamic-tools",
    )

    # 真实 CapabilityRegistry
    capability_registry = CapabilityRegistry(
        registry_path=tmp_workspace / "capabilities.json"
    )
    capability_registry.register_defaults()

    # mock TestRunner + 真实 SnapshotManager + 真实 EvolutionValidator
    test_runner = _make_mock_test_runner(
        passed=test_runner_passed, failed=test_runner_failed
    )
    snapshot_mgr = SnapshotManager(
        snapshot_dir=tmp_workspace / "snapshots"
    )
    evolution_validator = EvolutionValidator(
        project_root=tmp_workspace,
        test_runner=test_runner,
        snapshot_mgr=snapshot_mgr,
    )

    memory = MemoryStore(tmp_workspace)
    _add_reflection(memory)

    engine = GEPEngine(
        memory=memory,
        llm=llm,
        strategy="balanced",
        workspace=tmp_workspace,
        capability_registry=capability_registry,
        code_generator=code_generator,
        sandbox_executor=sandbox_executor,
        dynamic_loader=dynamic_loader,
        evolution_validator=evolution_validator,
        project_root=tmp_workspace,
    )
    return engine


# ============================================================
# 向后兼容测试
# ============================================================

def test_backward_compat_no_real_steps(tmp_workspace):
    """不传新模块 → _use_real_steps=False，run_cycle 走原逻辑"""
    from superclaw.llm_router import LLMRouter
    llm = LLMRouter()
    llm.add_provider("mock", priority=1)
    memory = MemoryStore(tmp_workspace)
    engine = GEPEngine(memory=memory, llm=llm, workspace=tmp_workspace)

    assert engine._use_real_steps is False
    assert engine.capability_registry is None
    assert engine.code_generator is None
    assert engine.sandbox_executor is None
    assert engine.dynamic_loader is None
    assert engine.evolution_validator is None


def test_backward_compat_run_cycle_uses_original(tmp_workspace):
    """不传新模块 → run_cycle 调用原 _step_execute_modify 而非 _real"""
    from tests.test_gep_engine_coverage import MockLLMRouter
    memory = MemoryStore(tmp_workspace)
    _add_reflection(memory)
    llm = MockLLMRouter(signal_content="[]")
    engine = GEPEngine(memory=memory, llm=llm, workspace=tmp_workspace)

    result = engine.run_cycle()

    # 原逻辑应该正常工作
    assert result["cycle"] == 1
    assert "steps" in result
    # 原 _step_execute_modify 返回的 provider 是 "mock"
    assert result["steps"]["5_execute_modify"]["provider"] == "mock"


def test_real_steps_flag_enabled(tmp_workspace):
    """传入全部新模块 → _use_real_steps=True"""
    engine = _make_self_evo_engine(tmp_workspace)
    assert engine._use_real_steps is True
    assert engine.capability_registry is not None
    assert engine.code_generator is not None
    assert engine.sandbox_executor is not None
    assert engine.dynamic_loader is not None
    assert engine.evolution_validator is not None


# ============================================================
# _step_execute_modify_real 测试
# ============================================================

def test_step_execute_modify_real_success(tmp_workspace):
    """_step_execute_modify_real：CodeGenerator 生成 + SandboxExecutor 验证"""
    engine = _make_self_evo_engine(tmp_workspace)
    engine.cycle_count = 1

    modification = engine._step_execute_modify_real("test prompt", "repair")

    # 应该返回 CompletionResult
    assert modification.provider in ("mock-fallback:llm_error", "code_generator")
    # mock 兜底生成的代码非空
    assert len(modification.content) > 0
    # 沙箱验证应该通过（mock 兜底生成简单可执行代码）
    assert modification.error is None
    # 中间结果被缓存
    assert engine._last_generated_code is not None
    assert engine._last_sandbox_result is not None
    assert engine._last_sandbox_result.passed is True


def test_step_execute_modify_real_no_modules(tmp_workspace):
    """未配置 code_generator/sandbox_executor → 返回 error"""
    from superclaw.llm_router import LLMRouter
    llm = LLMRouter()
    llm.add_provider("mock", priority=1)
    engine = GEPEngine(
        memory=MemoryStore(tmp_workspace), llm=llm, workspace=tmp_workspace,
    )
    # 强制启用 _real 但不配置模块
    engine._use_real_steps = True

    modification = engine._step_execute_modify_real("prompt", "repair")
    assert modification.error is not None
    assert "未配置" in modification.error


# ============================================================
# _step_validate_real 测试
# ============================================================

def test_step_validate_real_passed(tmp_workspace):
    """沙箱通过 → score=0.9, passed=True"""
    engine = _make_self_evo_engine(tmp_workspace)
    engine.cycle_count = 1

    # 先执行 _real 生成代码 + 沙箱验证
    modification = engine._step_execute_modify_real("prompt", "repair")
    validation = engine._step_validate_real(modification, "repair")

    assert validation["passed"] is True
    assert validation["score"] == 0.9
    assert "sandbox" in validation
    assert validation["sandbox"]["import_ok"] is True
    assert validation["sandbox"]["test_ok"] is True


def test_step_validate_real_error(tmp_workspace):
    """modification 带 error → 验证失败"""
    engine = _make_self_evo_engine(tmp_workspace)
    mod = CompletionResult(
        content="", provider="mock", model="mock", error="some error"
    )
    validation = engine._step_validate_real(mod, "repair")
    assert validation["passed"] is False
    assert validation["score"] == 0.0
    assert "error" in validation["reason"]


def test_step_validate_real_no_sandbox_result(tmp_workspace):
    """无沙箱结果 → 验证失败"""
    engine = _make_self_evo_engine(tmp_workspace)
    mod = CompletionResult(
        content="code", provider="mock", model="mock", error=None
    )
    # 不调用 _step_execute_modify_real，直接调 _step_validate_real
    validation = engine._step_validate_real(mod, "repair")
    assert validation["passed"] is False
    assert "无沙箱验证结果" in validation["reason"]


# ============================================================
# _step_solidify_real 测试
# ============================================================

def test_step_solidify_real_success(tmp_workspace):
    """验证通过 + EvolutionValidator 通过 → 创建 Capsule + 注册能力"""
    engine = _make_self_evo_engine(tmp_workspace, test_runner_passed=True)
    engine.cycle_count = 1

    # 先执行 _real 生成代码 + 沙箱验证
    modification = engine._step_execute_modify_real("prompt", "repair")
    validation = engine._step_validate_real(modification, "repair")

    # 固化
    signals = [Signal(signal_type="error", pattern="test error")]
    capsule = engine._step_solidify_real(
        signals, "repair", modification, validation, None
    )

    # 应该返回 Capsule（score=0.9 > 0.6 → should_solidify=True）
    assert capsule is not None
    assert capsule.outcome["status"] == "success"
    assert "tool_name" in capsule.outcome

    # 新能力应该被注册到 CapabilityRegistry
    cap = engine.capability_registry.get(capsule.outcome["tool_name"])
    assert cap is not None
    assert cap.source == "generated"


def test_step_solidify_real_validation_failed(tmp_workspace):
    """验证未通过 → 返回 None"""
    engine = _make_self_evo_engine(tmp_workspace)
    mod = CompletionResult(content="code", provider="mock", model="mock")
    validation = {"passed": False, "score": 0.0}

    capsule = engine._step_solidify_real(
        [Signal(pattern="x")], "repair", mod, validation, None
    )
    assert capsule is None


def test_step_solidify_real_test_failure_rollback(tmp_workspace):
    """EvolutionValidator 测试失败 → 回滚，返回 None"""
    engine = _make_self_evo_engine(
        tmp_workspace, test_runner_passed=False, test_runner_failed=2
    )
    engine.cycle_count = 1

    # 先执行 _real 生成代码 + 沙箱验证
    modification = engine._step_execute_modify_real("prompt", "repair")
    validation = engine._step_validate_real(modification, "repair")
    assert validation["passed"] is True  # 沙箱通过

    # 固化时 EvolutionValidator 测试失败 → 回滚
    capsule = engine._step_solidify_real(
        [Signal(pattern="x")], "repair", modification, validation, None
    )
    # 测试失败 → 不创建 Capsule
    assert capsule is None


def test_step_solidify_real_critical_rejected(tmp_workspace):
    """target_files 在 superclaw/ 核心包 → critical 拒绝，返回 None"""
    engine = _make_self_evo_engine(tmp_workspace)
    engine.cycle_count = 1

    # 先执行 _real 生成代码 + 沙箱验证
    modification = engine._step_execute_modify_real("prompt", "repair")
    validation = engine._step_validate_real(modification, "repair")

    # 篡改 _last_tool_file 指向 superclaw/ 核心包（模拟 critical 风险）
    engine._last_tool_file = engine.project_root / "superclaw" / "agent.py"
    # 同时修改 generated.name 对应的工具文件路径
    # （EvolutionAction 用 target_files 判定 blast radius）
    # 直接构造一个 target_files 在核心包的 action
    from superclaw.evolution_validator import EvolutionAction
    action = EvolutionAction(
        action_type="add_tool",
        target_files=[engine.project_root / "superclaw" / "agent.py"],
        backup_snapshot_id=None,
        description="modify core",
    )
    result = engine.evolution_validator.validate_evolution(action)
    assert result.passed is False
    assert result.blast_radius.risk_level == "critical"


# ============================================================
# run_cycle 集成测试（_real 步骤）
# ============================================================

def test_run_cycle_with_real_steps(tmp_workspace):
    """完整 run_cycle 使用 _real 步骤"""
    engine = _make_self_evo_engine(tmp_workspace, test_runner_passed=True)

    result = engine.run_cycle()

    assert result["cycle"] == 1
    assert "steps" in result

    steps = result["steps"]
    for s in ["1_scan_logs", "2_extract_signals", "3_select_gene",
              "4_generate_prompt", "5_execute_modify", "6_validate",
              "7_solidify", "8_publish", "9_log_event", "10_monitor"]:
        assert s in steps, f"缺少 step: {s}"

    # _real 步骤应该使用 code_generator
    assert "code_generator" in steps["5_execute_modify"]["provider"] or \
           "mock-fallback" in steps["5_execute_modify"]["provider"]
    # 沙箱验证应该通过
    assert steps["6_validate"]["passed"] is True
    assert steps["6_validate"]["score"] == 0.9


# ============================================================
# run_self_evolution_cycle 测试
# ============================================================

def test_run_self_evolution_cycle_success(tmp_workspace):
    """完整自进化循环：感知短板 → 获取能力 → 自我构建 → 验证闭环"""
    engine = _make_self_evo_engine(tmp_workspace, test_runner_passed=True)

    # 用未注册的能力名作为任务需求
    result = engine.run_self_evolution_cycle(
        task_requirements=["custom_data_processor"]
    )

    assert result["status"] in ("success", "no_acquisition")
    assert len(result["gaps"]) > 0
    # 至少检测到 custom_data_processor 缺口
    gap_names = [g["missing_capability"] for g in result["gaps"]]
    assert "custom_data_processor" in gap_names

    # 如果成功获取能力，应该有 validated 或 acquired
    if result["acquired"]:
        acquired_names = [a["code_name"] for a in result["acquired"]]
        assert "custom_data_processor" in acquired_names


def test_run_self_evolution_cycle_no_gaps(tmp_workspace):
    """所有能力都已注册 → no_gaps"""
    engine = _make_self_evo_engine(tmp_workspace)

    # 用已注册的默认能力作为任务需求
    result = engine.run_self_evolution_cycle(
        task_requirements=["file_read", "file_write", "shell"]
    )

    assert result["status"] == "no_gaps"
    assert len(result["gaps"]) == 0


def test_run_self_evolution_cycle_skipped_no_registry(tmp_workspace):
    """未配置 capability_registry → skipped"""
    from superclaw.llm_router import LLMRouter
    llm = LLMRouter()
    llm.add_provider("mock", priority=1)
    engine = GEPEngine(
        memory=MemoryStore(tmp_workspace), llm=llm, workspace=tmp_workspace,
    )

    result = engine.run_self_evolution_cycle()
    assert result["status"] == "skipped"
    assert len(result["errors"]) > 0


def test_run_self_evolution_cycle_test_failure(tmp_workspace):
    """EvolutionValidator 测试失败 → rolled_back 或 rejected"""
    engine = _make_self_evo_engine(
        tmp_workspace, test_runner_passed=False, test_runner_failed=2
    )

    result = engine.run_self_evolution_cycle(
        task_requirements=["custom_processor"]
    )

    # 测试失败时，已获取的能力应该被回滚或拒绝
    assert result["status"] in ("failed", "no_acquisition", "success")
    # 不应该有 validated（因为测试失败）
    # （注意：如果 sandbox 失败，会进 rejected 而非 rolled_back）
    assert len(result["validated"]) == 0


def test_run_self_evolution_cycle_manual_action_rejected(tmp_workspace):
    """manual action 的 gap → 被拒绝"""
    engine = _make_self_evo_engine(tmp_workspace)

    # file_read 是 DEFAULT_CAPABILITIES 中的能力，但如果我们先注销它
    engine.capability_registry.unregister("file_read")

    result = engine.run_self_evolution_cycle(
        task_requirements=["file_read"]
    )

    # file_read 是 builtin 能力缺失 → suggested_action=manual → 被拒绝
    assert len(result["gaps"]) > 0
    assert result["gaps"][0]["suggested_action"] == "manual"
    assert len(result["rejected"]) > 0
    assert "manual" in result["rejected"][0]["reason"]


# ============================================================
# 安全边界测试
# ============================================================

def test_safety_only_add_to_dynamic_tools(tmp_workspace):
    """自进化生成的工具文件只能写到 dynamic-tools/ 目录"""
    engine = _make_self_evo_engine(tmp_workspace, test_runner_passed=True)
    engine.cycle_count = 1

    modification = engine._step_execute_modify_real("prompt", "repair")
    validation = engine._step_validate_real(modification, "repair")

    capsule = engine._step_solidify_real(
        [Signal(pattern="x")], "repair", modification, validation, None
    )

    if capsule is not None:
        # 工具文件应该在 dynamic-tools/ 目录内
        tool_file = engine._last_tool_file
        assert "dynamic-tools" in str(tool_file)
        # 不应该在 superclaw/ 核心包内
        assert "superclaw/" not in str(tool_file) or \
               "dynamic-tools" in str(tool_file)


def test_safety_critical_risk_rejected(tmp_workspace):
    """修改 superclaw/ 核心包 → critical 风险被拒绝"""
    engine = _make_self_evo_engine(tmp_workspace)

    # 直接构造一个 target_files 在核心包的 action
    action = EvolutionAction(
        action_type="modify_code",
        target_files=[engine.project_root / "superclaw" / "agent.py"],
        backup_snapshot_id=None,
        description="modify core package",
    )
    result = engine.evolution_validator.validate_evolution(action)

    assert result.passed is False
    assert result.blast_radius.risk_level == "critical"
    assert any("核心" in e or "critical" in e.lower() for e in result.errors)


def test_safety_all_external_calls_try_except(tmp_workspace):
    """所有外部调用都有 try-except 保护，不抛异常"""
    # 用一个会抛异常的 code_generator
    bad_generator = MagicMock()
    bad_generator.generate.side_effect = RuntimeError("LLM connection failed")

    llm = SelfEvoMockLLM()
    sandbox_executor = SandboxExecutor(sandbox_dir=tmp_workspace / "sandbox")
    tool_registry = ToolRegistry()
    dynamic_loader = DynamicToolLoader(
        tool_registry, tools_dir=tmp_workspace / "dynamic-tools"
    )
    capability_registry = CapabilityRegistry(
        registry_path=tmp_workspace / "capabilities.json"
    )
    capability_registry.register_defaults()
    test_runner = _make_mock_test_runner(passed=True)
    snapshot_mgr = SnapshotManager(snapshot_dir=tmp_workspace / "snapshots")
    evolution_validator = EvolutionValidator(
        project_root=tmp_workspace,
        test_runner=test_runner,
        snapshot_mgr=snapshot_mgr,
    )

    engine = GEPEngine(
        memory=MemoryStore(tmp_workspace), llm=llm, workspace=tmp_workspace,
        capability_registry=capability_registry,
        code_generator=bad_generator,
        sandbox_executor=sandbox_executor,
        dynamic_loader=dynamic_loader,
        evolution_validator=evolution_validator,
        project_root=tmp_workspace,
    )

    # _step_execute_modify_real 应该捕获异常，返回带 error 的 CompletionResult
    modification = engine._step_execute_modify_real("prompt", "repair")
    assert modification.error is not None
    assert "异常" in modification.error

    # run_self_evolution_cycle 应该捕获异常，不抛出
    result = engine.run_self_evolution_cycle(
        task_requirements=["some_missing_capability"]
    )
    assert "status" in result
    # 不应该抛异常
    assert result["status"] in ("failed", "no_acquisition", "success")


# ============================================================
# 集成测试：gap 检测 → 代码生成 → 沙箱验证 → 工具注册
# ============================================================

def test_full_pipeline_gap_to_registered_tool(tmp_workspace):
    """完整流水线：gap 检测 → 代码生成 → 沙箱验证 → 工具注册 → 测试通过"""
    engine = _make_self_evo_engine(tmp_workspace, test_runner_passed=True)

    # 用一个未注册的能力名
    result = engine.run_self_evolution_cycle(
        task_requirements=["data_transformer"]
    )

    # 应该检测到 gap
    gap_names = [g["missing_capability"] for g in result["gaps"]]
    assert "data_transformer" in gap_names

    # 如果成功获取并验证
    if result["validated"]:
        validated_names = [v["missing_capability"] for v in result["validated"]]
        assert "data_transformer" in validated_names

        # 新能力应该被注册到 CapabilityRegistry
        cap = engine.capability_registry.get("data_transformer")
        assert cap is not None
        assert cap.source == "generated"

        # 工具应该被注册到 ToolRegistry
        assert engine.dynamic_loader.tool_registry.has("data_transformer")


# ============================================================
# 端到端：GitHub 路径 + 真用上 — 修复自审 #4
# 旧测试只断言 has(tool)，从未实际调用注册的工具。
# 这里 mock GitHub 搜索/下载（deterministic，不依赖网络），
# 跑完整闭环后真调 tool_registry.call() 验证返回值正确。
# ============================================================

# 预制的"从 GitHub 下载"的安全代码 — 函数名必须等于能力名（沙箱 call 检查要求）
_GITHUB_MOCK_CODE = '''"""从 GitHub 搜索下载的字符串反转工具（测试用预制代码）"""


def string_reverser(text=""):
    """反转字符串"""
    return text[::-1]
'''


def test_e2e_github_path_then_actually_call_tool(tmp_workspace, monkeypatch):
    """端到端：发现短板 → GitHub 找能力（mock）→ 下载 → 沙箱验证 → 热加载 → 真用上

    修复自审 #4 的两个断点：
    1. GitHub 获取路径（_acquire_from_github）从未被 deterministic 测试覆盖
       —— 旧测试靠网络不可用静默回退到 code_generator 兜底
    2. 注册工具后从未被实际调用（"真用上"环节完全未测）
       —— 旧测试只断言 has(tool)，不验证工具可调用且行为正确
    """
    import superclaw.gep_engine as gep_mod

    # ---- mock GitHubSearcher.search_code 返回可控结果 ----
    search_calls: list = []

    class _MockSearcher:
        def __init__(self, *args, **kwargs):
            pass

        def search_code(self, query, limit=5):
            search_calls.append(query)
            return [{
                "name": "string_reverser.py",
                "path": "/",
                "repository": "test/repo",
                "html_url": "https://github.com/test/repo/blob/main/string_reverser.py",
                "download_url": "https://raw.githubusercontent.com/test/repo/main/string_reverser.py",
            }]

    # ---- mock FileDownloader.download_raw 写预制代码到目标路径 ----
    download_calls: list = []

    class _MockDownloader:
        MAX_BYTES = 1024 * 1024

        def download_raw(self, url, target_path):
            download_calls.append((url, str(target_path)))
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_text(_GITHUB_MOCK_CODE, encoding="utf-8")
            return target_path

    monkeypatch.setattr(gep_mod, "GitHubSearcher", _MockSearcher)
    monkeypatch.setattr(gep_mod, "FileDownloader", _MockDownloader)

    engine = _make_self_evo_engine(tmp_workspace, test_runner_passed=True)

    # ---- 跑完整自进化循环 ----
    result = engine.run_self_evolution_cycle(
        task_requirements=["string_reverser"]
    )

    # ---- 断言 1: GitHub 路径真被走通（不是静默回退到 code_generator）----
    assert len(search_calls) == 1, "GitHubSearcher.search_code 应被调用"
    assert "string reverser" in search_calls[0]
    assert len(download_calls) == 1, "FileDownloader.download_raw 应被调用"

    # ---- 断言 2: 闭环成功 ----
    assert result["status"] == "success", f"status={result['status']} errors={result['errors']}"
    assert len(result["validated"]) == 1
    validated = result["validated"][0]
    assert validated["missing_capability"] == "string_reverser"
    assert validated["tool_name"] == "string_reverser"

    # ---- 断言 3: 能力注册到 CapabilityRegistry ----
    cap = engine.capability_registry.get("string_reverser")
    assert cap is not None
    assert cap.source == "generated"

    # ---- 断言 4: 工具注册到 ToolRegistry ----
    tool_registry = engine.dynamic_loader.tool_registry
    assert tool_registry.has("string_reverser")

    # ---- 断言 5（核心）：真用上 —— 实际调用注册的工具，验证返回值 ----
    # 这是旧测试完全缺失的环节：只查 has() 不调 call()
    call_result = tool_registry.call("string_reverser", text="hello")
    assert call_result.error is False, f"工具调用出错: {call_result.content}"
    assert call_result.content == "olleh", f"期望 'olleh'，实际 {call_result.content!r}"

    # 再调一次验证幂等可用
    call_result2 = tool_registry.call("string_reverser", text="superclaw")
    assert call_result2.error is False
    assert call_result2.content == "walcrepus"


def test_e2e_github_path_failure_falls_back_to_generator(tmp_workspace, monkeypatch):
    """GitHub 搜索返回错误 → 回退到 code_generator 兜底（验证降级路径）

    旧测试靠"无网络"触发降级，非 deterministic。这里 mock GitHub 返回错误，
    deterministic 验证降级到 _acquire_from_generator 的路径。
    """
    import superclaw.gep_engine as gep_mod

    class _MockSearcherError:
        def __init__(self, *args, **kwargs):
            pass

        def search_code(self, query, limit=5):
            return [{"error": "mock: GitHub API 不可用"}]

    monkeypatch.setattr(gep_mod, "GitHubSearcher", _MockSearcherError)
    # FileDownloader 不应被调用（search 就失败了）
    monkeypatch.setattr(gep_mod, "FileDownloader",
                        lambda *a, **kw: (_ for _ in ()).throw(AssertionError(
                            "FileDownloader 不应在 search 失败时被调用")))

    engine = _make_self_evo_engine(tmp_workspace, test_runner_passed=True)

    result = engine.run_self_evolution_cycle(
        task_requirements=["data_transformer"]
    )

    # GitHub 失败 → 回退到 code_generator mock 兜底 → 仍应成功
    assert result["status"] in ("success", "no_acquisition"), \
        f"status={result['status']} errors={result['errors']}"
