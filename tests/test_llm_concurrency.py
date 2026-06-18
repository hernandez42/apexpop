"""LLM 路由的并发与冷却场景测试。

覆盖 superclaw.llm_router.LLMRouter 的：
- 连续失败 3 次进入冷却 + 第 4 次跳过走 fallback
- 冷却期过后 provider 恢复可用 + reset_failures 清除状态
- 多线程并发调用 complete() 不崩溃、结果一致
- 故障转移链（A 失败 → B 失败 → C 成功）
- 所有 provider 都失败的兜底行为
- 并发调用后成本统计可汇总、不报错

说明：LLMRouter 没有内置锁，也没有 total_cost/total_calls 字段，
本测试如实验证真实行为（不造假）：
- 并发测试只证明不崩溃 + 结果有效；
- 成本统计从返回的 CompletionResult 汇总计算。
"""
import os
import sys
import time
import threading
from unittest.mock import patch

# 让 superclaw 包可导入
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from superclaw.llm_router import LLMRouter, CompletionResult


MESSAGES = [{"role": "user", "content": "hello"}]
# 成功 provider 模拟返回的 token 数（与 _build_router 中 success 行为保持一致）
SUCCESS_TOKENS = 10


def _build_router(specs):
    """构造一个 LLMRouter，并把 _call_provider 替换为按 spec 分发的版本。

    specs: list of dict，每个 dict 含：
        name: str           - provider 名称（刻意用 alpha/beta/gamma 等不在
                              _COMPLEXITY_PREFERENCE 里的名字，使路由完全按 priority 排序）
        priority: int       - 越小优先级越高
        behavior:           - "fail" / "success" / callable(pconfig, messages, max_tokens) -> CompletionResult
        cost_per_1k: float  - 可选，默认 0.0
    返回 router。
    """
    router = LLMRouter()
    for spec in specs:
        router.add_provider(
            name=spec["name"],
            priority=spec.get("priority", 10),
            cost_per_1k=spec.get("cost_per_1k", 0.0),
            api_key="fake-key",  # 给个假 key，避免走到真实 _call_provider 时因缺 key 自动失败
        )

    behaviors = {spec["name"]: spec["behavior"] for spec in specs}

    def fake_call_provider(pconfig, messages, max_tokens):
        beh = behaviors.get(pconfig.name)
        if beh == "fail":
            return CompletionResult(
                content="", provider=pconfig.name, model=pconfig.model,
                error=f"{pconfig.name} simulated failure",
            )
        if beh == "success":
            time.sleep(0.005)  # 模拟 provider 延迟
            return CompletionResult(
                content=f"ok-{pconfig.name}", provider=pconfig.name,
                model=pconfig.model, tokens_used=SUCCESS_TOKENS,
                cost=SUCCESS_TOKENS * pconfig.cost_per_1k / 1000,
            )
        if callable(beh):
            return beh(pconfig, messages, max_tokens)
        return CompletionResult(
            content="", provider=pconfig.name, model=pconfig.model,
            error="unknown behavior",
        )

    router._call_provider = fake_call_provider
    return router


def _make_fail_then_succeed(fail_count):
    """返回一个 callable：前 fail_count 次返回错误，之后返回成功。"""
    state = {"fails": 0, "oks": 0}

    def fn(pconfig, messages, max_tokens):
        if state["fails"] < fail_count:
            state["fails"] += 1
            return CompletionResult(
                content="", provider=pconfig.name, model=pconfig.model,
                error=f"{pconfig.name} simulated failure #{state['fails']}",
            )
        state["oks"] += 1
        time.sleep(0.005)
        return CompletionResult(
            content=f"ok-{pconfig.name}", provider=pconfig.name,
            model=pconfig.model, tokens_used=SUCCESS_TOKENS,
            cost=SUCCESS_TOKENS * pconfig.cost_per_1k / 1000,
        )

    fn.state = state
    return fn


# --------------------------------------------------------------------------- #
# 冷却场景
# --------------------------------------------------------------------------- #
def test_cooldown_after_three_failures():
    """连续失败 3 次后 provider 进入冷却，第 4 次调用跳过它走 fallback。"""
    router = _build_router([
        {"name": "primary", "priority": 1, "behavior": "fail"},
        {"name": "fallback", "priority": 2, "behavior": "success"},
    ])
    primary = router.providers["primary"]
    fallback = router.providers["fallback"]

    # 前 3 次调用：primary 失败，fallback 兜底成功
    for _ in range(3):
        result = router.complete(MESSAGES)
        assert result.provider == "fallback"
        assert result.error is None

    # primary 累计 3 次失败，进入冷却
    assert primary._failures == 3
    assert primary._cooldown_until > time.time()
    assert primary._last_failure > 0
    # fallback 一直成功，不应有失败计数
    assert fallback._failures == 0

    failures_before = primary._failures
    cooldown_until = primary._cooldown_until

    # 第 4 次调用：primary 在冷却中被跳过，直接走 fallback
    result = router.complete(MESSAGES)
    assert result.provider == "fallback"
    assert result.error is None

    # primary 被跳过，失败计数与冷却时间都不变
    assert primary._failures == failures_before
    assert primary._cooldown_until == cooldown_until


# --------------------------------------------------------------------------- #
# 冷却恢复
# --------------------------------------------------------------------------- #
def test_cooldown_recovery_after_time_passes():
    """冷却期过去后 provider 恢复可用；reset_failures 清除失败计数与冷却。"""
    router = _build_router([
        {"name": "primary", "priority": 1, "behavior": _make_fail_then_succeed(3)},
        {"name": "fallback", "priority": 2, "behavior": "success"},
    ])
    primary = router.providers["primary"]

    # 让 primary 失败 3 次进入冷却（每次 fallback 兜底）
    for _ in range(3):
        router.complete(MESSAGES)
    assert primary._failures == 3
    assert primary._cooldown_until > time.time()

    # mock time.time 让冷却期过去
    fake_now = [primary._cooldown_until + 1]

    def fake_time():
        return fake_now[0]

    with patch("superclaw.llm_router.time.time", fake_time):
        # 冷却已过期，primary 被重新尝试；此时 callable 已转为成功
        result = router.complete(MESSAGES)

    # primary 恢复可用并被选中
    assert result.provider == "primary"
    assert result.error is None
    assert result.content == "ok-primary"
    # _cooldown_until 已落在“过去”（不再构成冷却屏障）
    assert primary._cooldown_until <= fake_now[0]

    # 实现不会在恢复/成功时自动重置 _failures，用 reset_failures 显式清除
    router.reset_failures("primary")
    assert primary._failures == 0
    assert primary._cooldown_until == 0

    # 清除后再次调用，primary 仍可用（callable 已是成功态）
    result = router.complete(MESSAGES)
    assert result.provider == "primary"
    assert result.error is None


# --------------------------------------------------------------------------- #
# 并发安全
# --------------------------------------------------------------------------- #
def test_concurrent_complete_does_not_crash():
    """多线程并发调用 complete() 不抛异常、结果一致。"""
    router = _build_router([
        {"name": "alpha", "priority": 1, "behavior": "success"},
        {"name": "beta", "priority": 2, "behavior": "success"},
    ])

    n_threads = 12
    barrier = threading.Barrier(n_threads)
    results = [None] * n_threads
    errors = [None] * n_threads

    def worker(i):
        try:
            barrier.wait()  # 同步并发起点
            results[i] = router.complete(MESSAGES)
        except Exception as exc:  # noqa: BLE001 - 测试需要捕获任意异常
            errors[i] = exc

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # 不应有任何线程抛异常
    assert all(e is None for e in errors), f"threads raised: {errors}"
    # 所有结果都有效且成功
    assert all(r is not None for r in results)
    for r in results:
        assert r.error is None
    # alpha 优先级最高且永远成功，所有线程应得到一致的 alpha 结果
    assert all(r.provider == "alpha" for r in results)
    assert all(r.content == "ok-alpha" for r in results)


# --------------------------------------------------------------------------- #
# 故障转移链
# --------------------------------------------------------------------------- #
def test_failover_chain():
    """A 失败 → B 失败 → C 成功：complete() 最终用 C 返回，A/B 各 +1。"""
    router = _build_router([
        {"name": "alpha", "priority": 1, "behavior": "fail"},
        {"name": "beta", "priority": 2, "behavior": "fail"},
        {"name": "gamma", "priority": 3, "behavior": "success"},
    ])

    result = router.complete(MESSAGES)

    assert result.provider == "gamma"
    assert result.error is None
    assert result.content == "ok-gamma"
    assert router.providers["alpha"]._failures == 1
    assert router.providers["beta"]._failures == 1
    assert router.providers["gamma"]._failures == 0


# --------------------------------------------------------------------------- #
# 全部失败兜底
# --------------------------------------------------------------------------- #
def test_all_providers_fail_returns_error_result():
    """所有 provider 都失败 — 返回兜底错误结果（实现不抛异常）。"""
    router = _build_router([
        {"name": "alpha", "priority": 1, "behavior": "fail"},
        {"name": "beta", "priority": 2, "behavior": "fail"},
    ])

    result = router.complete(MESSAGES)

    # 实现返回带 error 的 CompletionResult，而非抛异常
    assert result.error is not None
    assert result.error == "all providers failed"
    assert result.provider == "none"
    # 两个 provider 各被尝试一次、各 +1 失败
    assert router.providers["alpha"]._failures == 1
    assert router.providers["beta"]._failures == 1


# --------------------------------------------------------------------------- #
# 成本统计并发
# --------------------------------------------------------------------------- #
def test_concurrent_cost_statistics():
    """多线程调用后成本统计可汇总、不报错。"""
    cost_per_1k = 0.5
    router = _build_router([
        {"name": "alpha", "priority": 1, "behavior": "success",
         "cost_per_1k": cost_per_1k},
    ])

    n_threads = 10
    barrier = threading.Barrier(n_threads)
    results = [None] * n_threads
    errors = [None] * n_threads

    def worker(i):
        try:
            barrier.wait()  # 同步并发起点
            results[i] = router.complete(MESSAGES)
        except Exception as exc:  # noqa: BLE001
            errors[i] = exc

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert all(e is None for e in errors), f"threads raised: {errors}"
    assert all(r is not None and r.error is None for r in results)

    # LLMRouter 没有内置 total_cost/total_calls 字段，从结果汇总验证可计算、不报错
    total_calls = sum(1 for r in results if r.error is None)
    total_cost = sum(r.cost for r in results)
    expected_per_call = SUCCESS_TOKENS * cost_per_1k / 1000
    assert total_calls == n_threads
    assert abs(total_cost - n_threads * expected_per_call) < 1e-9

    # 并发后 status() 仍可正常调用
    status = router.status()
    assert "providers" in status
    assert "alpha" in status["providers"]
    assert status["providers"]["alpha"]["failures"] == 0
