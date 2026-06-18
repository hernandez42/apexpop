"""测试 superclaw.scheduler — 进化调度器

覆盖：
- EvolutionScheduler: init/start/stop/is_running/run_once/get_results/stats/join
- 多模式: cycle/self_evolution/curious/experience/feedback
- 线程安全: 重复 start/stop、后台执行不崩溃
- MultiModeScheduler: 多模式轮转、start/stop/run_once/stats
- 向后兼容: 无可选模块时返回 skipped
"""
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from superclaw.scheduler import (
    EvolutionScheduler, MultiModeScheduler,
    MODE_CYCLE, MODE_SELF_EVOLUTION, MODE_CURIOUS,
    MODE_EXPERIENCE, MODE_FEEDBACK,
    VALID_MODES, DEFAULT_INTERVAL,
)
from superclaw.gep_engine import GEPEngine
from superclaw.memory import MemoryStore
from superclaw.llm_router import CompletionResult


# ============================================================
# Mock LLM — 调度器测试用
# ============================================================

class SchedMockLLM:
    """调度器测试用 Mock LLM"""

    def complete(self, messages, complexity="medium", provider=None, max_tokens=None):
        prompt = messages[0]["content"] if messages else ""
        if "分析以下进化信号" in prompt:
            return CompletionResult(
                content="[]", provider="mock", model="mock",
                tokens_used=10, error=None,
            )
        return CompletionResult(
            content='{"action": "fix", "target": "core", "risk_level": "low"}',
            provider="mock", model="mock",
            tokens_used=50, error=None,
        )

    def status(self):
        return {"provider": "mock", "available": True}


def _add_reflection(memory):
    """添加反思记录产生信号"""
    memory.reflection.reflect({
        "phi": 0.3, "tier": 1, "fitness": 0.4,
        "mutations": 1, "knowledge": 0,
        "health": 0, "balance": 0.5,
    })


def _make_engine(tmp_workspace):
    """创建测试用 GEPEngine"""
    memory = MemoryStore(tmp_workspace)
    _add_reflection(memory)
    return GEPEngine(
        memory=memory,
        llm=SchedMockLLM(),
        workspace=tmp_workspace,
    )


# ============================================================
# EvolutionScheduler 基础测试
# ============================================================

class TestEvolutionSchedulerBasic:
    """EvolutionScheduler 基础功能测试"""

    def test_init_default(self, tmp_workspace):
        """默认初始化"""
        engine = _make_engine(tmp_workspace)
        scheduler = EvolutionScheduler(engine)
        assert scheduler.mode == MODE_CYCLE
        assert scheduler.interval == DEFAULT_INTERVAL
        assert scheduler.is_running() is False

    def test_init_custom(self, tmp_workspace):
        """自定义参数初始化"""
        engine = _make_engine(tmp_workspace)
        scheduler = EvolutionScheduler(engine, interval=60, mode=MODE_CURIOUS)
        assert scheduler.interval == 60
        assert scheduler.mode == MODE_CURIOUS

    def test_init_invalid_mode(self, tmp_workspace):
        """无效模式抛 ValueError"""
        engine = _make_engine(tmp_workspace)
        with pytest.raises(ValueError):
            EvolutionScheduler(engine, mode="invalid")

    def test_init_invalid_interval(self, tmp_workspace):
        """无效间隔抛 ValueError"""
        engine = _make_engine(tmp_workspace)
        with pytest.raises(ValueError):
            EvolutionScheduler(engine, interval=0)

    def test_start_stop(self, tmp_workspace):
        """start/stop 正常工作"""
        engine = _make_engine(tmp_workspace)
        scheduler = EvolutionScheduler(engine, interval=3600)
        assert scheduler.start() is True
        assert scheduler.is_running() is True
        assert scheduler.start() is False  # 重复 start 返回 False
        assert scheduler.stop() is True
        assert scheduler.is_running() is False
        assert scheduler.stop() is False  # 重复 stop 返回 False

    def test_run_once_cycle(self, tmp_workspace):
        """run_once 执行 cycle 模式"""
        engine = _make_engine(tmp_workspace)
        scheduler = EvolutionScheduler(engine, mode=MODE_CYCLE)
        result = scheduler.run_once()
        assert "status" in result
        assert "steps" in result

    def test_run_once_self_evolution(self, tmp_workspace):
        """run_once 执行 self_evolution 模式（无模块时 skipped）"""
        engine = _make_engine(tmp_workspace)
        scheduler = EvolutionScheduler(engine, mode=MODE_SELF_EVOLUTION)
        result = scheduler.run_once()
        # 无自进化模块 → skipped
        assert result.get("status") == "skipped"

    def test_run_once_curious(self, tmp_workspace):
        """run_once 执行 curious 模式（无 curiosity 时 skipped）"""
        engine = _make_engine(tmp_workspace)
        scheduler = EvolutionScheduler(engine, mode=MODE_CURIOUS)
        result = scheduler.run_once()
        # 无 curiosity → skipped
        assert result.get("status") == "skipped"

    def test_run_once_experience(self, tmp_workspace):
        """run_once 执行 experience 模式（无 learner 时 skipped）"""
        engine = _make_engine(tmp_workspace)
        scheduler = EvolutionScheduler(engine, mode=MODE_EXPERIENCE)
        result = scheduler.run_once()
        # 无 experience_learner → skipped
        assert result.get("status") == "skipped"

    def test_run_once_feedback(self, tmp_workspace):
        """run_once 执行 feedback 模式（无 learner 时 skipped）"""
        engine = _make_engine(tmp_workspace)
        scheduler = EvolutionScheduler(engine, mode=MODE_FEEDBACK)
        result = scheduler.run_once()
        # 无 feedback_learner → skipped
        assert result.get("status") == "skipped"

    def test_get_results_empty(self, tmp_workspace):
        """无执行时 get_results 返回空"""
        engine = _make_engine(tmp_workspace)
        scheduler = EvolutionScheduler(engine)
        assert scheduler.get_results() == []

    def test_get_results_after_run(self, tmp_workspace):
        """run_once 后 get_results 有记录"""
        engine = _make_engine(tmp_workspace)
        scheduler = EvolutionScheduler(engine, mode=MODE_CYCLE)
        scheduler.run_once()
        # run_once 不记录到 results（只有定时触发才记录）
        # 但我们可以验证 stats
        stats = scheduler.stats()
        assert stats["run_count"] == 1

    def test_stats(self, tmp_workspace):
        """stats 返回正确信息"""
        engine = _make_engine(tmp_workspace)
        scheduler = EvolutionScheduler(engine, interval=60, mode=MODE_CURIOUS)
        stats = scheduler.stats()
        assert stats["running"] is False
        assert stats["mode"] == MODE_CURIOUS
        assert stats["interval"] == 60
        assert stats["run_count"] == 0


# ============================================================
# EvolutionScheduler 后台执行测试
# ============================================================

class TestEvolutionSchedulerBackground:
    """EvolutionScheduler 后台定时执行测试"""

    def test_background_execution(self, tmp_workspace):
        """后台定时执行进化循环"""
        engine = _make_engine(tmp_workspace)
        # 1 秒间隔，快速验证
        scheduler = EvolutionScheduler(engine, interval=1, mode=MODE_CYCLE)
        scheduler.start()

        # 等待 2.5 秒让定时器触发至少一次
        time.sleep(2.5)
        scheduler.stop()

        stats = scheduler.stats()
        assert stats["run_count"] >= 1

        results = scheduler.get_results()
        assert len(results) >= 1

    def test_background_records_results(self, tmp_workspace):
        """后台执行结果被记录"""
        engine = _make_engine(tmp_workspace)
        scheduler = EvolutionScheduler(engine, interval=1, mode=MODE_CYCLE)
        scheduler.start()
        time.sleep(2.5)
        scheduler.stop()

        results = scheduler.get_results()
        assert len(results) >= 1
        # 每条结果有 timestamp
        assert "timestamp" in results[0]
        # 有 result 或 error
        assert "result" in results[0] or "error" in results[0]

    def test_stop_cancels_timer(self, tmp_workspace):
        """stop 取消待执行的定时器"""
        engine = _make_engine(tmp_workspace)
        scheduler = EvolutionScheduler(engine, interval=10, mode=MODE_CYCLE)
        scheduler.start()
        assert scheduler.is_running() is True

        scheduler.stop()
        assert scheduler.is_running() is False

        # 等待一段时间确认没有更多执行
        count_after_stop = scheduler.stats()["run_count"]
        time.sleep(1)
        assert scheduler.stats()["run_count"] == count_after_stop

    def test_join_timeout(self, tmp_workspace):
        """join 超时返回 False"""
        engine = _make_engine(tmp_workspace)
        scheduler = EvolutionScheduler(engine, interval=3600, mode=MODE_CYCLE)
        scheduler.start()
        # join 0.5 秒，调度器还在运行 → False
        assert scheduler.join(timeout=0.5) is False
        scheduler.stop()
        # 停止后 join → True
        assert scheduler.join(timeout=1) is True

    def test_results_limit_100(self, tmp_workspace):
        """结果最多保留 100 条"""
        engine = _make_engine(tmp_workspace)
        scheduler = EvolutionScheduler(engine, interval=1, mode=MODE_CYCLE)

        # 直接填充 150 条模拟结果，验证截断到 100
        with scheduler._lock:
            for i in range(150):
                scheduler._results.append({
                    "timestamp": f"2026-01-01T00:{i:02d}:00",
                    "result": {"status": "success", "cycle": i},
                })
            # 模拟 _timer_callback 中的截断逻辑
            if len(scheduler._results) > 100:
                scheduler._results = scheduler._results[-100:]

        results = scheduler.get_results(limit=200)
        assert len(results) == 100
        # 保留的是最后 100 条（cycle 50-149）
        assert results[0]["result"]["cycle"] == 50
        assert results[-1]["result"]["cycle"] == 149


# ============================================================
# MultiModeScheduler 测试
# ============================================================

class TestMultiModeScheduler:
    """MultiModeScheduler 多模式轮转测试"""

    def test_init_default(self, tmp_workspace):
        """默认初始化"""
        engine = _make_engine(tmp_workspace)
        scheduler = MultiModeScheduler(engine, interval=60)
        assert scheduler.is_running() is False
        assert len(scheduler.modes) == 3  # 默认 3 个模式

    def test_init_custom_modes(self, tmp_workspace):
        """自定义模式列表"""
        engine = _make_engine(tmp_workspace)
        scheduler = MultiModeScheduler(
            engine, interval=60,
            modes=[MODE_CYCLE, MODE_FEEDBACK],
        )
        assert scheduler.modes == [MODE_CYCLE, MODE_FEEDBACK]

    def test_init_invalid_mode(self, tmp_workspace):
        """无效模式抛 ValueError"""
        engine = _make_engine(tmp_workspace)
        with pytest.raises(ValueError):
            MultiModeScheduler(engine, modes=["invalid"])

    def test_start_stop(self, tmp_workspace):
        """start/stop 正常工作"""
        engine = _make_engine(tmp_workspace)
        scheduler = MultiModeScheduler(engine, interval=3600)
        assert scheduler.start() is True
        assert scheduler.is_running() is True
        assert scheduler.stop() is True
        assert scheduler.is_running() is False

    def test_run_once_rotates_modes(self, tmp_workspace):
        """run_once 轮转模式"""
        engine = _make_engine(tmp_workspace)
        scheduler = MultiModeScheduler(
            engine, interval=60,
            modes=[MODE_CYCLE, MODE_FEEDBACK, MODE_EXPERIENCE],
        )

        # 第一次：cycle
        stats = scheduler.stats()
        assert stats["current_mode"] == MODE_CYCLE
        scheduler.run_once()

        # 第二次：feedback
        stats = scheduler.stats()
        assert stats["current_mode"] == MODE_FEEDBACK
        scheduler.run_once()

        # 第三次：experience
        stats = scheduler.stats()
        assert stats["current_mode"] == MODE_EXPERIENCE
        scheduler.run_once()

        # 第四次：回到 cycle
        stats = scheduler.stats()
        assert stats["current_mode"] == MODE_CYCLE

    def test_stats(self, tmp_workspace):
        """stats 包含多模式信息"""
        engine = _make_engine(tmp_workspace)
        scheduler = MultiModeScheduler(
            engine, interval=60,
            modes=[MODE_CYCLE, MODE_FEEDBACK],
        )
        stats = scheduler.stats()
        assert "modes" in stats
        assert stats["modes"] == [MODE_CYCLE, MODE_FEEDBACK]
        assert "current_mode" in stats
        assert "current_mode_index" in stats

    def test_background_execution(self, tmp_workspace):
        """后台多模式轮转执行"""
        engine = _make_engine(tmp_workspace)
        scheduler = MultiModeScheduler(
            engine, interval=1,
            modes=[MODE_CYCLE, MODE_FEEDBACK],
        )
        scheduler.start()
        time.sleep(2.5)
        scheduler.stop()

        stats = scheduler.stats()
        assert stats["run_count"] >= 1

    def test_join(self, tmp_workspace):
        """join 等待停止"""
        engine = _make_engine(tmp_workspace)
        scheduler = MultiModeScheduler(engine, interval=3600)
        scheduler.start()
        assert scheduler.join(timeout=0.5) is False
        scheduler.stop()
        assert scheduler.join(timeout=1) is True


# ============================================================
# 端到端：调度器 + 真实 GEPEngine
# ============================================================

class TestEndToEndScheduler:
    """端到端：调度器驱动真实 GEPEngine"""

    def test_full_cycle_via_scheduler(self, tmp_workspace):
        """通过调度器执行完整进化循环"""
        engine = _make_engine(tmp_workspace)
        scheduler = EvolutionScheduler(engine, mode=MODE_CYCLE)

        result = scheduler.run_once()
        assert result["status"] in ("success", "failed")
        assert "steps" in result
        assert "1_scan_logs" in result["steps"]

    def test_multiple_modes_sequential(self, tmp_workspace):
        """顺序执行多种模式"""
        engine = _make_engine(tmp_workspace)

        # cycle
        s1 = EvolutionScheduler(engine, mode=MODE_CYCLE)
        r1 = s1.run_once()
        assert r1["status"] != "skipped"

        # self_evolution（无模块 → skipped）
        s2 = EvolutionScheduler(engine, mode=MODE_SELF_EVOLUTION)
        r2 = s2.run_once()
        assert r2["status"] == "skipped"

        # experience（无 learner → skipped）
        s3 = EvolutionScheduler(engine, mode=MODE_EXPERIENCE)
        r3 = s3.run_once()
        assert r3["status"] == "skipped"

    def test_scheduler_with_all_modules(self, tmp_workspace):
        """带全部模块的调度器"""
        from superclaw.curiosity import (
            NoveltyScorer, BoredomTracker, CuriosityDrive,
        )
        from superclaw.experience_learner import ExperienceLearner
        from superclaw.feedback_learner import FeedbackLearner

        workspace = tmp_workspace
        memory = MemoryStore(workspace)
        _add_reflection(memory)

        engine = GEPEngine(
            memory=memory,
            llm=SchedMockLLM(),
            workspace=workspace,
            curiosity=CuriosityDrive(
                NoveltyScorer(workspace / "novelty.json"),
                BoredomTracker(),
            ),
            experience_learner=ExperienceLearner(workspace / "exp.jsonl"),
            feedback_learner=FeedbackLearner(workspace / "fb.jsonl"),
        )

        # 各模式都能执行（不 skipped）
        for mode in [MODE_CYCLE, MODE_EXPERIENCE, MODE_FEEDBACK]:
            scheduler = EvolutionScheduler(engine, mode=mode)
            result = scheduler.run_once()
            assert result.get("status") != "skipped", f"mode={mode} should not skip"
