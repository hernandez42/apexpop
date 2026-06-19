"""测试 superclaw.curiosity — 好奇心启发式机制（频次引导的探索倾向评分）

覆盖：
- NoveltyScorer：未见过=1.0、见过衰减、record 更新、持久化往返、多类型独立、novelty_bonus
- BoredomTracker：重复=高厌倦、多样=低厌倦、suggest_alternative、max_recent_boredom
- CuriosityDrive：intrinsic_reward 公式、should_explore 阈值、suggest_exploration_target
- ExplorationGoal dataclass
- CuriosityDrivenExplorer：discover_targets、generate_curiosity_signal
- 集成测试：StrategyManager 带 curiosity 时 explore 概率提升、GEPEngine.run_curious_exploration
- 向后兼容：无 curiosity 时原逻辑不变
"""
import math
import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from superclaw.curiosity import (
    NoveltyScorer, BoredomTracker, CuriosityDrive,
    ExplorationGoal, CuriosityDrivenExplorer,
)
from superclaw.gep_schema import Signal
from superclaw.gep_engine import StrategyManager, GEPEngine
from superclaw.memory import MemoryStore
from superclaw.llm_router import CompletionResult
from superclaw.capability_registry import CapabilityRegistry


# ============================================================
# Mock LLM — 信号提取/好奇心领域建议/修改 各返回不同响应
# ============================================================

class CuriosityMockLLM:
    """好奇心测试用 Mock LLM

    - 信号提取 prompt（含 "分析以下进化信号"）→ 返回空 JSON 数组
    - 好奇心领域建议 prompt（含 "可能值得探索的新能力领域"）→ 返回领域列表
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

        if "可能值得探索的新能力领域" in prompt:
            return CompletionResult(
                content="weather_query\nfetch_url\nimage_recognition\ntranslation\ndata_analysis",
                provider="mock", model="mock",
                tokens_used=20, error=None,
            )

        return CompletionResult(
            content='{"action": "探索未知领域", "target": "unknown"}',
            provider="mock", model="mock",
            tokens_used=50, error=None,
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


# ============================================================
# NoveltyScorer 测试
# ============================================================

def test_novelty_unseen_is_1(tmp_workspace):
    """从未见过的 item → 1.0"""
    scorer = NoveltyScorer(tmp_workspace / "novelty.json")
    assert scorer.score("never_seen", "signal") == 1.0


def test_novelty_seen_once_is_08(tmp_workspace):
    """见过 1 次 → 0.8"""
    scorer = NoveltyScorer(tmp_workspace / "novelty.json")
    scorer.record("item1", "signal")
    assert scorer.score("item1", "signal") == pytest.approx(0.8)


def test_novelty_seen_many_times_decays(tmp_workspace):
    """见过 N 次（N>=2）→ 1.0 / (1.0 + log(N+1))，且小于 0.8"""
    scorer = NoveltyScorer(tmp_workspace / "novelty.json")
    for _ in range(5):
        scorer.record("item1", "signal")
    expected = 1.0 / (1.0 + math.log(5 + 1))
    assert scorer.score("item1", "signal") == pytest.approx(expected)
    assert scorer.score("item1", "signal") < 0.8


def test_novelty_record_increments(tmp_workspace):
    """record 多次 → count 递增 → score 持续衰减"""
    scorer = NoveltyScorer(tmp_workspace / "novelty.json")
    scorer.record("x", "signal")
    assert scorer.score("x", "signal") == pytest.approx(0.8)
    scorer.record("x", "signal")
    expected = 1.0 / (1.0 + math.log(3))
    assert scorer.score("x", "signal") == pytest.approx(expected)


def test_novelty_persistence_roundtrip(tmp_workspace):
    """record 自动持久化，新实例加载后保留记录"""
    path = tmp_workspace / "novelty.json"
    scorer1 = NoveltyScorer(path)
    scorer1.record("alpha", "signal")
    scorer1.record("alpha", "signal")
    scorer1.record("beta", "domain")

    scorer2 = NoveltyScorer(path)
    assert scorer2.score("alpha", "signal") < 1.0
    assert scorer2.score("beta", "domain") < 1.0
    assert scorer2.score("unknown", "signal") == 1.0


def test_novelty_persistence_json_structure(tmp_workspace):
    """持久化文件结构：{signal: {item: count}, domain: {...}, task: {...}}"""
    path = tmp_workspace / "novelty.json"
    scorer = NoveltyScorer(path)
    scorer.record("a", "signal")
    scorer.record("b", "domain")
    scorer.record("c", "task")

    import json
    data = json.loads(path.read_text(encoding="utf-8"))
    assert "signal" in data
    assert "domain" in data
    assert "task" in data
    assert data["signal"]["a"] == 1
    assert data["domain"]["b"] == 1
    assert data["task"]["c"] == 1


def test_novelty_types_independent(tmp_workspace):
    """不同 item_type 的计数相互独立"""
    scorer = NoveltyScorer(tmp_workspace / "novelty.json")
    scorer.record("shared_name", "signal")
    assert scorer.score("shared_name", "signal") < 1.0
    assert scorer.score("shared_name", "domain") == 1.0


def test_novelty_bonus_max(tmp_workspace):
    """novelty_bonus 取一组 item 的最大新颖度"""
    scorer = NoveltyScorer(tmp_workspace / "novelty.json")
    scorer.record("seen", "signal")
    bonus = scorer.novelty_bonus(["seen", "unseen"])
    assert bonus == pytest.approx(1.0)  # unseen = 1.0


def test_novelty_bonus_empty(tmp_workspace):
    """空列表 → 0.0"""
    scorer = NoveltyScorer(tmp_workspace / "novelty.json")
    assert scorer.novelty_bonus([]) == 0.0


# ============================================================
# BoredomTracker 测试
# ============================================================

def test_boredom_repeated_high(tmp_workspace):
    """最近 10 次都是同一类 → 0.9"""
    tracker = BoredomTracker()
    for _ in range(10):
        tracker.record("repair")
    assert tracker.boredom_level("repair") == pytest.approx(0.9)


def test_boredom_diverse_low(tmp_workspace):
    """最近 10 次多样 → 低厌倦"""
    tracker = BoredomTracker()
    for t in ["repair", "optimize", "innovate", "explore", "repair",
              "optimize", "innovate", "explore", "repair", "optimize"]:
        tracker.record(t)
    assert tracker.boredom_level("repair") < 0.3
    assert tracker.boredom_level("explore") < 0.3


def test_boredom_empty_history(tmp_workspace):
    """无历史 → 0.0"""
    tracker = BoredomTracker()
    assert tracker.boredom_level("repair") == 0.0


def test_boredom_partial_history(tmp_workspace):
    """不足 10 条但全是同一类 → 0.9"""
    tracker = BoredomTracker()
    for _ in range(3):
        tracker.record("repair")
    assert tracker.boredom_level("repair") == pytest.approx(0.9)


def test_boredom_max_recent(tmp_workspace):
    """max_recent_boredom 返回最近历史中最大厌倦度"""
    tracker = BoredomTracker()
    for _ in range(10):
        tracker.record("repair")
    tracker.record("optimize")
    # repair 出现 9/10 → 0.81；optimize 1/10 → 0.09
    assert tracker.max_recent_boredom() == pytest.approx(0.81)


def test_boredom_suggest_alternative(tmp_workspace):
    """suggest_alternative 返回最近最少做的任务类型"""
    tracker = BoredomTracker()
    for _ in range(5):
        tracker.record("repair")
    tracker.record("optimize")
    suggestion = tracker.suggest_alternative(
        ["repair", "optimize", "innovate", "explore"]
    )
    # repair 做了 5 次，optimize 1 次，innovate/explore 0 次
    assert suggestion in ["innovate", "explore"]
    assert suggestion != "repair"


def test_boredom_suggest_alternative_empty(tmp_workspace):
    """空候选列表 → 空字符串"""
    tracker = BoredomTracker()
    assert tracker.suggest_alternative([]) == ""


# ============================================================
# CuriosityDrive 测试
# ============================================================

def test_curiosity_intrinsic_reward_high_novelty(tmp_workspace):
    """全新 item + 无 boredom → reward = 1.0*0.6 + 1.0*0.4 = 1.0"""
    scorer = NoveltyScorer(tmp_workspace / "n.json")
    boredom = BoredomTracker()
    drive = CuriosityDrive(scorer, boredom)
    reward = drive.intrinsic_reward("brand_new", "signal")
    assert reward == pytest.approx(1.0)


def test_curiosity_intrinsic_reward_low_novelty_high_boredom(tmp_workspace):
    """低 novelty + 高 boredom → 低奖励"""
    scorer = NoveltyScorer(tmp_workspace / "n.json")
    boredom = BoredomTracker()
    drive = CuriosityDrive(scorer, boredom)
    for _ in range(20):
        scorer.record("old_item", "signal")
    for _ in range(10):
        boredom.record("signal")
    reward = drive.intrinsic_reward("old_item", "signal")
    assert reward < 0.5


def test_curiosity_intrinsic_reward_formula(tmp_workspace):
    """验证公式：novelty*0.6 + (1-boredom)*0.4"""
    scorer = NoveltyScorer(tmp_workspace / "n.json")
    boredom = BoredomTracker()
    drive = CuriosityDrive(scorer, boredom)
    scorer.record("x", "signal")  # novelty = 0.8
    boredom.record("signal")
    boredom.record("signal")  # boredom = 0.9 * 2/2 = 0.9
    expected = 0.8 * 0.6 + (1.0 - 0.9) * 0.4
    assert drive.intrinsic_reward("x", "signal") == pytest.approx(expected)


def test_curiosity_should_explore_high_novelty(tmp_workspace):
    """信号整体新颖度 > 0.6 → True"""
    scorer = NoveltyScorer(tmp_workspace / "n.json")
    boredom = BoredomTracker()
    drive = CuriosityDrive(scorer, boredom)
    assert drive.should_explore(["brand_new_signal"]) is True


def test_curiosity_should_explore_boredom(tmp_workspace):
    """novelty 低但 boredom 高 → True"""
    scorer = NoveltyScorer(tmp_workspace / "n.json")
    boredom = BoredomTracker()
    drive = CuriosityDrive(scorer, boredom)
    for _ in range(20):
        scorer.record("old", "signal")
    for _ in range(10):
        boredom.record("signal")
    assert drive.should_explore(["old"]) is True


def test_curiosity_should_explore_neither(tmp_workspace):
    """novelty 低 + boredom 低 → False"""
    scorer = NoveltyScorer(tmp_workspace / "n.json")
    boredom = BoredomTracker()
    drive = CuriosityDrive(scorer, boredom)
    for _ in range(20):
        scorer.record("old", "signal")
    assert drive.should_explore(["old"]) is False


def test_curiosity_should_explore_empty_signals_no_boredom(tmp_workspace):
    """空信号 + 无 boredom → False"""
    scorer = NoveltyScorer(tmp_workspace / "n.json")
    boredom = BoredomTracker()
    drive = CuriosityDrive(scorer, boredom)
    assert drive.should_explore([]) is False


def test_curiosity_suggest_target_gap(tmp_workspace):
    """有未覆盖领域 → 选 novelty 最高的 gap"""
    scorer = NoveltyScorer(tmp_workspace / "n.json")
    boredom = BoredomTracker()
    drive = CuriosityDrive(scorer, boredom)
    known = ["a", "b"]
    all_possible = ["a", "b", "c", "d"]
    target = drive.suggest_exploration_target(known, all_possible)
    assert target in ["c", "d"]
    assert target not in known


def test_curiosity_suggest_target_all_covered_picks_low_boredom(tmp_workspace):
    """全覆盖 → 选 boredom 最低的"""
    scorer = NoveltyScorer(tmp_workspace / "n.json")
    boredom = BoredomTracker()
    drive = CuriosityDrive(scorer, boredom)
    for _ in range(10):
        boredom.record("a")  # a 很无聊
    known = ["a", "b"]
    all_possible = ["a", "b"]
    target = drive.suggest_exploration_target(known, all_possible)
    assert target == "b"


def test_curiosity_suggest_target_empty(tmp_workspace):
    """空 all_possible → 空字符串"""
    scorer = NoveltyScorer(tmp_workspace / "n.json")
    boredom = BoredomTracker()
    drive = CuriosityDrive(scorer, boredom)
    assert drive.suggest_exploration_target(["a"], []) == ""


# ============================================================
# ExplorationGoal dataclass 测试
# ============================================================

def test_exploration_goal_dataclass():
    goal = ExplorationGoal(
        target_domain="weather_query",
        reason="gap",
        novelty_score=1.0,
        expected_reward=0.9,
        suggested_action="code_generate",
    )
    assert goal.target_domain == "weather_query"
    assert goal.reason == "gap"
    assert goal.novelty_score == 1.0
    assert goal.expected_reward == 0.9
    assert goal.suggested_action == "code_generate"


# ============================================================
# CuriosityDrivenExplorer 测试
# ============================================================

def test_explorer_discover_targets_gaps(tmp_workspace):
    """有 gap → 返回 gap 类型的探索目标，top 3"""
    scorer = NoveltyScorer(tmp_workspace / "n.json")
    boredom = BoredomTracker()
    drive = CuriosityDrive(scorer, boredom)
    registry = CapabilityRegistry(registry_path=tmp_workspace / "cap.json")
    registry.register_defaults()
    explorer = CuriosityDrivenExplorer(drive, registry, llm_router=None)

    known = [c.name for c in registry.list_all()]
    targets = explorer.discover_targets({"known_domains": known})

    assert 0 < len(targets) <= 3
    for t in targets:
        assert isinstance(t, ExplorationGoal)
        assert t.target_domain not in known
        assert t.reason == "gap"
        assert t.suggested_action in ["github_search", "code_generate", "skill_load"]
        assert 0.0 <= t.novelty_score <= 1.0


def test_explorer_discover_targets_sorted_by_reward(tmp_workspace):
    """返回目标按 expected_reward 降序"""
    scorer = NoveltyScorer(tmp_workspace / "n.json")
    boredom = BoredomTracker()
    drive = CuriosityDrive(scorer, boredom)
    registry = CapabilityRegistry(registry_path=tmp_workspace / "cap.json")
    explorer = CuriosityDrivenExplorer(drive, registry, llm_router=None)

    targets = explorer.discover_targets({"known_domains": []})
    rewards = [t.expected_reward for t in targets]
    assert rewards == sorted(rewards, reverse=True)


def test_explorer_discover_targets_all_covered(tmp_workspace):
    """全覆盖 → 返回 boredom/novelty 类型目标"""
    scorer = NoveltyScorer(tmp_workspace / "n.json")
    boredom = BoredomTracker()
    drive = CuriosityDrive(scorer, boredom)
    registry = CapabilityRegistry(registry_path=tmp_workspace / "cap.json")
    explorer = CuriosityDrivenExplorer(drive, registry, llm_router=None)

    # known == all_possible → 无 gap
    targets = explorer.discover_targets({
        "known_domains": ["a", "b"],
        "all_possible_domains": ["a", "b"],
    })
    assert len(targets) > 0
    for t in targets:
        assert t.reason in ["boredom", "novelty"]


def test_explorer_discover_targets_empty(tmp_workspace):
    """无候选领域 → 空列表"""
    scorer = NoveltyScorer(tmp_workspace / "n.json")
    boredom = BoredomTracker()
    drive = CuriosityDrive(scorer, boredom)
    registry = CapabilityRegistry(registry_path=tmp_workspace / "cap.json")
    explorer = CuriosityDrivenExplorer(drive, registry, llm_router=None)

    targets = explorer.discover_targets({
        "known_domains": ["a"],
        "all_possible_domains": ["a"],
    })
    # 全覆盖且无 gap → 返回 boredom/novelty 目标（非空）
    assert len(targets) > 0


def test_explorer_generate_curiosity_signal(tmp_workspace):
    """generate_curiosity_signal 生成 curiosity 类型 Signal"""
    scorer = NoveltyScorer(tmp_workspace / "n.json")
    boredom = BoredomTracker()
    drive = CuriosityDrive(scorer, boredom)
    registry = CapabilityRegistry(registry_path=tmp_workspace / "cap.json")
    explorer = CuriosityDrivenExplorer(drive, registry, llm_router=None)

    goal = ExplorationGoal(
        target_domain="weather_query",
        reason="gap",
        novelty_score=1.0,
        expected_reward=0.9,
        suggested_action="code_generate",
    )
    signal = explorer.generate_curiosity_signal(goal)
    assert signal.signal_type == "curiosity"
    assert signal.severity == "info"
    assert "weather_query" in signal.pattern


def test_explorer_suggest_action_skill_load(tmp_workspace):
    """domain 含 skill/load → skill_load"""
    scorer = NoveltyScorer(tmp_workspace / "n.json")
    boredom = BoredomTracker()
    drive = CuriosityDrive(scorer, boredom)
    registry = CapabilityRegistry(registry_path=tmp_workspace / "cap.json")
    explorer = CuriosityDrivenExplorer(drive, registry, llm_router=None)

    targets = explorer.discover_targets({
        "known_domains": [],
        "all_possible_domains": ["skill_loader_new"],
    })
    assert targets[0].suggested_action == "skill_load"


def test_explorer_suggest_action_github_search(tmp_workspace):
    """domain 含 search/fetch → github_search"""
    scorer = NoveltyScorer(tmp_workspace / "n.json")
    boredom = BoredomTracker()
    drive = CuriosityDrive(scorer, boredom)
    registry = CapabilityRegistry(registry_path=tmp_workspace / "cap.json")
    explorer = CuriosityDrivenExplorer(drive, registry, llm_router=None)

    targets = explorer.discover_targets({
        "known_domains": [],
        "all_possible_domains": ["web_search"],
    })
    assert targets[0].suggested_action == "github_search"


# ============================================================
# StrategyManager 集成测试
# ============================================================

def test_strategy_manager_accepts_curiosity(tmp_workspace):
    """StrategyManager 接受可选 curiosity 参数"""
    scorer = NoveltyScorer(tmp_workspace / "n.json")
    boredom = BoredomTracker()
    drive = CuriosityDrive(scorer, boredom)
    sm = StrategyManager("balanced", curiosity=drive)
    assert sm.curiosity is drive


def test_strategy_manager_backward_compat_no_curiosity():
    """无 curiosity → 原逻辑不变"""
    sm = StrategyManager("balanced")
    assert sm.curiosity is None


def test_strategy_manager_curiosity_boosts_explore(tmp_workspace, monkeypatch):
    """带 curiosity 且 should_explore=True → innovate 分支返回 explore"""
    scorer = NoveltyScorer(tmp_workspace / "n.json")
    boredom = BoredomTracker()
    drive = CuriosityDrive(scorer, boredom)
    signals = [Signal(signal_type="feature", pattern="brand_new_feature")]

    monkeypatch.setattr(random, "random", lambda: 0.9)

    # 无 curiosity → innovate（r=0.9 >= 0.7，有 feature）
    sm_no = StrategyManager("balanced")
    assert sm_no.select_category(signals) == "innovate"

    # 有 curiosity → explore（好奇心提升探索）
    sm_yes = StrategyManager("balanced", curiosity=drive)
    assert sm_yes.select_category(signals) == "explore"


def test_strategy_manager_curiosity_increases_explore_rate(tmp_workspace):
    """有 curiosity 时 explore 出现频率高于无 curiosity"""
    scorer = NoveltyScorer(tmp_workspace / "n.json")
    boredom = BoredomTracker()
    drive = CuriosityDrive(scorer, boredom)
    signals = [Signal(signal_type="feature", pattern="novel_feature")]

    sm_no = StrategyManager("balanced")
    sm_yes = StrategyManager("balanced", curiosity=drive)

    explore_no = sum(
        1 for _ in range(200) if sm_no.select_category(signals) == "explore"
    )
    explore_yes = sum(
        1 for _ in range(200) if sm_yes.select_category(signals) == "explore"
    )
    assert explore_yes > explore_no


def test_strategy_manager_curiosity_no_explore_when_low_novelty(tmp_workspace, monkeypatch):
    """should_explore=False 时 → 原逻辑不变"""
    scorer = NoveltyScorer(tmp_workspace / "n.json")
    boredom = BoredomTracker()
    drive = CuriosityDrive(scorer, boredom)
    # 让信号不新颖
    for _ in range(20):
        scorer.record("old_feature", "signal")
    signals = [Signal(signal_type="feature", pattern="old_feature")]

    monkeypatch.setattr(random, "random", lambda: 0.9)
    sm = StrategyManager("balanced", curiosity=drive)
    # should_explore=False → 走原逻辑 → innovate
    assert sm.select_category(signals) == "innovate"


def test_strategy_manager_repair_only_ignores_curiosity(tmp_workspace):
    """repair-only 策略即使有 curiosity 也返回 repair"""
    scorer = NoveltyScorer(tmp_workspace / "n.json")
    boredom = BoredomTracker()
    drive = CuriosityDrive(scorer, boredom)
    sm = StrategyManager("repair-only", curiosity=drive)
    for _ in range(10):
        assert sm.select_category(
            [Signal(signal_type="feature", pattern="new")]
        ) == "repair"


# ============================================================
# GEPEngine 集成测试
# ============================================================

def test_engine_accepts_curiosity(tmp_workspace):
    """GEPEngine 接受可选 curiosity 参数"""
    scorer = NoveltyScorer(tmp_workspace / "n.json")
    boredom = BoredomTracker()
    drive = CuriosityDrive(scorer, boredom)
    memory = MemoryStore(tmp_workspace)
    llm = CuriosityMockLLM()
    engine = GEPEngine(memory=memory, llm=llm, workspace=tmp_workspace, curiosity=drive)
    assert engine.curiosity is drive


def test_engine_backward_compat_no_curiosity(tmp_workspace):
    """无 curiosity → curiosity 属性为 None"""
    memory = MemoryStore(tmp_workspace)
    llm = CuriosityMockLLM()
    engine = GEPEngine(memory=memory, llm=llm, workspace=tmp_workspace)
    assert engine.curiosity is None


def test_engine_run_cycle_with_curiosity_injects_signal(tmp_workspace):
    """run_cycle 带 curiosity → 注入好奇心信号"""
    scorer = NoveltyScorer(tmp_workspace / "n.json")
    boredom = BoredomTracker()
    drive = CuriosityDrive(scorer, boredom)
    memory = MemoryStore(tmp_workspace)
    _add_reflection(memory)
    llm = CuriosityMockLLM()
    engine = GEPEngine(
        memory=memory, llm=llm, workspace=tmp_workspace, curiosity=drive
    )

    result = engine.run_cycle()
    assert result.get("curiosity_active") is True
    assert result.get("curiosity_exploring") is True
    # 注入的 curiosity 信号应出现在信号列表里
    signals = result["steps"]["2_extract_signals"]["signals"]
    assert any(s.get("signal_type") == "curiosity" for s in signals)


def test_engine_run_cycle_no_curiosity_no_injection(tmp_workspace):
    """无 curiosity → 不注入好奇心信号，curiosity_active 不存在"""
    memory = MemoryStore(tmp_workspace)
    _add_reflection(memory)
    llm = CuriosityMockLLM()
    engine = GEPEngine(memory=memory, llm=llm, workspace=tmp_workspace)

    result = engine.run_cycle()
    assert "curiosity_active" not in result
    signals = result["steps"]["2_extract_signals"]["signals"]
    assert all(s.get("signal_type") != "curiosity" for s in signals)


def test_engine_run_curious_exploration(tmp_workspace):
    """run_curious_exploration 发现目标 + 调用自进化循环"""
    scorer = NoveltyScorer(tmp_workspace / "n.json")
    boredom = BoredomTracker()
    drive = CuriosityDrive(scorer, boredom)
    registry = CapabilityRegistry(registry_path=tmp_workspace / "cap.json")
    registry.register_defaults()

    memory = MemoryStore(tmp_workspace)
    llm = CuriosityMockLLM()
    engine = GEPEngine(
        memory=memory, llm=llm, workspace=tmp_workspace,
        capability_registry=registry, curiosity=drive,
    )

    result = engine.run_curious_exploration()
    assert "status" in result
    assert "targets" in result
    assert isinstance(result["targets"], list)
    assert len(result["targets"]) > 0
    # 无 real steps → 自进化跳过
    assert result["status"] == "skipped"
    assert result["signals"] >= 1


def test_engine_run_curious_exploration_no_curiosity(tmp_workspace):
    """无 curiosity → run_curious_exploration 返回 skipped"""
    memory = MemoryStore(tmp_workspace)
    llm = CuriosityMockLLM()
    engine = GEPEngine(memory=memory, llm=llm, workspace=tmp_workspace)
    result = engine.run_curious_exploration()
    assert result["status"] == "skipped"
    assert result["targets"] == []


def test_engine_run_curious_exploration_no_registry(tmp_workspace):
    """有 curiosity 但无 capability_registry → skipped"""
    scorer = NoveltyScorer(tmp_workspace / "n.json")
    boredom = BoredomTracker()
    drive = CuriosityDrive(scorer, boredom)
    memory = MemoryStore(tmp_workspace)
    llm = CuriosityMockLLM()
    engine = GEPEngine(
        memory=memory, llm=llm, workspace=tmp_workspace, curiosity=drive
    )
    result = engine.run_curious_exploration()
    assert result["status"] == "skipped"
    assert result["targets"] == []


def test_engine_run_curious_exploration_records_novelty(tmp_workspace):
    """run_curious_exploration 后记录探索过的领域到 novelty"""
    path = tmp_workspace / "n.json"
    scorer = NoveltyScorer(path)
    boredom = BoredomTracker()
    drive = CuriosityDrive(scorer, boredom)
    registry = CapabilityRegistry(registry_path=tmp_workspace / "cap.json")
    registry.register_defaults()

    memory = MemoryStore(tmp_workspace)
    llm = CuriosityMockLLM()
    engine = GEPEngine(
        memory=memory, llm=llm, workspace=tmp_workspace,
        capability_registry=registry, curiosity=drive,
    )

    engine.run_curious_exploration()
    # 探索过的领域应被记录（novelty < 1.0）
    targets = engine.run_curious_exploration()["targets"]
    # 第二次调用，至少一个目标应已记录
    recorded = [t for t in targets if scorer.score(t, "domain") < 1.0]
    assert len(recorded) > 0
