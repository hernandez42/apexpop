"""测试 superclaw.gep_engine — GEP 10 步进化循环引擎

目标：把 gep_engine.py 覆盖率从 14% 提升到 85%+
覆盖：SignalExtractor / StrategyManager / GEPEngine 全部公开方法和关键分支
"""
import json
import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from superclaw.gep_engine import GEPEngine, StrategyManager, SignalExtractor
from superclaw.gep_schema import (
    Gene, Capsule, EvolutionEvent, Signal, GeneLibrary, VALID_STRATEGIES,
)
from superclaw.memory import MemoryStore
from superclaw.llm_router import LLMRouter, CompletionResult
import superclaw.gep_engine as _gep_engine_mod


# ============================================================
# Mock LLM — 根据 prompt 内容返回不同响应
# ============================================================

class MockLLMRouter:
    """Mock LLMRouter — 信号提取和修改调用返回不同响应

    - 信号提取 prompt（含 "分析以下进化信号"）→ 返回 signal_content
    - 修改 prompt（含 "你是 superclaw GEP"）→ 返回 content
    """

    def __init__(self, content=None, error=None, tokens=100, latency=10,
                 signal_content=None, signal_error=None):
        self._content = content
        self._error = error
        self._tokens = tokens
        self._latency = latency
        self._signal_content = signal_content
        self._signal_error = signal_error
        self.calls = []

    def complete(self, messages, complexity="medium", provider=None, max_tokens=None):
        self.calls.append({
            "messages": messages, "complexity": complexity,
        })
        prompt = messages[0]["content"] if messages else ""

        if "分析以下进化信号" in prompt or "自进化分析器" in prompt or "冷启动状态" in prompt:
            return CompletionResult(
                content=self._signal_content if self._signal_content is not None else "",
                provider="mock", model="mock",
                tokens_used=50, error=self._signal_error,
            )

        return CompletionResult(
            content=self._content if self._content is not None else
                '{"action": "优化数据库查询逻辑", "target": "db.py", "expected_improvement": "提升性能", "risk_level": "low"}',
            provider="mock", model="mock-model",
            tokens_used=self._tokens, cost=0.0,
            latency_ms=self._latency, error=self._error,
        )

    def status(self):
        return {"providers": {"mock": {"enabled": True, "model": "mock-model"}}}


# ============================================================
# Helpers
# ============================================================

def _add_reflection(memory, phi=0.3, tier=1, fitness=0.4, mutations=1, knowledge=0,
                    health=0, balance=0.5):
    """添加一条反思记录（产生 gaps/problems 信号源）"""
    memory.reflection.reflect({
        "phi": phi, "tier": tier, "fitness": fitness,
        "mutations": mutations, "knowledge": knowledge,
        "health": health, "balance": balance,
    })


def _make_signals():
    return [
        Signal(signal_type="error", source="test", severity="high", pattern="测试错误", context="测试"),
        Signal(signal_type="performance", source="test", severity="medium", pattern="性能问题", context="测试"),
        Signal(signal_type="feature", source="test", severity="low", pattern="新功能需求", context="测试"),
    ]


def _make_engine(tmp_workspace, **kwargs):
    """创建一个隔离的 GEPEngine"""
    memory = kwargs.get("memory") or MemoryStore(tmp_workspace)
    llm = kwargs.get("llm") or MockLLMRouter()
    strategy = kwargs.get("strategy", "balanced")
    return GEPEngine(memory=memory, llm=llm, strategy=strategy, workspace=tmp_workspace)


# ============================================================
# StrategyManager 测试
# ============================================================

def test_strategy_manager_default_is_balanced():
    sm = StrategyManager()
    assert sm.strategy == "balanced"


def test_strategy_manager_invalid_falls_back_to_balanced():
    """无效策略 → balanced（覆盖 183-185）"""
    sm = StrategyManager("not-a-strategy")
    assert sm.strategy == "balanced"


@pytest.mark.parametrize("strategy", VALID_STRATEGIES)
def test_strategy_manager_all_strategies_accepted(strategy):
    sm = StrategyManager(strategy)
    assert sm.strategy == strategy


def test_strategy_ratios_cover_all_strategies():
    for s in VALID_STRATEGIES:
        assert s in StrategyManager.STRATEGY_RATIOS


def test_strategy_repair_only_always_repair():
    """repair-only 策略永远返回 repair（覆盖 205-206）"""
    sm = StrategyManager("repair-only")
    for _ in range(10):
        assert sm.select_category(_make_signals()) == "repair"


def test_select_category_repair_with_error(monkeypatch):
    """r < repair_ratio 且有 error → repair（覆盖 208-211）"""
    monkeypatch.setattr(random, "random", lambda: 0.1)
    sm = StrategyManager("balanced")
    assert sm.select_category([Signal(signal_type="error", pattern="err")]) == "repair"


def test_select_category_optimize_with_perf(monkeypatch):
    """r < repair_ratio，无 error，有 perf → optimize（覆盖 212-213）"""
    monkeypatch.setattr(random, "random", lambda: 0.1)
    sm = StrategyManager("balanced")
    assert sm.select_category([Signal(signal_type="performance", pattern="slow")]) == "optimize"


def test_select_category_repair_default_no_match(monkeypatch):
    """r < repair_ratio，无 error/perf → repair 默认（覆盖 214-215）"""
    monkeypatch.setattr(random, "random", lambda: 0.1)
    sm = StrategyManager("balanced")
    assert sm.select_category([Signal(signal_type="feature", pattern="feat")]) == "repair"


def test_select_category_innovate_with_feature(monkeypatch):
    """r >= repair_ratio，有 feature → innovate（覆盖 218-219）"""
    monkeypatch.setattr(random, "random", lambda: 0.9)
    sm = StrategyManager("balanced")
    assert sm.select_category([Signal(signal_type="feature", pattern="new")]) == "innovate"


def test_select_category_explore_no_feature(monkeypatch):
    """r >= repair_ratio，无 feature → explore（覆盖 220-221）"""
    monkeypatch.setattr(random, "random", lambda: 0.9)
    sm = StrategyManager("balanced")
    assert sm.select_category([Signal(signal_type="error", pattern="err")]) == "explore"


def test_should_solidify_success_high_score():
    """成功 + score > 0.6 → True（覆盖 225-229）"""
    sm = StrategyManager()
    assert sm.should_solidify(Capsule(outcome={"status": "success", "score": 0.8})) is True


def test_should_solidify_failed_status():
    sm = StrategyManager()
    assert sm.should_solidify(Capsule(outcome={"status": "failed", "score": 0.9})) is False


def test_should_solidify_low_score():
    sm = StrategyManager()
    assert sm.should_solidify(Capsule(outcome={"status": "success", "score": 0.5})) is False


def test_should_solidify_exact_threshold():
    """score == 0.6 → 不 > 0.6 → False"""
    sm = StrategyManager()
    assert sm.should_solidify(Capsule(outcome={"status": "success", "score": 0.6})) is False


# ============================================================
# SignalExtractor 测试
# ============================================================

def test_signal_extractor_init(tmp_workspace):
    """覆盖 47-48"""
    memory = MemoryStore(tmp_workspace)
    llm = MockLLMRouter()
    extractor = SignalExtractor(memory, llm)
    assert extractor.memory is memory
    assert extractor.llm is llm


def test_signal_extractor_scan_empty(tmp_workspace):
    """空记忆 → 冷启动逻辑触发 LLM，返回空内容 → 无信号"""
    memory = MemoryStore(tmp_workspace)
    llm = MockLLMRouter()  # cold_start 返回空内容
    extractor = SignalExtractor(memory, llm)
    signals = extractor.scan()
    # 冷启动 LLM 返回空内容 → 不生成信号
    assert signals == []
    # 冷启动仍触发 1 次 LLM 调用（即使返回空）
    assert len(llm.calls) == 1


def test_signal_extractor_scan_from_gaps(tmp_workspace):
    """从反思 gaps 提取 feature 信号（覆盖 54-64）"""
    memory = MemoryStore(tmp_workspace)
    _add_reflection(memory, phi=0.3, tier=1, fitness=0.4, mutations=1, knowledge=0)
    llm = MockLLMRouter(signal_content="[]")
    extractor = SignalExtractor(memory, llm)
    signals = extractor.scan()
    feature_signals = [s for s in signals if s.source == "reflection:gaps"]
    assert len(feature_signals) > 0
    assert all(s.signal_type == "feature" for s in feature_signals)


def test_signal_extractor_scan_from_problems(tmp_workspace):
    """从反思 problems 提取 error 信号（覆盖 65-72）"""
    memory = MemoryStore(tmp_workspace)
    _add_reflection(memory, health=0, balance=0)  # 触发 problems
    llm = MockLLMRouter(signal_content="[]")
    extractor = SignalExtractor(memory, llm)
    signals = extractor.scan()
    error_signals = [s for s in signals if s.source == "reflection:problems"]
    assert len(error_signals) > 0
    assert all(s.signal_type == "error" for s in error_signals)


def test_signal_extractor_scan_from_failed_evolution(tmp_workspace):
    """从进化历史失败记录提取信号（覆盖 74-84）"""
    memory = MemoryStore(tmp_workspace)
    memory.evolution.record(
        cycle=1, phi=0.3, domain="repair", gene_id="g1",
        score=0.2, retained=False, tier=1,
    )
    llm = MockLLMRouter(signal_content="[]")
    extractor = SignalExtractor(memory, llm)
    signals = extractor.scan()
    failed_signals = [s for s in signals if s.source == "evolution:failed"]
    assert len(failed_signals) > 0
    assert all(s.signal_type == "error" for s in failed_signals)


def test_signal_extractor_scan_low_retention(tmp_workspace):
    """保留率 < 0.5 → performance 信号（覆盖 86-95）"""
    memory = MemoryStore(tmp_workspace)
    for i in range(5):
        memory.evolution.record(
            cycle=i + 1, phi=0.3, domain="repair", gene_id=f"g{i}",
            score=0.1, retained=False, tier=1,
        )
    llm = MockLLMRouter(signal_content="[]")
    extractor = SignalExtractor(memory, llm)
    signals = extractor.scan()
    perf_signals = [s for s in signals if s.source == "evolution:stats"]
    assert len(perf_signals) > 0
    assert all(s.signal_type == "performance" for s in perf_signals)


def test_signal_extractor_scan_with_llm(tmp_workspace):
    """有信号时调用 LLM 深度分析（覆盖 97-101）"""
    memory = MemoryStore(tmp_workspace)
    _add_reflection(memory)
    llm = MockLLMRouter(
        signal_content='[{"signal_type":"pattern","severity":"high","pattern":"隐藏模式","context":"LLM"}]'
    )
    extractor = SignalExtractor(memory, llm)
    signals = extractor.scan()
    llm_signals = [s for s in signals if s.source == "llm:analysis"]
    assert len(llm_signals) == 1
    assert llm_signals[0].pattern == "隐藏模式"


def test_llm_extract_signals_success(tmp_workspace):
    """LLM 返回有效 JSON 数组（覆盖 107-153）"""
    memory = MemoryStore(tmp_workspace)
    llm = MockLLMRouter(
        signal_content='[{"signal_type":"pattern","severity":"high","pattern":"p1","context":"c1"}]'
    )
    extractor = SignalExtractor(memory, llm)
    result = extractor._llm_extract_signals([Signal(pattern="x")])
    assert len(result) == 1
    assert result[0].source == "llm:analysis"
    assert result[0].signal_type == "pattern"
    assert result[0].pattern == "p1"


def test_llm_extract_signals_codeblock_json(tmp_workspace):
    """LLM 返回 ```json ...``` 代码块（覆盖 136-139）"""
    memory = MemoryStore(tmp_workspace)
    llm = MockLLMRouter(signal_content='```json\n[{"signal_type":"pattern","pattern":"p"}]\n```')
    extractor = SignalExtractor(memory, llm)
    result = extractor._llm_extract_signals([Signal(pattern="x")])
    assert len(result) == 1
    assert result[0].pattern == "p"


def test_llm_extract_signals_plain_codeblock(tmp_workspace):
    """LLM 返回 ``` ... ``` 代码块（无 json 前缀，覆盖 136 但不进 138）"""
    memory = MemoryStore(tmp_workspace)
    llm = MockLLMRouter(signal_content='```\n[{"signal_type":"pattern","pattern":"p"}]\n```')
    extractor = SignalExtractor(memory, llm)
    result = extractor._llm_extract_signals([Signal(pattern="x")])
    assert len(result) == 1


def test_llm_extract_signals_object_not_array(tmp_workspace):
    """LLM 返回单个对象 → 包装成列表（覆盖 141-142）"""
    memory = MemoryStore(tmp_workspace)
    llm = MockLLMRouter(signal_content='{"signal_type":"pattern","pattern":"p"}')
    extractor = SignalExtractor(memory, llm)
    result = extractor._llm_extract_signals([Signal(pattern="x")])
    assert len(result) == 1


def test_llm_extract_signals_truncates_to_3(tmp_workspace):
    """LLM 返回 5 个 → 只取 3 个（覆盖 152）"""
    memory = MemoryStore(tmp_workspace)
    items = [{"signal_type": "pattern", "pattern": f"p{i}"} for i in range(5)]
    llm = MockLLMRouter(signal_content=json.dumps(items))
    extractor = SignalExtractor(memory, llm)
    result = extractor._llm_extract_signals([Signal(pattern="x")])
    assert len(result) == 3


def test_llm_extract_signals_error(tmp_workspace):
    """LLM 返回 error → 空列表（覆盖 130-131）"""
    memory = MemoryStore(tmp_workspace)
    llm = MockLLMRouter(signal_error="LLM error")
    extractor = SignalExtractor(memory, llm)
    assert extractor._llm_extract_signals([Signal(pattern="x")]) == []


def test_llm_extract_signals_empty_content(tmp_workspace):
    """LLM 返回空内容 → 空列表（覆盖 130-131）"""
    memory = MemoryStore(tmp_workspace)
    llm = MockLLMRouter(signal_content="")
    extractor = SignalExtractor(memory, llm)
    assert extractor._llm_extract_signals([Signal(pattern="x")]) == []


def test_llm_extract_signals_invalid_json(tmp_workspace):
    """LLM 返回非法 JSON → 空列表（覆盖 154-155）"""
    memory = MemoryStore(tmp_workspace)
    llm = MockLLMRouter(signal_content="not json at all")
    extractor = SignalExtractor(memory, llm)
    assert extractor._llm_extract_signals([Signal(pattern="x")]) == []


# ============================================================
# GEPEngine 初始化测试
# ============================================================

def test_engine_init_with_all_params(tmp_workspace):
    """覆盖 257-263"""
    memory = MemoryStore(tmp_workspace)
    llm = MockLLMRouter()
    engine = GEPEngine(memory=memory, llm=llm, strategy="balanced", workspace=tmp_workspace)
    assert engine.workspace == tmp_workspace
    assert engine.memory is memory
    assert engine.llm is llm
    assert engine.strategy_mgr.strategy == "balanced"
    assert engine.cycle_count == 0
    assert engine.library.dir == tmp_workspace / "gep-library"


def test_engine_init_default_memory(tmp_workspace):
    """不传 memory → 自动创建 MemoryStore（覆盖 258）"""
    llm = MockLLMRouter()
    engine = GEPEngine(llm=llm, workspace=tmp_workspace)
    assert engine.memory is not None
    assert engine.memory.root == tmp_workspace


def test_engine_init_default_llm(tmp_workspace, monkeypatch):
    """不传 llm → 调用 get_router()（覆盖 259）"""
    memory = MemoryStore(tmp_workspace)
    mock_router = MockLLMRouter()
    monkeypatch.setattr(_gep_engine_mod, "get_router", lambda: mock_router)
    engine = GEPEngine(memory=memory, workspace=tmp_workspace)
    assert engine.llm is mock_router


def test_engine_init_invalid_strategy(tmp_workspace):
    engine = _make_engine(tmp_workspace, strategy="bogus")
    assert engine.strategy_mgr.strategy == "balanced"


# ============================================================
# GEPEngine 单步测试
# ============================================================

def test_step_scan_logs(tmp_workspace):
    """覆盖 346-350"""
    engine = _make_engine(tmp_workspace)
    result = engine._step_scan_logs()
    assert "knowledge_files" in result
    assert "evolution_cycles" in result
    assert "reflections" in result
    assert "gene_library" in result


def test_step_extract_signals(tmp_workspace):
    """覆盖 359"""
    memory = MemoryStore(tmp_workspace)
    _add_reflection(memory)
    engine = GEPEngine(memory=memory, llm=MockLLMRouter(signal_content="[]"), workspace=tmp_workspace)
    signals = engine._step_extract_signals()
    assert len(signals) > 0


def test_step_select_gene_new(tmp_workspace):
    """GeneLibrary 无匹配 → 创建新 Gene（覆盖 372-378）"""
    engine = _make_engine(tmp_workspace)
    category, gene = engine._step_select_gene(_make_signals())
    assert gene is not None
    assert gene.id.startswith("gene-")
    assert gene.category == category


def test_step_select_gene_matching_existing(tmp_workspace):
    """GeneLibrary 有匹配 → 返回已有 Gene（覆盖 366-370）"""
    engine = _make_engine(tmp_workspace)
    existing = Gene(category="repair", signals_match=["测试错误"], strategy=["s"], summary="已有")
    engine.library.upsert_gene(existing)
    signals = [Signal(signal_type="error", pattern="测试错误")]
    category, gene = engine._step_select_gene(signals)
    assert gene.id == existing.id


def test_step_generate_prompt_with_gene(tmp_workspace):
    """覆盖 383-417（有 gene 分支）"""
    engine = _make_engine(tmp_workspace)
    gene = Gene(category="repair", signals_match=["err"], summary="test gene")
    prompt = engine._step_generate_prompt(_make_signals(), "repair", gene)
    assert "GEP 进化引擎" in prompt
    assert "repair" in prompt
    assert gene.id in prompt
    assert len(prompt) > 0


def test_step_generate_prompt_without_gene(tmp_workspace):
    """覆盖 383-417（无 gene 分支）"""
    engine = _make_engine(tmp_workspace)
    prompt = engine._step_generate_prompt(_make_signals(), "innovate", None)
    assert "innovate" in prompt
    assert "GEP 进化引擎" in prompt


def test_step_execute_modify_repair(tmp_workspace):
    """repair → complexity=low（覆盖 423-429）"""
    llm = MockLLMRouter(content='{"action": "fix bug"}')
    engine = _make_engine(tmp_workspace, llm=llm)
    result = engine._step_execute_modify("prompt", "repair")
    assert result.provider == "mock"
    assert result.error is None
    assert llm.calls[0]["complexity"] == "low"


def test_step_execute_modify_non_repair(tmp_workspace):
    """非 repair → complexity=medium（覆盖 423）"""
    llm = MockLLMRouter(content='{"action": "fix bug"}')
    engine = _make_engine(tmp_workspace, llm=llm)
    engine._step_execute_modify("prompt", "innovate")
    assert llm.calls[0]["complexity"] == "medium"


def test_step_validate_error(tmp_workspace):
    """LLM error → 验证失败（覆盖 434-439）"""
    engine = _make_engine(tmp_workspace)
    mod = CompletionResult(content="", provider="mock", model="mock", error="LLM 失败")
    result = engine._step_validate(mod, "repair")
    assert result["passed"] is False
    assert "LLM 错误" in result["reason"]
    assert result["score"] == 0.0


def test_step_validate_short_content(tmp_workspace):
    """内容过短 → 验证失败（覆盖 441-446）"""
    engine = _make_engine(tmp_workspace)
    mod = CompletionResult(content="short", provider="mock", model="mock", tokens_used=10)
    result = engine._step_validate(mod, "repair")
    assert result["passed"] is False
    assert "过短" in result["reason"]


def test_step_validate_valid_json_action(tmp_workspace):
    """有效 JSON + action > 5 + tokens > 50 → score=0.8（覆盖 448-475）"""
    engine = _make_engine(tmp_workspace)
    mod = CompletionResult(
        content='{"action": "优化数据库查询", "target": "db.py"}',
        provider="mock", model="mock", tokens_used=100,
    )
    result = engine._step_validate(mod, "repair")
    assert result["passed"] is True
    assert result["score"] == pytest.approx(0.8)  # 0.7 + 0.1
    assert result["action"] == "优化数据库查询"


def test_step_validate_json_codeblock_json(tmp_workspace):
    """```json 代码块（覆盖 452-455）"""
    engine = _make_engine(tmp_workspace)
    mod = CompletionResult(
        content='```json\n{"action": "修复数据库问题"}\n```',
        provider="mock", model="mock", tokens_used=30,
    )
    result = engine._step_validate(mod, "repair")
    assert result["passed"] is True
    assert result["action"] == "修复数据库问题"
    assert result["score"] == pytest.approx(0.7)  # action>5, tokens<=50


def test_step_validate_json_plain_codeblock(tmp_workspace):
    """``` 代码块（无 json 前缀）"""
    engine = _make_engine(tmp_workspace)
    mod = CompletionResult(
        content='```\n{"action": "修复数据库问题"}\n```',
        provider="mock", model="mock", tokens_used=30,
    )
    result = engine._step_validate(mod, "repair")
    assert result["passed"] is True
    assert result["action"] == "修复数据库问题"


def test_step_validate_invalid_json_low_tokens(tmp_workspace):
    """无效 JSON + tokens <= 50 → score=0.5（覆盖 458-459, 462-465）"""
    engine = _make_engine(tmp_workspace)
    mod = CompletionResult(
        content="this is not json but long enough to pass",
        provider="mock", model="mock", tokens_used=30,
    )
    result = engine._step_validate(mod, "repair")
    assert result["passed"] is True
    assert result["score"] == 0.5
    assert result["action"] is None


def test_step_validate_invalid_json_high_tokens(tmp_workspace):
    """无效 JSON + tokens > 50 → score=0.6"""
    engine = _make_engine(tmp_workspace)
    mod = CompletionResult(
        content="this is not json but long enough to pass",
        provider="mock", model="mock", tokens_used=100,
    )
    result = engine._step_validate(mod, "repair")
    assert result["score"] == 0.6  # 0.5 + 0.1


def test_step_solidify_validation_failed(tmp_workspace):
    """验证未通过 → None（覆盖 482-483）"""
    engine = _make_engine(tmp_workspace)
    mod = CompletionResult(content="content", provider="mock", model="mock")
    validation = {"passed": False, "score": 0.0}
    assert engine._step_solidify(_make_signals(), "repair", mod, validation, None) is None


def test_step_solidify_success_with_tokens(tmp_workspace):
    """成功 + score > 0.6 + tokens > 0 → 固化（覆盖 485-513）"""
    engine = _make_engine(tmp_workspace)
    mod = CompletionResult(content="content here", provider="mock", model="mock", tokens_used=100)
    validation = {"passed": True, "score": 0.8, "action": "优化"}
    gene = Gene(category="repair", summary="test")
    result = engine._step_solidify(_make_signals(), "repair", mod, validation, gene)
    assert result is not None
    assert result.outcome["status"] == "success"
    assert result.gene == gene.id
    assert result.confidence == 0.8
    assert result.derivation_tokens is not None  # tokens > 0
    assert result.content == "content here"


def test_step_solidify_success_no_tokens(tmp_workspace):
    """tokens_used=0 → derivation_tokens=None（覆盖 500）"""
    engine = _make_engine(tmp_workspace)
    mod = CompletionResult(content="content", provider="mock", model="mock", tokens_used=0)
    validation = {"passed": True, "score": 0.8, "action": "action"}
    result = engine._step_solidify(_make_signals(), "repair", mod, validation, None)
    assert result is not None
    assert result.derivation_tokens is None


def test_step_solidify_low_score_not_solidified(tmp_workspace):
    """score <= 0.6 → should_solidify False → None（覆盖 511-513）"""
    engine = _make_engine(tmp_workspace)
    mod = CompletionResult(content="content", provider="mock", model="mock", tokens_used=0)
    validation = {"passed": True, "score": 0.5, "action": ""}
    assert engine._step_solidify(_make_signals(), "repair", mod, validation, None) is None


def test_step_publish_nothing(tmp_workspace):
    """无 gene 无 capsule → False（覆盖 522-540）"""
    engine = _make_engine(tmp_workspace)
    assert engine._step_publish(None, None, "repair") is False


def test_step_publish_gene_only(tmp_workspace):
    """只有 gene → True，gene 保存"""
    engine = _make_engine(tmp_workspace)
    gene = Gene(category="repair", summary="test")
    assert engine._step_publish(None, gene, "repair") is True
    assert len(engine.library.load_genes()) == 1


def test_step_publish_gene_and_capsule(tmp_workspace):
    """gene + capsule → True，都保存"""
    engine = _make_engine(tmp_workspace)
    gene = Gene(category="repair", summary="test")
    capsule = Capsule(gene=gene.id, outcome={"status": "success", "score": 0.8}, summary="cap")
    assert engine._step_publish(capsule, gene, "repair") is True
    assert len(engine.library.load_genes()) == 1
    assert len(engine.library.load_capsules()) == 1


def test_step_publish_capsule_only(tmp_workspace):
    """只有 capsule → True，capsule 保存"""
    engine = _make_engine(tmp_workspace)
    capsule = Capsule(outcome={"status": "success", "score": 0.8}, summary="cap")
    assert engine._step_publish(capsule, None, "repair") is True
    assert len(engine.library.load_capsules()) == 1


def test_step_log_event_repair(tmp_workspace):
    """覆盖 548-592（repair 类别）"""
    engine = _make_engine(tmp_workspace)
    engine.cycle_count = 1
    capsule = Capsule(gene="gene-test", outcome={"status": "success", "score": 0.8}, summary="test")
    validation = {"passed": True, "score": 0.8, "reason": "通过"}
    event = engine._step_log_event("cycle-1", "repair", _make_signals(), capsule, validation)
    assert event is not None
    assert event.event_id.startswith("evt-")
    assert event.event_type == "repair"
    assert event.gene_id == "gene-test"
    assert event.success is True
    # 验证事件被记录
    assert len(engine.library.load_events(10)) >= 1
    # 验证进化历史被记录
    assert len(engine.memory.evolution.recent(10)) >= 1


def test_step_log_event_innovation_category(tmp_workspace):
    """innovate/explore → event_type=innovation"""
    engine = _make_engine(tmp_workspace)
    engine.cycle_count = 1
    validation = {"passed": False, "score": 0.3, "reason": "失败"}
    event = engine._step_log_event("cycle-1", "innovate", _make_signals(), None, validation)
    assert event.event_type == "innovation"
    assert event.success is False
    assert event.gene_id is None


def test_step_log_event_explore_category(tmp_workspace):
    """explore → event_type=innovation"""
    engine = _make_engine(tmp_workspace)
    engine.cycle_count = 1
    validation = {"passed": True, "score": 0.7, "reason": "通过"}
    event = engine._step_log_event("cycle-1", "explore", _make_signals(), None, validation)
    assert event.event_type == "innovation"


def test_step_log_event_empty_signals(tmp_workspace):
    """空信号 → trigger_signal=''"""
    engine = _make_engine(tmp_workspace)
    engine.cycle_count = 1
    validation = {"passed": False, "score": 0.0, "reason": "无"}
    event = engine._step_log_event("cycle-1", "repair", [], None, validation)
    assert event.trigger_signal == ""


def test_step_return_monitor(tmp_workspace):
    """覆盖 596-605"""
    engine = _make_engine(tmp_workspace)
    engine.cycle_count = 5
    monitor = engine._step_return_monitor()
    assert monitor["cycle"] == 5
    assert "gene_library" in monitor
    assert "llm_status" in monitor
    assert "memory_status" in monitor


def test_get_apex_state_no_file(tmp_workspace):
    """无 apex-state.json → 默认值（覆盖 617）"""
    engine = _make_engine(tmp_workspace)
    assert engine._get_apex_state() == {"phi": 0, "tier": 1}


def test_get_apex_state_with_file(tmp_workspace):
    """有 apex-state.json → 读取（覆盖 609-614）"""
    engine = _make_engine(tmp_workspace)
    apex_dir = tmp_workspace / "apex-state"
    apex_dir.mkdir(parents=True, exist_ok=True)
    (apex_dir / "apex-state.json").write_text(
        json.dumps({"current": {"phi": 1.5, "tier": 3}}), encoding="utf-8",
    )
    assert engine._get_apex_state() == {"phi": 1.5, "tier": 3}


def test_get_apex_state_corrupt_file(tmp_workspace):
    """损坏的 apex-state.json → 默认值（覆盖 615-616）"""
    engine = _make_engine(tmp_workspace)
    apex_dir = tmp_workspace / "apex-state"
    apex_dir.mkdir(parents=True, exist_ok=True)
    (apex_dir / "apex-state.json").write_text("{not valid json", encoding="utf-8")
    assert engine._get_apex_state() == {"phi": 0, "tier": 1}


# ============================================================
# GEPEngine 完整循环测试
# ============================================================

def test_run_cycle_success(tmp_workspace):
    """完整 10 步成功循环（覆盖 267-340）"""
    memory = MemoryStore(tmp_workspace)
    _add_reflection(memory)
    llm = MockLLMRouter(signal_content="[]")
    engine = GEPEngine(memory=memory, llm=llm, workspace=tmp_workspace)

    result = engine.run_cycle()

    assert result["cycle"] == 1
    assert result["cycle_id"] == "cycle-1"
    assert "timestamp" in result
    assert result["strategy"] == "balanced"
    assert "steps" in result

    steps = result["steps"]
    for s in ["1_scan_logs", "2_extract_signals", "3_select_gene",
              "4_generate_prompt", "5_execute_modify", "6_validate",
              "7_solidify", "8_publish", "9_log_event", "10_monitor"]:
        assert s in steps

    assert result["status"] in ("success", "failed")


def test_run_cycle_no_signals(tmp_workspace):
    """无信号 → 提前返回 no_signals（覆盖 287-289）"""
    engine = _make_engine(tmp_workspace)
    result = engine.run_cycle()
    assert result["status"] == "no_signals"
    assert "1_scan_logs" in result["steps"]
    assert "2_extract_signals" in result["steps"]
    assert "3_select_gene" not in result["steps"]


def test_run_cycle_validation_failed(tmp_workspace):
    """LLM 错误 → 验证失败"""
    memory = MemoryStore(tmp_workspace)
    _add_reflection(memory)
    llm = MockLLMRouter(error="LLM error", signal_content="[]")
    engine = GEPEngine(memory=memory, llm=llm, workspace=tmp_workspace)

    result = engine.run_cycle()

    assert result["status"] == "failed"
    assert result["steps"]["6_validate"]["passed"] is False
    assert result["steps"]["7_solidify"]["solidified"] is False


def test_run_cycle_solidified(tmp_workspace):
    """成功固化"""
    memory = MemoryStore(tmp_workspace)
    _add_reflection(memory)
    llm = MockLLMRouter(
        content='{"action": "优化数据库查询逻辑", "target": "db.py"}',
        tokens=100, signal_content="[]",
    )
    engine = GEPEngine(memory=memory, llm=llm, workspace=tmp_workspace)

    result = engine.run_cycle()

    assert result["status"] == "success"
    assert result["steps"]["6_validate"]["passed"] is True
    assert result["steps"]["7_solidify"]["solidified"] is True
    assert result["steps"]["7_solidify"]["capsule_id"] is not None
    assert result["steps"]["8_publish"]["published"] is True


def test_run_cycle_not_solidified(tmp_workspace):
    """验证通过但 score 低 → 不固化"""
    memory = MemoryStore(tmp_workspace)
    _add_reflection(memory)
    llm = MockLLMRouter(
        content="this is a long enough response but not json",
        tokens=10, signal_content="[]",
    )
    engine = GEPEngine(memory=memory, llm=llm, workspace=tmp_workspace)

    result = engine.run_cycle()

    assert result["steps"]["6_validate"]["passed"] is True
    assert result["steps"]["6_validate"]["score"] == 0.5
    assert result["steps"]["7_solidify"]["solidified"] is False
    assert result["steps"]["7_solidify"]["capsule_id"] is None
    # Gene 仍然发布
    assert result["steps"]["8_publish"]["published"] is True


def test_run_cycle_gene_saved(tmp_workspace):
    """Gene 被保存到 GeneLibrary"""
    memory = MemoryStore(tmp_workspace)
    _add_reflection(memory)
    llm = MockLLMRouter(signal_content="[]")
    engine = GEPEngine(memory=memory, llm=llm, workspace=tmp_workspace)

    engine.run_cycle()

    genes = engine.library.load_genes()
    assert len(genes) >= 1
    assert len(genes[0].learning_history) >= 1


def test_run_cycle_capsule_saved_on_solidify(tmp_workspace):
    """固化成功 → Capsule 保存"""
    memory = MemoryStore(tmp_workspace)
    _add_reflection(memory)
    llm = MockLLMRouter(
        content='{"action": "优化数据库查询逻辑"}',
        tokens=100, signal_content="[]",
    )
    engine = GEPEngine(memory=memory, llm=llm, workspace=tmp_workspace)

    engine.run_cycle()

    assert len(engine.library.load_capsules()) >= 1


def test_run_cycle_capsule_not_saved_on_fail(tmp_workspace):
    """固化失败 → Capsule 不保存"""
    memory = MemoryStore(tmp_workspace)
    _add_reflection(memory)
    llm = MockLLMRouter(error="err", signal_content="[]")
    engine = GEPEngine(memory=memory, llm=llm, workspace=tmp_workspace)

    engine.run_cycle()

    assert len(engine.library.load_capsules()) == 0


def test_run_cycle_event_recorded(tmp_workspace):
    """EvolutionEvent 被记录到 MemoryStore"""
    memory = MemoryStore(tmp_workspace)
    _add_reflection(memory)
    llm = MockLLMRouter(signal_content="[]")
    engine = GEPEngine(memory=memory, llm=llm, workspace=tmp_workspace)

    result = engine.run_cycle()

    assert len(engine.library.load_events(10)) >= 1
    assert result["steps"]["9_log_event"]["event_id"] is not None
    assert len(memory.evolution.recent(10)) >= 1


# ============================================================
# GEPEngine 多轮循环测试（覆盖 run 方法 621-646）
# ============================================================

def test_run_multiple_cycles_quiet(tmp_workspace):
    memory = MemoryStore(tmp_workspace)
    _add_reflection(memory)
    llm = MockLLMRouter(signal_content="[]")
    engine = GEPEngine(memory=memory, llm=llm, workspace=tmp_workspace)

    results = engine.run(cycles=3, verbose=False)

    assert len(results) == 3
    assert engine.cycle_count == 3


def test_run_multiple_cycles_verbose(tmp_workspace, capsys):
    memory = MemoryStore(tmp_workspace)
    _add_reflection(memory)
    llm = MockLLMRouter(signal_content="[]")
    engine = GEPEngine(memory=memory, llm=llm, workspace=tmp_workspace)

    results = engine.run(cycles=2, verbose=True)

    assert len(results) == 2
    captured = capsys.readouterr()
    assert "GEP 进化循环" in captured.out
    # 有信号时所有 step 都打印
    assert "信号:" in captured.out
    assert "类别:" in captured.out
    assert "验证:" in captured.out
    assert "固化:" in captured.out


def test_run_verbose_no_signals(tmp_workspace, capsys):
    """无信号时 verbose 输出（覆盖 635-636 但不进 637-642 的其他分支）"""
    engine = _make_engine(tmp_workspace)
    engine.run(cycles=1, verbose=True)
    captured = capsys.readouterr()
    assert "GEP 进化循环" in captured.out
    assert "❌" in captured.out  # no_signals → ❌


def test_state_accumulation_across_cycles(tmp_workspace):
    """连续 3 轮 → 状态累积"""
    memory = MemoryStore(tmp_workspace)
    _add_reflection(memory)
    llm = MockLLMRouter(signal_content="[]")
    engine = GEPEngine(memory=memory, llm=llm, workspace=tmp_workspace)

    for _ in range(3):
        engine.run_cycle()

    assert len(engine.library.load_events(100)) >= 3
    assert len(memory.evolution.recent(100)) >= 3
    genes = engine.library.load_genes()
    assert len(genes) >= 1
    assert len(genes[0].learning_history) >= 3


def test_learning_history_grows(tmp_workspace):
    """learning_history 随循环增长"""
    memory = MemoryStore(tmp_workspace)
    _add_reflection(memory)
    llm = MockLLMRouter(signal_content="[]")
    engine = GEPEngine(memory=memory, llm=llm, workspace=tmp_workspace)

    engine.run_cycle()
    genes_1 = engine.library.load_genes()
    lh_1 = len(genes_1[0].learning_history) if genes_1 else 0

    engine.run_cycle()
    genes_2 = engine.library.load_genes()
    lh_2 = len(genes_2[0].learning_history) if genes_2 else 0

    assert lh_2 > lh_1


# ============================================================
# 异常分支和策略测试
# ============================================================

def test_empty_library_select_gene(tmp_workspace):
    """GeneLibrary 为空 → 创建新 Gene"""
    engine = _make_engine(tmp_workspace)
    category, gene = engine._step_select_gene(_make_signals())
    assert gene is not None
    assert gene.id.startswith("gene-")


def test_llm_failure_validation_failed(tmp_workspace):
    """LLM 调用失败 → 验证失败 → 不固化"""
    memory = MemoryStore(tmp_workspace)
    _add_reflection(memory)
    llm = MockLLMRouter(error="connection failed", signal_content="[]")
    engine = GEPEngine(memory=memory, llm=llm, workspace=tmp_workspace)
    result = engine.run_cycle()
    assert result["status"] == "failed"
    assert result["steps"]["5_execute_modify"]["error"] is not None


def test_strategy_innovate_ratio(monkeypatch):
    """innovate 策略 → 30% repair / 70% innovate"""
    monkeypatch.setattr(random, "random", lambda: 0.5)
    sm = StrategyManager("innovate")
    # r=0.5 >= 0.3 → innovate branch
    assert sm.select_category([Signal(signal_type="feature", pattern="feat")]) == "innovate"


def test_strategy_harden_ratio(monkeypatch):
    """harden 策略 → 90% repair"""
    monkeypatch.setattr(random, "random", lambda: 0.85)
    sm = StrategyManager("harden")
    # r=0.85 < 0.9 → repair branch
    assert sm.select_category([Signal(signal_type="error", pattern="err")]) == "repair"


def test_strategy_early_stabilize_ratio(monkeypatch):
    """early-stabilize 策略 → 80% repair"""
    monkeypatch.setattr(random, "random", lambda: 0.75)
    sm = StrategyManager("early-stabilize")
    # r=0.75 < 0.8 → repair branch
    assert sm.select_category([Signal(signal_type="error", pattern="err")]) == "repair"


def test_strategy_steady_state_ratio(monkeypatch):
    """steady-state 策略 → 60% repair"""
    monkeypatch.setattr(random, "random", lambda: 0.55)
    sm = StrategyManager("steady-state")
    # r=0.55 < 0.6 → repair branch
    assert sm.select_category([Signal(signal_type="error", pattern="err")]) == "repair"


def test_strategy_auto_default_balanced(monkeypatch):
    """auto 策略默认 balanced"""
    monkeypatch.setattr(random, "random", lambda: 0.1)
    sm = StrategyManager("auto")
    assert sm.select_category([Signal(signal_type="error", pattern="err")]) == "repair"
