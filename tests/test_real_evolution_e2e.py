"""真实 LLM 端到端进化测试 — 用真实 LLM 驱动完整进化循环

这些测试会发起真实网络请求，需要真实 API 凭证才能运行。
默认被 `-m "not integration"` 排除，不会在常规 `pytest tests/` 中执行。

运行方式：
    # 默认不跑
    pytest tests/

    # 显式运行（需要凭证）
    pytest tests/test_real_evolution_e2e.py -m integration -v

无凭证时测试会 pytest.skip 而非 fail。

需要的环境变量：
    DEEPSEEK_API_KEY / GROQ_API_KEY / OPENAI_API_KEY — 至少一个
    可选: <PROVIDER>_MODEL / <PROVIDER>_BASE_URL 覆盖默认值

测试内容：
    1. 真 LLM 驱动 GEP 进化循环（信号提取 + 策略生成 + 验证）
    2. 真 LLM 驱动好奇心探索（领域建议 + 自进化）
    3. 真 LLM 驱动自进化循环（代码生成 + 沙箱验证）
"""
import os
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import pytest  # noqa: E402

from superclaw.llm_router import LLMRouter  # noqa: E402
from superclaw.gep_engine import GEPEngine  # noqa: E402
from superclaw.memory import MemoryStore  # noqa: E402
from superclaw.curiosity import (  # noqa: E402
    NoveltyScorer, BoredomTracker, CuriosityDrive,
)
from superclaw.experience_learner import ExperienceLearner  # noqa: E402
from superclaw.feedback_learner import FeedbackLearner  # noqa: E402


# ============================================================
# 辅助函数
# ============================================================

_PROVIDER_DEFAULTS = {
    "deepseek": ("DEEPSEEK_API_KEY", "deepseek-chat",
                 "https://api.deepseek.com/chat/completions"),
    "groq": ("GROQ_API_KEY", "llama-3.1-8b-instant",
             "https://api.groq.com/openai/v1/chat/completions"),
    "openai": ("OPENAI_API_KEY", "gpt-4o-mini",
               "https://api.openai.com/v1/chat/completions"),
}


def _first_real_provider():
    """返回第一个有凭证的真实 provider (name, api_key)，无则返回 None。"""
    for name, (env_key, _, _) in _PROVIDER_DEFAULTS.items():
        key = os.environ.get(env_key)
        if key:
            return name, key
    return None


def _make_real_router():
    """创建带真实 provider 的 LLMRouter，无凭证返回 None。"""
    real = _first_real_provider()
    if real is None:
        return None
    name, api_key = real
    env_key, default_model, default_url = _PROVIDER_DEFAULTS[name]
    model = os.environ.get(f"{name.upper()}_MODEL", default_model)
    base_url = os.environ.get(f"{name.upper()}_BASE_URL", default_url)

    router = LLMRouter()
    router.add_provider(name, api_key=api_key, model=model,
                        base_url=base_url, priority=1)
    return router


def _add_reflection(memory):
    """添加反思记录产生信号"""
    memory.reflection.reflect({
        "phi": 0.3, "tier": 1, "fitness": 0.4,
        "mutations": 1, "knowledge": 0,
        "health": 0, "balance": 0.5,
    })


# ============================================================
# 真 LLM 端到端进化测试
# ============================================================

@pytest.mark.integration
class TestRealLLMEvolution:
    """真实 LLM 驱动的进化循环端到端测试"""

    def test_real_llm_gep_cycle(self, tmp_path):
        """真 LLM 驱动 GEP 10 步进化循环

        验证：
        - LLM 真实返回内容（非 mock）
        - 信号提取能工作
        - 策略生成能工作
        - 验证步骤能处理真实 LLM 响应
        """
        router = _make_real_router()
        if router is None:
            pytest.skip("无真实 LLM API 凭证，跳过")

        workspace = tmp_path
        memory = MemoryStore(workspace)
        _add_reflection(memory)

        engine = GEPEngine(
            memory=memory,
            llm=router,
            workspace=workspace,
        )

        result = engine.run_cycle()

        # 验证进化循环完成
        assert result["status"] in ("success", "failed")
        assert "steps" in result

        # 验证信号提取
        signals = result["steps"]["2_extract_signals"]
        assert signals["count"] > 0

        # 验证 LLM 真实调用（provider 不是 mock）
        modify_step = result["steps"]["5_execute_modify"]
        assert modify_step["provider"] != "mock"
        assert modify_step["error"] is None or modify_step["error"] == ""
        assert modify_step["content_length"] > 0

    def test_real_llm_curious_exploration(self, tmp_path):
        """真 LLM 驱动好奇心探索

        验证：
        - LLM 能生成探索领域建议
        - 好奇心探索能发现目标
        """
        router = _make_real_router()
        if router is None:
            pytest.skip("无真实 LLM API 凭证，跳过")

        workspace = tmp_path
        memory = MemoryStore(workspace)
        _add_reflection(memory)

        curiosity = CuriosityDrive(
            NoveltyScorer(workspace / "novelty.json"),
            BoredomTracker(),
        )

        engine = GEPEngine(
            memory=memory,
            llm=router,
            workspace=workspace,
            curiosity=curiosity,
        )

        result = engine.run_curious_exploration()

        # 验证好奇心探索执行
        assert result["status"] in ("success", "skipped", "no_targets",
                                     "no_gaps", "no_acquisition")
        # 应该发现了探索目标（LLM 能生成领域建议）
        if result["status"] != "skipped":
            assert "targets" in result

    def test_real_llm_with_experience_learning(self, tmp_path):
        """真 LLM + 经验学习联合

        验证：
        - 进化循环记录策略结果
        - 经验分析能工作
        """
        router = _make_real_router()
        if router is None:
            pytest.skip("无真实 LLM API 凭证，跳过")

        workspace = tmp_path
        memory = MemoryStore(workspace)
        _add_reflection(memory)

        experience_learner = ExperienceLearner(workspace / "exp.jsonl")

        engine = GEPEngine(
            memory=memory,
            llm=router,
            workspace=workspace,
            experience_learner=experience_learner,
        )

        # 跑 2 个 cycle 积累经验
        engine.run_cycle()
        engine.run_cycle()

        # 验证经验记录
        outcomes = experience_learner.recent_outcomes()
        assert len(outcomes) == 2

        # 验证经验分析
        stats = experience_learner.analyze_all()
        assert "balanced" in stats
        assert stats["balanced"].attempts == 2

    def test_real_llm_with_feedback_learning(self, tmp_path):
        """真 LLM + 反馈学习联合

        验证：
        - 反馈能被记录
        - 反馈信号能驱动进化
        """
        router = _make_real_router()
        if router is None:
            pytest.skip("无真实 LLM API 凭证，跳过")

        workspace = tmp_path
        memory = MemoryStore(workspace)
        _add_reflection(memory)

        feedback_learner = FeedbackLearner(workspace / "fb.jsonl")
        # 模拟用户反馈
        feedback_learner.detect_and_record("这个功能报错了有 bug")

        engine = GEPEngine(
            memory=memory,
            llm=router,
            workspace=workspace,
            feedback_learner=feedback_learner,
        )

        result = engine.run_feedback_driven_evolution()

        # 验证反馈驱动进化
        assert result["status"] == "success"
        assert result["stats"]["bug"] == 1
        assert result["critical_signals"] > 0
        assert result["cycle_run"] is True

    def test_real_llm_full_stack(self, tmp_path):
        """真 LLM + 全栈模块联合

        验证好奇心 + 经验学习 + 反馈学习 + 进化循环联合工作
        """
        router = _make_real_router()
        if router is None:
            pytest.skip("无真实 LLM API 凭证，跳过")

        workspace = tmp_path
        memory = MemoryStore(workspace)
        _add_reflection(memory)

        curiosity = CuriosityDrive(
            NoveltyScorer(workspace / "novelty.json"),
            BoredomTracker(),
        )
        experience_learner = ExperienceLearner(workspace / "exp.jsonl")
        feedback_learner = FeedbackLearner(workspace / "fb.jsonl")
        feedback_learner.detect_and_record("建议加个新功能")

        engine = GEPEngine(
            memory=memory,
            llm=router,
            workspace=workspace,
            curiosity=curiosity,
            experience_learner=experience_learner,
            feedback_learner=feedback_learner,
        )

        # 跑进化循环
        result = engine.run_cycle()
        assert result["status"] in ("success", "failed")

        # 验证经验记录
        outcomes = experience_learner.recent_outcomes()
        assert len(outcomes) == 1

        # 验证反馈信号被提取
        signals_info = result["steps"]["2_extract_signals"]
        assert signals_info["count"] > 0
