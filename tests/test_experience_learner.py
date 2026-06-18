"""测试 superclaw.experience_learner — 从经验学习

覆盖：
- StrategyOutcome：to_dict/from_dict 往返、默认值、边界值
- ExperienceStore：record+recent、query_by_strategy/category、空文件、损坏行跳过
- ExperienceAnalyzer：analyze_strategy 空数据、analyze_all、best/worst_strategy、min_samples
- AdaptiveWeights：无数据用默认、样本不足不调整、高成功率增加、低成功率降低、
  trend 影响、clamp 边界、adjustment_report
- ExperienceLearner：record+adjusted_weights、report、recent_outcomes、best_strategy
- gep_engine 集成：StrategyManager 带 experience_learner、_current_ratios、
  GEPEngine 带 experience_learner、run_cycle 记录、run_experience_driven_adjustment
- 向后兼容：无 experience_learner 时原逻辑不变
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from superclaw.experience_learner import (
    StrategyOutcome, ExperienceStore,
    ExperienceAnalyzer, AdaptiveWeights, ExperienceLearner,
    DEFAULT_WEIGHTS, MAX_ADJUSTMENT,
)
from superclaw.gep_schema import Signal
from superclaw.gep_engine import StrategyManager, GEPEngine
from superclaw.memory import MemoryStore
from superclaw.llm_router import CompletionResult


# ============================================================
# Mock LLM — 经验学习测试用
# ============================================================

class ExpMockLLM:
    """经验学习测试用 Mock LLM

    - 信号提取 prompt（含 "分析以下进化信号"）→ 返回空 JSON 数组
    - 其他 prompt → 返回 JSON action
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

        # 默认返回有效 JSON action
        return CompletionResult(
            content='{"action": "fix the bug", "target": "core", "risk_level": "low"}',
            provider="mock", model="mock",
            tokens_used=50, error=None,
        )

    def status(self):
        return {"provider": "mock", "available": True}


def _add_reflection(memory, phi=0.3, tier=1, fitness=0.4, mutations=1, knowledge=0,
                    health=0, balance=0.5):
    """添加一条反思记录（产生 gaps/problems 信号源，让 scan 能提取到信号）"""
    memory.reflection.reflect({
        "phi": phi, "tier": tier, "fitness": fitness,
        "mutations": mutations, "knowledge": knowledge,
        "health": health, "balance": balance,
    })


# ============================================================
# StrategyOutcome 测试
# ============================================================

class TestStrategyOutcome:
    """StrategyOutcome dataclass 测试"""

    def test_to_dict_roundtrip(self):
        """to_dict → from_dict 往返保持数据"""
        o = StrategyOutcome(
            strategy="balanced", category="repair",
            score=0.85, retained=True,
            timestamp="2026-01-01T00:00:00",
            cycle=5, signal_count=3,
        )
        d = o.to_dict()
        assert d["strategy"] == "balanced"
        assert d["category"] == "repair"
        assert d["score"] == 0.85
        assert d["retained"] is True
        assert d["cycle"] == 5
        assert d["signal_count"] == 3

        o2 = StrategyOutcome.from_dict(d)
        assert o2.strategy == o.strategy
        assert o2.category == o.category
        assert o2.score == o.score
        assert o2.retained == o.retained
        assert o2.cycle == o.cycle
        assert o2.signal_count == o.signal_count

    def test_from_dict_defaults(self):
        """from_dict 缺失字段用默认值"""
        o = StrategyOutcome.from_dict({"strategy": "innovate"})
        assert o.strategy == "innovate"
        assert o.category == "repair"
        assert o.score == 0.0
        assert o.retained is False
        assert o.cycle == 0
        assert o.signal_count == 0

    def test_to_dict_score_rounded(self):
        """to_dict 对 score 做 4 位小数舍入"""
        o = StrategyOutcome(
            strategy="balanced", category="repair",
            score=0.123456789, retained=True,
        )
        d = o.to_dict()
        assert d["score"] == 0.1235

    def test_timestamp_auto_filled(self):
        """record 时 timestamp 为空会被自动填充"""
        o = StrategyOutcome(
            strategy="balanced", category="repair",
            score=0.5, retained=False,
        )
        assert o.timestamp == ""  # 创建时为空
        # record 时会填充（在 ExperienceStore 测试中验证）


# ============================================================
# ExperienceStore 测试
# ============================================================

class TestExperienceStore:
    """ExperienceStore 持久化测试"""

    def test_record_and_recent(self, tmp_path):
        """record 后 recent 能读到"""
        store = ExperienceStore(tmp_path / "exp.jsonl")
        o1 = StrategyOutcome("balanced", "repair", 0.8, True, cycle=1)
        o2 = StrategyOutcome("innovate", "explore", 0.4, False, cycle=2)
        store.record(o1)
        store.record(o2)

        recent = store.recent()
        assert len(recent) == 2
        assert recent[0].strategy == "balanced"
        assert recent[1].strategy == "innovate"
        # timestamp 被自动填充
        assert recent[0].timestamp != ""

    def test_recent_limit(self, tmp_path):
        """recent(limit) 限制返回数量"""
        store = ExperienceStore(tmp_path / "exp.jsonl")
        for i in range(10):
            store.record(StrategyOutcome("balanced", "repair", 0.5, True, cycle=i))
        recent = store.recent(3)
        assert len(recent) == 3
        # 返回最近 3 条（cycle 7,8,9）
        assert recent[-1].cycle == 9

    def test_recent_empty_file(self, tmp_path):
        """文件不存在时 recent 返回空列表"""
        store = ExperienceStore(tmp_path / "nonexistent.jsonl")
        assert store.recent() == []

    def test_recent_skips_corrupt_lines(self, tmp_path):
        """损坏的 JSON 行被跳过"""
        log_path = tmp_path / "exp.jsonl"
        log_path.write_text(
            '{"strategy": "balanced", "category": "repair", "score": 0.8, "retained": true}\n'
            'CORRUPT LINE\n'
            '{"strategy": "innovate", "category": "explore", "score": 0.4, "retained": false}\n',
            encoding="utf-8",
        )
        store = ExperienceStore(log_path)
        recent = store.recent()
        assert len(recent) == 2
        assert recent[0].strategy == "balanced"
        assert recent[1].strategy == "innovate"

    def test_query_by_strategy(self, tmp_path):
        """query_by_strategy 过滤策略"""
        store = ExperienceStore(tmp_path / "exp.jsonl")
        store.record(StrategyOutcome("balanced", "repair", 0.8, True, cycle=1))
        store.record(StrategyOutcome("innovate", "explore", 0.4, False, cycle=2))
        store.record(StrategyOutcome("balanced", "optimize", 0.6, True, cycle=3))

        balanced = store.query_by_strategy("balanced")
        assert len(balanced) == 2
        assert all(o.strategy == "balanced" for o in balanced)

        innovate = store.query_by_strategy("innovate")
        assert len(innovate) == 1

        nonexistent = store.query_by_strategy("harden")
        assert len(nonexistent) == 0

    def test_query_by_category(self, tmp_path):
        """query_by_category 过滤类别"""
        store = ExperienceStore(tmp_path / "exp.jsonl")
        store.record(StrategyOutcome("balanced", "repair", 0.8, True, cycle=1))
        store.record(StrategyOutcome("balanced", "explore", 0.4, False, cycle=2))
        store.record(StrategyOutcome("balanced", "repair", 0.6, True, cycle=3))

        repair = store.query_by_category("repair")
        assert len(repair) == 2
        assert all(o.category == "repair" for o in repair)

    def test_creates_parent_dir(self, tmp_path):
        """record 自动创建父目录"""
        store = ExperienceStore(tmp_path / "subdir" / "deep" / "exp.jsonl")
        store.record(StrategyOutcome("balanced", "repair", 0.5, True, cycle=1))
        assert store.log_path.exists()
        assert store.recent()[0].strategy == "balanced"


# ============================================================
# ExperienceAnalyzer 测试
# ============================================================

class TestExperienceAnalyzer:
    """ExperienceAnalyzer 分析测试"""

    def test_analyze_strategy_empty(self, tmp_path):
        """无数据时返回空统计"""
        store = ExperienceStore(tmp_path / "exp.jsonl")
        analyzer = ExperienceAnalyzer(store)
        stats = analyzer.analyze_strategy("balanced")
        assert stats.strategy == "balanced"
        assert stats.attempts == 0
        assert stats.successes == 0
        assert stats.success_rate == 0.0
        assert stats.avg_score == 0.0

    def test_analyze_strategy_with_data(self, tmp_path):
        """有数据时正确统计"""
        store = ExperienceStore(tmp_path / "exp.jsonl")
        # 3 次成功，2 次失败
        store.record(StrategyOutcome("balanced", "repair", 0.9, True, cycle=1))
        store.record(StrategyOutcome("balanced", "repair", 0.8, True, cycle=2))
        store.record(StrategyOutcome("balanced", "repair", 0.7, True, cycle=3))
        store.record(StrategyOutcome("balanced", "repair", 0.2, False, cycle=4))
        store.record(StrategyOutcome("balanced", "repair", 0.1, False, cycle=5))

        analyzer = ExperienceAnalyzer(store)
        stats = analyzer.analyze_strategy("balanced")
        assert stats.attempts == 5
        assert stats.successes == 3
        assert stats.success_rate == 0.6
        assert abs(stats.avg_score - 0.54) < 0.01  # (0.9+0.8+0.7+0.2+0.1)/5

    def test_recent_trend(self, tmp_path):
        """recent_trend = 最近5次平均 - 整体平均"""
        store = ExperienceStore(tmp_path / "exp.jsonl")
        # 前 5 次低分，后 5 次高分 → trend 应为正
        for i in range(5):
            store.record(StrategyOutcome("balanced", "repair", 0.2, False, cycle=i))
        for i in range(5):
            store.record(StrategyOutcome("balanced", "repair", 0.9, True, cycle=i + 5))

        analyzer = ExperienceAnalyzer(store)
        stats = analyzer.analyze_strategy("balanced")
        assert stats.attempts == 10
        # 整体平均 = (0.2*5 + 0.9*5)/10 = 0.55
        assert abs(stats.avg_score - 0.55) < 0.01
        # 最近 5 次平均 = 0.9
        # trend = 0.9 - 0.55 = 0.35
        assert abs(stats.recent_trend - 0.35) < 0.01

    def test_analyze_all(self, tmp_path):
        """analyze_all 返回所有策略的统计"""
        store = ExperienceStore(tmp_path / "exp.jsonl")
        store.record(StrategyOutcome("balanced", "repair", 0.8, True, cycle=1))
        store.record(StrategyOutcome("innovate", "explore", 0.4, False, cycle=2))

        analyzer = ExperienceAnalyzer(store)
        all_stats = analyzer.analyze_all()
        assert "balanced" in all_stats
        assert "innovate" in all_stats
        assert all_stats["balanced"].attempts == 1
        assert all_stats["innovate"].attempts == 1

    def test_best_strategy(self, tmp_path):
        """best_strategy 返回成功率最高的策略"""
        store = ExperienceStore(tmp_path / "exp.jsonl")
        # balanced: 4/5 成功
        for i in range(4):
            store.record(StrategyOutcome("balanced", "repair", 0.8, True, cycle=i))
        store.record(StrategyOutcome("balanced", "repair", 0.2, False, cycle=4))
        # innovate: 1/5 成功
        store.record(StrategyOutcome("innovate", "explore", 0.9, True, cycle=0))
        for i in range(4):
            store.record(StrategyOutcome("innovate", "explore", 0.2, False, cycle=i + 1))

        analyzer = ExperienceAnalyzer(store)
        best = analyzer.best_strategy(min_samples=5)
        assert best == "balanced"

    def test_best_strategy_insufficient_samples(self, tmp_path):
        """样本不足时 best_strategy 返回 None"""
        store = ExperienceStore(tmp_path / "exp.jsonl")
        store.record(StrategyOutcome("balanced", "repair", 0.9, True, cycle=1))
        store.record(StrategyOutcome("balanced", "repair", 0.9, True, cycle=2))

        analyzer = ExperienceAnalyzer(store)
        # 只有 2 个样本，min_samples=5 → None
        assert analyzer.best_strategy(min_samples=5) is None
        # min_samples=2 → 返回 balanced
        assert analyzer.best_strategy(min_samples=2) == "balanced"

    def test_worst_strategy(self, tmp_path):
        """worst_strategy 返回成功率最低的策略"""
        store = ExperienceStore(tmp_path / "exp.jsonl")
        for i in range(5):
            store.record(StrategyOutcome("balanced", "repair", 0.8, True, cycle=i))
        for i in range(5):
            store.record(StrategyOutcome("innovate", "explore", 0.2, False, cycle=i))

        analyzer = ExperienceAnalyzer(store)
        worst = analyzer.worst_strategy(min_samples=5)
        assert worst == "innovate"

    def test_worst_strategy_insufficient_samples(self, tmp_path):
        """样本不足时 worst_strategy 返回 None"""
        store = ExperienceStore(tmp_path / "exp.jsonl")
        analyzer = ExperienceAnalyzer(store)
        assert analyzer.worst_strategy(min_samples=5) is None


# ============================================================
# AdaptiveWeights 测试
# ============================================================

class TestAdaptiveWeights:
    """AdaptiveWeights 动态权重调整测试"""

    def test_no_data_uses_default(self, tmp_path):
        """无数据时返回默认权重"""
        store = ExperienceStore(tmp_path / "exp.jsonl")
        analyzer = ExperienceAnalyzer(store)
        weights = AdaptiveWeights(analyzer)
        adjusted = weights.adjusted_weights()
        for strategy, default in DEFAULT_WEIGHTS.items():
            assert adjusted[strategy] == default

    def test_insufficient_samples_no_adjust(self, tmp_path):
        """样本不足时不调整，返回默认"""
        store = ExperienceStore(tmp_path / "exp.jsonl")
        # 只记录 2 个样本（< MIN_SAMPLES_FOR_ADJUST=5）
        store.record(StrategyOutcome("balanced", "repair", 0.95, True, cycle=1))
        store.record(StrategyOutcome("balanced", "repair", 0.95, True, cycle=2))

        analyzer = ExperienceAnalyzer(store)
        weights = AdaptiveWeights(analyzer)
        adjusted = weights.adjusted_weight("balanced")
        assert adjusted == DEFAULT_WEIGHTS["balanced"]

    def test_high_success_rate_increases_repair(self, tmp_path):
        """成功率高 → repair_ratio 增加（更稳妥利用优势）"""
        store = ExperienceStore(tmp_path / "exp.jsonl")
        # 8/10 成功，成功率 0.8 > 0.7
        for i in range(8):
            store.record(StrategyOutcome("balanced", "repair", 0.85, True, cycle=i))
        for i in range(2):
            store.record(StrategyOutcome("balanced", "repair", 0.3, False, cycle=i + 8))

        analyzer = ExperienceAnalyzer(store)
        weights = AdaptiveWeights(analyzer)
        adjusted = weights.adjusted_weight("balanced")
        default = DEFAULT_WEIGHTS["balanced"]
        # repair_ratio 应该增加
        assert adjusted[0] > default[0]
        # 增幅在合理范围
        assert adjusted[0] - default[0] <= MAX_ADJUSTMENT + 0.1  # +0.1 容纳 trend 微调

    def test_low_success_rate_decreases_repair(self, tmp_path):
        """成功率低 → repair_ratio 降低（鼓励尝试创新）"""
        store = ExperienceStore(tmp_path / "exp.jsonl")
        # 2/10 成功，成功率 0.2 < 0.3
        for i in range(2):
            store.record(StrategyOutcome("balanced", "repair", 0.85, True, cycle=i))
        for i in range(8):
            store.record(StrategyOutcome("balanced", "repair", 0.2, False, cycle=i + 2))

        analyzer = ExperienceAnalyzer(store)
        weights = AdaptiveWeights(analyzer)
        adjusted = weights.adjusted_weight("balanced")
        default = DEFAULT_WEIGHTS["balanced"]
        # repair_ratio 应该降低
        assert adjusted[0] < default[0]

    def test_medium_success_rate_no_adjust(self, tmp_path):
        """成功率在 0.3-0.7 之间不调整（trend 也接近 0 时）"""
        store = ExperienceStore(tmp_path / "exp.jsonl")
        # 5/10 成功，成功率 0.5，所有 score 相同 → trend=0
        for i in range(5):
            store.record(StrategyOutcome("balanced", "repair", 0.5, True, cycle=i))
        for i in range(5):
            store.record(StrategyOutcome("balanced", "repair", 0.5, False, cycle=i + 5))

        analyzer = ExperienceAnalyzer(store)
        weights = AdaptiveWeights(analyzer)
        adjusted = weights.adjusted_weight("balanced")
        default = DEFAULT_WEIGHTS["balanced"]
        # 成功率 0.5 在 [0.3, 0.7] → 不调整；trend=0 → 不微调
        assert adjusted == default

    def test_clamp_to_min(self, tmp_path):
        """repair_ratio 不会低于 0.1"""
        store = ExperienceStore(tmp_path / "exp.jsonl")
        # 极低成功率 + 下降趋势
        for i in range(10):
            store.record(StrategyOutcome("innovate", "explore", 0.1, False, cycle=i))

        analyzer = ExperienceAnalyzer(store)
        weights = AdaptiveWeights(analyzer)
        adjusted = weights.adjusted_weight("innovate")
        # innovate 默认 repair=0.3，降低后不应低于 0.1
        assert adjusted[0] >= 0.1

    def test_clamp_to_max(self, tmp_path):
        """repair_ratio 不会高于 1.0"""
        store = ExperienceStore(tmp_path / "exp.jsonl")
        # 极高成功率 + 上升趋势，用 harden（默认 repair=0.9）
        for i in range(10):
            store.record(StrategyOutcome("harden", "repair", 0.95, True, cycle=i))

        analyzer = ExperienceAnalyzer(store)
        weights = AdaptiveWeights(analyzer)
        adjusted = weights.adjusted_weight("harden")
        assert adjusted[0] <= 1.0

    def test_innovate_ratio_is_complement(self, tmp_path):
        """innovate_ratio = 1 - repair_ratio"""
        store = ExperienceStore(tmp_path / "exp.jsonl")
        for i in range(8):
            store.record(StrategyOutcome("balanced", "repair", 0.85, True, cycle=i))
        for i in range(2):
            store.record(StrategyOutcome("balanced", "repair", 0.3, False, cycle=i + 8))

        analyzer = ExperienceAnalyzer(store)
        weights = AdaptiveWeights(analyzer)
        adjusted = weights.adjusted_weight("balanced")
        assert abs(adjusted[0] + adjusted[1] - 1.0) < 0.01

    def test_adjustment_report(self, tmp_path):
        """adjustment_report 返回所有策略的报告"""
        store = ExperienceStore(tmp_path / "exp.jsonl")
        analyzer = ExperienceAnalyzer(store)
        weights = AdaptiveWeights(analyzer)
        report = weights.adjustment_report()

        assert len(report) == len(DEFAULT_WEIGHTS)
        for entry in report:
            assert "strategy" in entry
            assert "default" in entry
            assert "adjusted" in entry
            assert "reason" in entry
            assert entry["reason"] in (
                "no_data", "insufficient_samples",
                "no_adjustment_needed", "adjusted",
            )

    def test_adjustment_report_with_data(self, tmp_path):
        """有数据时报告包含统计信息"""
        store = ExperienceStore(tmp_path / "exp.jsonl")
        for i in range(8):
            store.record(StrategyOutcome("balanced", "repair", 0.85, True, cycle=i))
        for i in range(2):
            store.record(StrategyOutcome("balanced", "repair", 0.3, False, cycle=i + 8))

        analyzer = ExperienceAnalyzer(store)
        weights = AdaptiveWeights(analyzer)
        report = weights.adjustment_report()

        balanced_entry = next(e for e in report if e["strategy"] == "balanced")
        assert balanced_entry["attempts"] == 10
        assert balanced_entry["success_rate"] == 0.8
        assert balanced_entry["reason"] == "adjusted"


# ============================================================
# ExperienceLearner 统一入口测试
# ============================================================

class TestExperienceLearner:
    """ExperienceLearner 统一入口测试"""

    def test_record_and_adjusted_weights(self, tmp_path):
        """record 后 adjusted_weights 反映历史"""
        learner = ExperienceLearner(tmp_path / "exp.jsonl")
        # 样本不足 → 默认权重
        assert learner.adjusted_weight("balanced") == DEFAULT_WEIGHTS["balanced"]

        # 记录足够样本
        for i in range(8):
            learner.record(StrategyOutcome("balanced", "repair", 0.9, True, cycle=i))
        for i in range(2):
            learner.record(StrategyOutcome("balanced", "repair", 0.3, False, cycle=i + 8))

        adjusted = learner.adjusted_weight("balanced")
        assert adjusted[0] > DEFAULT_WEIGHTS["balanced"][0]

    def test_report(self, tmp_path):
        """report 返回调整报告"""
        learner = ExperienceLearner(tmp_path / "exp.jsonl")
        report = learner.report()
        assert len(report) == len(DEFAULT_WEIGHTS)

    def test_recent_outcomes(self, tmp_path):
        """recent_outcomes 返回最近记录"""
        learner = ExperienceLearner(tmp_path / "exp.jsonl")
        learner.record(StrategyOutcome("balanced", "repair", 0.8, True, cycle=1))
        learner.record(StrategyOutcome("innovate", "explore", 0.4, False, cycle=2))

        recent = learner.recent_outcomes()
        assert len(recent) == 2
        assert recent[-1].cycle == 2

    def test_best_strategy(self, tmp_path):
        """best_strategy 透传到 analyzer"""
        learner = ExperienceLearner(tmp_path / "exp.jsonl")
        for i in range(8):
            learner.record(StrategyOutcome("balanced", "repair", 0.85, True, cycle=i))
        for i in range(2):
            learner.record(StrategyOutcome("balanced", "repair", 0.3, False, cycle=i + 8))
        for i in range(5):
            learner.record(StrategyOutcome("innovate", "explore", 0.2, False, cycle=i))

        best = learner.best_strategy(min_samples=5)
        assert best == "balanced"

    def test_analyze_all(self, tmp_path):
        """analyze_all 透传到 analyzer"""
        learner = ExperienceLearner(tmp_path / "exp.jsonl")
        learner.record(StrategyOutcome("balanced", "repair", 0.8, True, cycle=1))
        stats = learner.analyze_all()
        assert "balanced" in stats
        assert stats["balanced"].attempts == 1


# ============================================================
# gep_engine 集成测试 — StrategyManager
# ============================================================

class TestStrategyManagerIntegration:
    """StrategyManager 集成 experience_learner 测试"""

    def test_strategy_mgr_with_experience_learner(self, tmp_path):
        """StrategyManager 带 experience_learner 时 _current_ratios 用调整后权重"""
        learner = ExperienceLearner(tmp_path / "exp.jsonl")
        # 样本不足 → 等于默认
        mgr = StrategyManager("balanced", experience_learner=learner)
        assert mgr._current_ratios() == DEFAULT_WEIGHTS["balanced"]

    def test_strategy_mgr_without_experience_learner(self, tmp_path):
        """无 experience_learner 时 _current_ratios 用 STRATEGY_RATIOS"""
        mgr = StrategyManager("balanced")
        assert mgr._current_ratios() == StrategyManager.STRATEGY_RATIOS["balanced"]

    def test_strategy_mgr_uses_adjusted_weights(self, tmp_path):
        """有足够样本时 _current_ratios 返回调整后权重"""
        learner = ExperienceLearner(tmp_path / "exp.jsonl")
        for i in range(8):
            learner.record(StrategyOutcome("balanced", "repair", 0.9, True, cycle=i))
        for i in range(2):
            learner.record(StrategyOutcome("balanced", "repair", 0.3, False, cycle=i + 8))

        mgr = StrategyManager("balanced", experience_learner=learner)
        adjusted = mgr._current_ratios()
        assert adjusted[0] > DEFAULT_WEIGHTS["balanced"][0]

    def test_select_category_with_experience(self, tmp_path):
        """select_category 使用调整后权重（不崩溃）"""
        learner = ExperienceLearner(tmp_path / "exp.jsonl")
        for i in range(8):
            learner.record(StrategyOutcome("balanced", "repair", 0.9, True, cycle=i))
        for i in range(2):
            learner.record(StrategyOutcome("balanced", "repair", 0.3, False, cycle=i + 8))

        mgr = StrategyManager("balanced", experience_learner=learner)
        signals = [Signal(signal_type="error", source="test", severity="high", pattern="err")]
        category = mgr.select_category(signals)
        assert category in ("repair", "optimize", "innovate", "explore")

    def test_backward_compatible_no_learner(self):
        """无 experience_learner 时 select_category 走原逻辑"""
        mgr = StrategyManager("repair-only")
        signals = [Signal(signal_type="error", source="test", severity="high", pattern="err")]
        assert mgr.select_category(signals) == "repair"


# ============================================================
# gep_engine 集成测试 — GEPEngine
# ============================================================

class TestGEPEngineIntegration:
    """GEPEngine 集成 experience_learner 测试"""

    def test_engine_init_with_experience_learner(self, tmp_workspace):
        """GEPEngine 带 experience_learner 初始化"""
        learner = ExperienceLearner(tmp_workspace / "logs" / "exp.jsonl")
        engine = GEPEngine(
            memory=MemoryStore(tmp_workspace),
            llm=ExpMockLLM(),
            workspace=tmp_workspace,
            experience_learner=learner,
        )
        assert engine.experience_learner is learner
        assert engine.strategy_mgr.experience_learner is learner

    def test_engine_init_without_experience_learner(self, tmp_workspace):
        """无 experience_learner 时向后兼容"""
        engine = GEPEngine(
            memory=MemoryStore(tmp_workspace),
            llm=ExpMockLLM(),
            workspace=tmp_workspace,
        )
        assert engine.experience_learner is None
        assert engine.strategy_mgr.experience_learner is None

    def test_run_cycle_records_experience(self, tmp_workspace):
        """run_cycle 记录策略结果到 experience_learner"""
        exp_path = tmp_workspace / "logs" / "exp.jsonl"
        learner = ExperienceLearner(exp_path)
        memory = MemoryStore(tmp_workspace)
        _add_reflection(memory)  # 注入反思记录产生信号
        engine = GEPEngine(
            memory=memory,
            llm=ExpMockLLM(),
            workspace=tmp_workspace,
            experience_learner=learner,
        )
        result = engine.run_cycle()
        assert result.get("experience_recorded") is True

        # 验证记录已写入
        outcomes = learner.recent_outcomes()
        assert len(outcomes) == 1
        assert outcomes[0].strategy == "balanced"
        assert outcomes[0].cycle == 1

    def test_run_cycle_no_learner_no_record(self, tmp_workspace):
        """无 experience_learner 时 run_cycle 不记录"""
        engine = GEPEngine(
            memory=MemoryStore(tmp_workspace),
            llm=ExpMockLLM(),
            workspace=tmp_workspace,
        )
        result = engine.run_cycle()
        assert "experience_recorded" not in result

    def test_run_experience_driven_adjustment_no_learner(self, tmp_workspace):
        """无 experience_learner 时 run_experience_driven_adjustment 返回 skipped"""
        engine = GEPEngine(
            memory=MemoryStore(tmp_workspace),
            llm=ExpMockLLM(),
            workspace=tmp_workspace,
        )
        result = engine.run_experience_driven_adjustment()
        assert result["status"] == "skipped"
        assert "reason" in result

    def test_run_experience_driven_adjustment_empty(self, tmp_workspace):
        """有 learner 但无数据时返回空统计"""
        learner = ExperienceLearner(tmp_workspace / "logs" / "exp.jsonl")
        engine = GEPEngine(
            memory=MemoryStore(tmp_workspace),
            llm=ExpMockLLM(),
            workspace=tmp_workspace,
            experience_learner=learner,
        )
        result = engine.run_experience_driven_adjustment()
        assert result["status"] == "success"
        assert result["stats"] == {}
        assert "adjusted_weights" in result

    def test_run_experience_driven_adjustment_with_data(self, tmp_workspace):
        """有数据时返回完整统计和调整后权重"""
        learner = ExperienceLearner(tmp_workspace / "logs" / "exp.jsonl")
        # 记录足够样本
        for i in range(8):
            learner.record(StrategyOutcome("balanced", "repair", 0.9, True, cycle=i))
        for i in range(2):
            learner.record(StrategyOutcome("balanced", "repair", 0.3, False, cycle=i + 8))

        engine = GEPEngine(
            memory=MemoryStore(tmp_workspace),
            llm=ExpMockLLM(),
            workspace=tmp_workspace,
            experience_learner=learner,
        )
        result = engine.run_experience_driven_adjustment()
        assert result["status"] == "success"
        assert "balanced" in result["stats"]
        assert result["stats"]["balanced"]["attempts"] == 10
        assert result["stats"]["balanced"]["success_rate"] == 0.8
        assert "balanced" in result["adjusted_weights"]
        assert "report" in result

    def test_multiple_cycles_accumulate_experience(self, tmp_workspace):
        """多个 cycle 累积经验"""
        exp_path = tmp_workspace / "logs" / "exp.jsonl"
        learner = ExperienceLearner(exp_path)
        memory = MemoryStore(tmp_workspace)
        _add_reflection(memory)  # 注入反思记录产生信号
        engine = GEPEngine(
            memory=memory,
            llm=ExpMockLLM(),
            workspace=tmp_workspace,
            experience_learner=learner,
        )
        engine.run_cycle()
        engine.run_cycle()
        engine.run_cycle()

        outcomes = learner.recent_outcomes()
        assert len(outcomes) == 3
        assert outcomes[0].cycle == 1
        assert outcomes[1].cycle == 2
        assert outcomes[2].cycle == 3

    def test_experience_persists_across_engines(self, tmp_workspace):
        """经验记录持久化，新 engine 能读到旧记录"""
        exp_path = tmp_workspace / "logs" / "exp.jsonl"

        # 第一个 engine 记录
        learner1 = ExperienceLearner(exp_path)
        memory1 = MemoryStore(tmp_workspace)
        _add_reflection(memory1)  # 注入反思记录产生信号
        engine1 = GEPEngine(
            memory=memory1,
            llm=ExpMockLLM(),
            workspace=tmp_workspace,
            experience_learner=learner1,
        )
        engine1.run_cycle()

        # 第二个 engine 读到旧记录
        learner2 = ExperienceLearner(exp_path)
        outcomes = learner2.recent_outcomes()
        assert len(outcomes) == 1
        assert outcomes[0].strategy == "balanced"


# ============================================================
# 端到端：经验驱动权重调整影响后续策略选择
# ============================================================

class TestEndToEndExperienceDriven:
    """端到端：经验积累 → 权重调整 → 影响策略选择"""

    def test_experience_influences_strategy_selection(self, tmp_workspace):
        """经验积累后权重调整影响 select_category 的概率分布"""
        exp_path = tmp_workspace / "logs" / "exp.jsonl"
        learner = ExperienceLearner(exp_path)

        # 模拟 balanced 策略历史成功率极低 → repair_ratio 降低
        for i in range(10):
            learner.record(StrategyOutcome("balanced", "repair", 0.1, False, cycle=i))

        mgr = StrategyManager("balanced", experience_learner=learner)
        adjusted = mgr._current_ratios()
        default = DEFAULT_WEIGHTS["balanced"]

        # repair_ratio 应该降低
        assert adjusted[0] < default[0]

        # 统计 100 次 select_category，repair 类应比默认少
        signals = [Signal(signal_type="error", source="test", severity="high", pattern="err")]
        repair_count = sum(
            1 for _ in range(100)
            if mgr.select_category(signals) in ("repair", "optimize")
        )
        # 默认 repair_ratio=0.7，调整后降低 → repair_count 应明显少于 70
        # （允许统计波动，用宽松断言）
        assert repair_count < 75

    def test_full_cycle_learn_and_adjust(self, tmp_workspace):
        """完整循环：run_cycle 积累经验 → 调整 → 影响后续"""
        exp_path = tmp_workspace / "logs" / "exp.jsonl"
        learner = ExperienceLearner(exp_path)
        memory = MemoryStore(tmp_workspace)
        _add_reflection(memory)  # 注入反思记录产生信号
        engine = GEPEngine(
            memory=memory,
            llm=ExpMockLLM(),
            workspace=tmp_workspace,
            experience_learner=learner,
        )

        # 跑 6 个 cycle 积累经验（超过 MIN_SAMPLES_FOR_ADJUST=5）
        for _ in range(6):
            engine.run_cycle()

        # 调整报告应能生成
        report = engine.run_experience_driven_adjustment()
        assert report["status"] == "success"
        assert "balanced" in report["stats"]
        assert report["stats"]["balanced"]["attempts"] == 6
