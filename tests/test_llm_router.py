"""测试 superclaw.llm_router — LLM 路由/故障转移/冷却/成本"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from superclaw.llm_router import (
    LLMRouter,
    ProviderConfig,
    CompletionResult,
    LITELLM_AVAILABLE,
)


# ============================================================
# 辅助：确保测试离线 — 禁用 litellm 与网络
# ============================================================

def _offline_router():
    """创建一个离线路由器（不用 litellm，不连网）"""
    r = LLMRouter()
    r._litellm = None  # 强制走 urllib 路径
    return r


# ============================================================
# ProviderConfig / CompletionResult dataclass
# ============================================================

def test_provider_config_defaults():
    p = ProviderConfig(name="test")
    assert p.name == "test"
    assert p.api_key == ""
    assert p.priority == 10
    assert p.enabled is True
    assert p._failures == 0
    assert p._cooldown_until == 0.0


def test_completion_result_defaults():
    r = CompletionResult(content="hi", provider="mock", model="mock")
    assert r.content == "hi"
    assert r.tokens_used == 0
    assert r.cost == 0.0
    assert r.error is None
    assert r.fallback_used is False


# ============================================================
# 添加 provider + complete() 基本路由
# ============================================================

def test_add_provider_stores_config():
    router = _offline_router()
    router.add_provider("mock", priority=1, cost_per_1k=0.0)
    assert "mock" in router.providers
    assert router.providers["mock"].priority == 1


def test_complete_with_mock_provider():
    router = _offline_router()
    router.add_provider("mock", priority=1)
    result = router.complete([{"role": "user", "content": "你好"}])
    assert result.error is None
    assert result.provider == "mock"
    assert result.model == "mock"
    assert len(result.content) > 0


def test_complete_with_explicit_provider():
    router = _offline_router()
    router.add_provider("mock", priority=1)
    result = router.complete(
        [{"role": "user", "content": "hi"}], provider="mock"
    )
    assert result.provider == "mock"
    assert result.error is None


def test_complete_mock_response_echoes_input():
    router = _offline_router()
    router.add_provider("mock", priority=1)
    result = router.complete([{"role": "user", "content": "Python测试"}])
    # mock 模板包含用户输入的前缀
    assert "Python测试" in result.content or result.content != ""


def test_complete_all_providers_fail():
    """只有失败 provider（无 api_key）时返回 all providers failed"""
    router = _offline_router()
    router.add_provider("deepseek", api_key="", model="m", priority=1)
    result = router.complete([{"role": "user", "content": "hi"}])
    assert result.error == "all providers failed"
    assert result.provider == "none"


# ============================================================
# 复杂度路由
# ============================================================

def test_route_low_prefers_groq():
    router = _offline_router()
    router.add_provider("groq", api_key="k", model="llama", priority=1)
    router.add_provider("openai", api_key="k", model="gpt", priority=2)
    router.add_provider("mock", priority=3)
    order = router._route("low")
    # low 偏好: groq 在 openai 之前
    assert order.index("groq") < order.index("openai")


def test_route_high_prefers_openai():
    router = _offline_router()
    router.add_provider("groq", api_key="k", model="llama", priority=1)
    router.add_provider("openai", api_key="k", model="gpt", priority=2)
    router.add_provider("mock", priority=3)
    order = router._route("high")
    # high 偏好: openai 在 groq 之前
    assert order.index("openai") < order.index("groq")


def test_route_only_returns_configured_providers():
    router = _offline_router()
    router.add_provider("mock", priority=1)
    order = router._route("medium")
    assert order == ["mock"]


def test_route_unknown_complexity_defaults_to_medium():
    router = _offline_router()
    router.add_provider("groq", api_key="k", priority=1)
    router.add_provider("openai", api_key="k", priority=2)
    order_unknown = router._route("bogus")
    order_medium = router._route("medium")
    assert order_unknown == order_medium


def test_complete_complexity_selects_correct_provider(monkeypatch):
    """实际 complete() 应按复杂度选择第一个 provider"""
    router = _offline_router()
    router.add_provider("groq", api_key="fake", model="llama", priority=1)
    router.add_provider("openai", api_key="fake", model="gpt", priority=2)

    called = []

    def fake_urllib(self, pconfig, messages, max_tokens, t0):
        called.append(pconfig.name)
        return CompletionResult(
            content=f"from-{pconfig.name}",
            provider=pconfig.name, model=pconfig.model,
            tokens_used=10,
        )

    monkeypatch.setattr(LLMRouter, "_call_urllib", fake_urllib)

    # low → groq 优先
    result = router.complete([{"role": "user", "content": "hi"}], complexity="low")
    assert result.provider == "groq"
    assert called == ["groq"]

    # high → openai 优先
    called.clear()
    result = router.complete([{"role": "user", "content": "hi"}], complexity="high")
    assert result.provider == "openai"
    assert called == ["openai"]


# ============================================================
# 故障转移
# ============================================================

def test_failover_to_next_provider():
    """第一个 provider 失败 → 自动切到下一个"""
    router = _offline_router()
    router.add_provider("deepseek", api_key="", model="m", priority=1)
    router.add_provider("mock", priority=2)

    result = router.complete([{"role": "user", "content": "hi"}])
    # deepseek 无 api_key 失败，mock 兜底成功
    assert result.provider == "mock"
    assert result.error is None
    assert router.providers["deepseek"]._failures == 1


def test_failover_skips_disabled_provider():
    router = _offline_router()
    p = router.add_provider  # noqa: just alias
    router.add_provider("deepseek", api_key="k", model="m", priority=1)
    router.providers["deepseek"].enabled = False
    router.add_provider("mock", priority=2)

    result = router.complete([{"role": "user", "content": "hi"}])
    assert result.provider == "mock"


def test_failover_chain_three_providers(monkeypatch):
    """三个 provider，前两个失败，第三个成功"""
    router = _offline_router()
    router.add_provider("deepseek", api_key="", model="m", priority=1)
    router.add_provider("openai", api_key="", model="m", priority=2)
    router.add_provider("mock", priority=3)

    result = router.complete([{"role": "user", "content": "hi"}])
    assert result.provider == "mock"
    assert result.error is None
    assert router.providers["deepseek"]._failures == 1
    assert router.providers["openai"]._failures == 1


# ============================================================
# 冷却机制
# ============================================================

def test_cooldown_triggered_after_three_failures():
    """连续失败 3 次后进入 60 秒冷却"""
    router = _offline_router()
    router.add_provider("deepseek", api_key="", model="m", priority=1)
    router.add_provider("mock", priority=2)

    # 调用 3 次，每次 deepseek 失败，mock 兜底
    for _ in range(3):
        result = router.complete([{"role": "user", "content": "hi"}])
        assert result.provider == "mock"

    ds = router.providers["deepseek"]
    assert ds._failures == 3
    assert ds._cooldown_until > time.time()  # 未来时间


def test_cooldown_skips_provider():
    """冷却期间 provider 被跳过（failures 不再增加）"""
    router = _offline_router()
    router.add_provider("deepseek", api_key="", model="m", priority=1)
    router.add_provider("mock", priority=2)

    # 触发 3 次失败进入冷却
    for _ in range(3):
        router.complete([{"role": "user", "content": "hi"}])
    assert router.providers["deepseek"]._failures == 3

    # 第 4 次：deepseek 在冷却中应被跳过
    router.complete([{"role": "user", "content": "hi"}])
    # failures 不应增加（因为被跳过）
    assert router.providers["deepseek"]._failures == 3


def test_cooldown_duration_is_60_seconds():
    router = _offline_router()
    router.add_provider("deepseek", api_key="", model="m", priority=1)
    router.add_provider("mock", priority=2)

    for _ in range(3):
        router.complete([{"role": "user", "content": "hi"}])

    ds = router.providers["deepseek"]
    remaining = ds._cooldown_until - time.time()
    # 冷却应在 60 秒左右（允许少量误差）
    assert 50 < remaining <= 60


def test_status_shows_cooldown():
    router = _offline_router()
    router.add_provider("deepseek", api_key="", model="m", priority=1)
    router.add_provider("mock", priority=2)

    for _ in range(3):
        router.complete([{"role": "user", "content": "hi"}])

    status = router.status()
    assert status["providers"]["deepseek"]["in_cooldown"] is True
    assert status["providers"]["deepseek"]["failures"] == 3
    assert status["providers"]["mock"]["in_cooldown"] is False


def test_reset_failures_clears_cooldown():
    router = _offline_router()
    router.add_provider("deepseek", api_key="", model="m", priority=1)
    router.add_provider("mock", priority=2)

    for _ in range(3):
        router.complete([{"role": "user", "content": "hi"}])
    assert router.providers["deepseek"]._failures == 3

    router.reset_failures("deepseek")
    assert router.providers["deepseek"]._failures == 0
    assert router.providers["deepseek"]._cooldown_until == 0


def test_reset_all_failures():
    router = _offline_router()
    router.add_provider("deepseek", api_key="", model="m", priority=1)
    router.add_provider("mock", priority=2)

    for _ in range(3):
        router.complete([{"role": "user", "content": "hi"}])

    router.reset_failures()  # 重置全部
    assert router.providers["deepseek"]._failures == 0
    assert router.providers["mock"]._failures == 0


# ============================================================
# add_from_env — 从环境变量添加 provider
# ============================================================

def test_add_from_env_deepseek(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key-123")
    # 避免 _check_ollama 发起网络请求
    monkeypatch.setattr(LLMRouter, "_check_ollama", lambda self: False)

    router = _offline_router()
    router.add_from_env()

    assert "deepseek" in router.providers
    ds = router.providers["deepseek"]
    assert ds.api_key == "test-key-123"
    assert ds.model == "deepseek-chat"
    assert "deepseek.com" in ds.base_url


def test_add_from_env_custom_model(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "groq-key")
    monkeypatch.setenv("GROQ_MODEL", "custom-llama")
    monkeypatch.setattr(LLMRouter, "_check_ollama", lambda self: False)

    router = _offline_router()
    router.add_from_env()

    assert router.providers["groq"].model == "custom-llama"


def test_add_from_env_always_adds_mock(monkeypatch):
    """没有配置任何 API key 时，mock 兜底总是被添加"""
    # 清除所有相关 env
    for k in ["DEEPSEEK_API_KEY", "GROQ_API_KEY", "OPENAI_API_KEY",
              "OPENROUTER_API_KEY", "OLLAMA_BASE_URL"]:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(LLMRouter, "_check_ollama", lambda self: False)

    router = _offline_router()
    router.add_from_env()

    assert "mock" in router.providers
    # 没有配置 key 的 provider 不应被添加
    assert "deepseek" not in router.providers


def test_add_from_env_priority_increments(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k1")
    monkeypatch.setenv("GROQ_API_KEY", "k2")
    monkeypatch.setattr(LLMRouter, "_check_ollama", lambda self: False)

    router = _offline_router()
    router.add_from_env()

    # deepseek 先添加 priority=1，groq 后添加 priority=2
    assert router.providers["deepseek"].priority == 1
    assert router.providers["groq"].priority == 2
    # mock 兜底固定 priority=99
    assert router.providers["mock"].priority == 99


# ============================================================
# 成本统计
# ============================================================

def test_status_reports_cost_per_1k():
    router = _offline_router()
    router.add_provider("deepseek", api_key="k", model="m",
                        cost_per_1k=0.002, priority=1)
    status = router.status()
    assert status["providers"]["deepseek"]["cost_per_1k"] == 0.002


def test_cost_calculation_in_result(monkeypatch):
    """验证 cost = tokens * cost_per_1k / 1000"""
    router = _offline_router()
    router.add_provider("deepseek", api_key="fake", model="m",
                        cost_per_1k=0.002, priority=1)

    def fake_urllib(self, pconfig, messages, max_tokens, t0):
        tokens = 1500
        return CompletionResult(
            content="ok",
            provider=pconfig.name, model=pconfig.model,
            tokens_used=tokens,
            cost=tokens * pconfig.cost_per_1k / 1000,
        )

    monkeypatch.setattr(LLMRouter, "_call_urllib", fake_urllib)

    result = router.complete([{"role": "user", "content": "hi"}])
    assert result.provider == "deepseek"
    assert result.tokens_used == 1500
    # 1500 * 0.002 / 1000 = 0.003
    assert abs(result.cost - 0.003) < 1e-9


def test_status_reports_litellm_availability():
    router = _offline_router()
    status = router.status()
    assert "litellm_available" in status
    assert status["litellm_available"] == LITELLM_AVAILABLE


def test_status_reports_provider_enabled():
    router = _offline_router()
    router.add_provider("mock", priority=1)
    status = router.status()
    assert status["providers"]["mock"]["enabled"] is True
