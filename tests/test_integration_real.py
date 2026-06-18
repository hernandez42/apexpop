"""真实 LLM 与飞书连接的集成测试框架。

这些测试会发起真实网络请求，需要真实 API 凭证才能运行。
默认被 `-m "not integration"` 排除，不会在常规 `pytest tests/` 中执行。

运行方式：
    # 默认不跑（被 addopts 中的 -m "not integration" 排除）
    pytest tests/

    # 显式运行真实集成测试（需要凭证）
    pytest tests/test_integration_real.py -m integration -v

无凭证时测试会 pytest.skip 而非 fail。

需要的环境变量：
    LLM:
        DEEPSEEK_API_KEY        — DeepSeek API Key
        GROQ_API_KEY            — Groq API Key
        OPENAI_API_KEY          — OpenAI API Key
        OLLAMA_HOST             — Ollama 主机地址（默认 localhost:11434）
        OLLAMA_MODEL            — Ollama 模型名（默认 llama3.2）
        可选: <PROVIDER>_MODEL / <PROVIDER>_BASE_URL 覆盖默认值

    飞书:
        FEISHU_APP_ID           — 飞书应用 App ID
        FEISHU_APP_SECRET       — 飞书应用 App Secret
        FEISHU_TEST_CHAT_ID     — 测试用 chat_id（仅 test_real_feishu_send 需要）
"""
import asyncio
import os
import sys
import urllib.request
import urllib.error
from pathlib import Path

# 把项目根目录（/workspace/superclaw）插入 sys.path，让 superclaw 包可导入
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import pytest  # noqa: E402

from superclaw.llm_router import LLMRouter  # noqa: E402
from superclaw.channels.bus import MessageBus  # noqa: E402
from superclaw.channels.events import OutboundMessage  # noqa: E402
from superclaw.channels.feishu import FeishuChannel  # noqa: E402


# ============================================================
# 辅助函数
# ============================================================

# 真实 provider 的默认配置: (env_key, default_model, default_url, cost_per_1k)
_PROVIDER_DEFAULTS = {
    "deepseek": (
        "DEEPSEEK_API_KEY",
        "deepseek-chat",
        "https://api.deepseek.com/chat/completions",
        0.001,
    ),
    "groq": (
        "GROQ_API_KEY",
        "llama-3.1-8b-instant",
        "https://api.groq.com/openai/v1/chat/completions",
        0.0002,
    ),
    "openai": (
        "OPENAI_API_KEY",
        "gpt-4o-mini",
        "https://api.openai.com/v1/chat/completions",
        0.002,
    ),
}


def _first_real_provider():
    """返回第一个有凭证的真实 provider (name, api_key)，无则返回 None。"""
    for name, (env_key, _, _, _) in _PROVIDER_DEFAULTS.items():
        key = os.environ.get(env_key)
        if key:
            return name, key
    return None


def _add_real_provider(router: LLMRouter, name: str, api_key: str,
                       priority: int = 1) -> None:
    """把指定真实 provider 加到 router（从环境变量读取 model/base_url 覆盖）。"""
    env_key, default_model, default_url, cost = _PROVIDER_DEFAULTS[name]
    model = os.environ.get(f"{name.upper()}_MODEL", default_model)
    base_url = os.environ.get(f"{name.upper()}_BASE_URL", default_url)
    router.add_provider(
        name=name,
        api_key=api_key,
        model=model,
        base_url=base_url,
        priority=priority,
        cost_per_1k=cost,
    )


def _run(coro):
    """同步运行协程（与 test_feishu_mock.py 保持一致的便携写法）。"""
    return asyncio.run(coro)


# ============================================================
# 真实 LLM 测试
# ============================================================

@pytest.mark.integration
def test_real_deepseek():
    """真实 DeepSeek API 调用：验证返回内容、provider、成本统计。"""
    if not os.environ.get("DEEPSEEK_API_KEY"):
        pytest.skip("未设置 DEEPSEEK_API_KEY")

    router = LLMRouter()
    router.add_from_env()
    assert "deepseek" in router.providers, "add_from_env 未加载 deepseek provider"

    result = router.complete(
        [{"role": "user", "content": "说一个字"}],
        provider="deepseek",
    )

    assert result.error is None, f"DeepSeek 调用出错: {result.error}"
    assert result.provider == "deepseek", f"provider 不符: {result.provider}"
    assert result.content, "返回内容为空"
    assert result.cost > 0, f"未统计成本: cost={result.cost}, tokens={result.tokens_used}"


@pytest.mark.integration
def test_real_groq():
    """真实 Groq API 调用：验证返回内容、provider、成本统计。"""
    if not os.environ.get("GROQ_API_KEY"):
        pytest.skip("未设置 GROQ_API_KEY")

    router = LLMRouter()
    router.add_from_env()
    assert "groq" in router.providers, "add_from_env 未加载 groq provider"

    result = router.complete(
        [{"role": "user", "content": "说一个字"}],
        provider="groq",
    )

    assert result.error is None, f"Groq 调用出错: {result.error}"
    assert result.provider == "groq", f"provider 不符: {result.provider}"
    assert result.content, "返回内容为空"
    assert result.cost > 0, f"未统计成本: cost={result.cost}, tokens={result.tokens_used}"


@pytest.mark.integration
def test_real_openai():
    """真实 OpenAI API 调用：验证返回内容、provider、成本统计。"""
    if not os.environ.get("OPENAI_API_KEY"):
        pytest.skip("未设置 OPENAI_API_KEY")

    router = LLMRouter()
    router.add_from_env()
    assert "openai" in router.providers, "add_from_env 未加载 openai provider"

    result = router.complete(
        [{"role": "user", "content": "说一个字"}],
        provider="openai",
    )

    assert result.error is None, f"OpenAI 调用出错: {result.error}"
    assert result.provider == "openai", f"provider 不符: {result.provider}"
    assert result.content, "返回内容为空"
    assert result.cost > 0, f"未统计成本: cost={result.cost}, tokens={result.tokens_used}"


@pytest.mark.integration
def test_real_ollama():
    """真实本地 Ollama 调用：先探测连接，连不上则 skip。"""
    host = os.environ.get("OLLAMA_HOST", "localhost:11434")
    # 探测 Ollama 是否在运行
    try:
        req = urllib.request.Request(
            f"http://{host}/api/tags",
            headers={"User-Agent": "superclaw"},
        )
        with urllib.request.urlopen(req, timeout=3) as resp:  # nosec B310 - host 由本地配置，已设 timeout
            if resp.status != 200:
                pytest.skip(f"Ollama 返回非 200: {resp.status}")
    except Exception as e:
        pytest.skip(f"无法连接 Ollama ({host}): {e}")

    router = LLMRouter()
    model = os.environ.get("OLLAMA_MODEL", "llama3.2")
    router.add_provider(
        name="ollama",
        api_key="",
        model=model,
        base_url=f"http://{host}/api/chat",
        priority=1,
        cost_per_1k=0.0,  # 本地免费
    )

    result = router.complete(
        [{"role": "user", "content": "1+1等于几？只回答数字"}],
        provider="ollama",
    )

    assert result.error is None, f"Ollama 调用出错: {result.error}"
    assert result.provider == "ollama", f"provider 不符: {result.provider}"
    assert result.content, "返回内容为空"


@pytest.mark.integration
def test_real_llm_failover():
    """故障转移：假 key provider 失败后自动切到真实 provider。"""
    real = _first_real_provider()
    if not real:
        pytest.skip("无真实 LLM 凭证（需要 DEEPSEEK_API_KEY/GROQ_API_KEY/OPENAI_API_KEY 之一）")
    real_name, real_key = real

    router = LLMRouter()
    # 主 provider：假 key，会失败（priority=1）
    router.add_provider(
        name="fake_primary",
        api_key="sk-invalid-failover-test-key",
        model="gpt-4o-mini",
        base_url="https://api.openai.com/v1/chat/completions",
        priority=1,
        cost_per_1k=0.002,
    )
    # 备用 provider：真实 key（priority=2，故障转移目标）
    _add_real_provider(router, real_name, real_key, priority=2)

    result = router.complete(
        [{"role": "user", "content": "说一个字"}],
        provider="fake_primary",  # 强制从假 key provider 开始
    )

    assert result.error is None, f"故障转移后仍失败: {result.error}"
    assert result.provider == real_name, (
        f"故障转移失败，最终 provider: {result.provider}（期望 {real_name}）"
    )
    assert result.content, "返回内容为空"
    # 假 key provider 应记录一次失败
    assert router.providers["fake_primary"]._failures >= 1


@pytest.mark.integration
def test_real_llm_cost_tracking():
    """多轮调用后验证成本统计累加。"""
    real = _first_real_provider()
    if not real:
        pytest.skip("无真实 LLM 凭证（需要 DEEPSEEK_API_KEY/GROQ_API_KEY/OPENAI_API_KEY 之一）")
    real_name, real_key = real

    router = LLMRouter()
    _add_real_provider(router, real_name, real_key, priority=1)

    costs = []
    for i in range(3):
        result = router.complete(
            [{"role": "user", "content": f"说第{i + 1}个字"}],
            provider=real_name,
        )
        assert result.error is None, f"第 {i + 1} 次调用失败: {result.error}"
        assert result.content, f"第 {i + 1} 次返回为空"
        costs.append(result.cost)

    # 每次调用都应统计成本
    assert all(c > 0 for c in costs), f"存在未统计成本的调用: {costs}"
    # 累加验证：总和应大于任意单次成本
    total = sum(costs)
    assert total > max(costs), (
        f"成本未累加: total={total}, max={max(costs)}, costs={costs}"
    )


# ============================================================
# 真实飞书测试
# ============================================================

def _feishu_credentials():
    """返回 (app_id, app_secret)，无则返回 (None, None)。"""
    return os.environ.get("FEISHU_APP_ID"), os.environ.get("FEISHU_APP_SECRET")


def _lark_sdk_available() -> bool:
    """检查真实 lark-oapi SDK 是否可用（排除 mock 测试注入的伪模块）。"""
    try:
        import lark_oapi  # noqa: F401
    except ImportError:
        return False
    # test_feishu_mock.py 会注入带 _superclaw_fake 标记的伪模块
    if getattr(sys.modules.get("lark_oapi"), "_superclaw_fake", False):
        return False
    return True


@pytest.mark.integration
def test_real_feishu_connect():
    """真实飞书 WebSocket 连接：start() 后验证连接建立，5 秒超时。"""
    app_id, app_secret = _feishu_credentials()
    if not app_id or not app_secret:
        pytest.skip("未设置 FEISHU_APP_ID / FEISHU_APP_SECRET")
    if not _lark_sdk_available():
        pytest.skip("lark-oapi 未安装，请运行: pip install lark-oapi")

    config = {"app_id": app_id, "app_secret": app_secret, "allow_from": ["*"]}
    ch = FeishuChannel(config, MessageBus())

    async def _run():
        task = asyncio.create_task(ch.start())
        try:
            deadline = 5.0  # 总超时 5 秒
            elapsed = 0.0
            # 阶段 1：等待 _running + _ws_client 就绪（在阻塞连接前设置）
            while elapsed < deadline:
                if ch._running and ch._ws_client is not None:
                    break
                if task.done():
                    pytest.fail(
                        "渠道 start() 提前返回 — 检查凭证或 lark-oapi 安装"
                    )
                await asyncio.sleep(0.1)
                elapsed += 0.1
            assert ch._running, "渠道未启动（_running=False）"
            assert ch._ws_client is not None, "WebSocket 客户端未创建"
            # 阶段 2：等待 WebSocket 实际建立连接（用剩余时间）
            while elapsed < deadline:
                if task.done():
                    pytest.fail(
                        "WebSocket 连接失败 — start() 提前返回"
                        "（凭证错误或网络问题）"
                    )
                await asyncio.sleep(0.1)
                elapsed += 0.1
            # 5 秒后 start() 仍在阻塞 → 连接已建立
            assert not task.done(), "WebSocket 连接未建立（5 秒超时）"
        finally:
            await ch.stop()
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    _run(_run())


@pytest.mark.integration
def test_real_feishu_send():
    """真实飞书发送消息：发送 '集成测试' 到测试 chat，验证无异常。"""
    app_id, app_secret = _feishu_credentials()
    chat_id = os.environ.get("FEISHU_TEST_CHAT_ID")
    if not app_id or not app_secret:
        pytest.skip("未设置 FEISHU_APP_ID / FEISHU_APP_SECRET")
    if not chat_id:
        pytest.skip("未设置 FEISHU_TEST_CHAT_ID")
    if not _lark_sdk_available():
        pytest.skip("lark-oapi 未安装，请运行: pip install lark-oapi")

    config = {"app_id": app_id, "app_secret": app_secret, "allow_from": ["*"]}
    ch = FeishuChannel(config, MessageBus())
    ch._load_lark()  # 初始化 SDK，send() 依赖 self._lark

    msg = OutboundMessage(
        channel="feishu",
        chat_id=chat_id,
        content="集成测试",
    )

    # send() 内部捕获异常不外抛，验证无异常 + tenant token 获取成功
    _run(ch.send(msg))

    # tenant_access_token 非空说明凭证有效（REST API 认证通过）
    assert ch._tenant_access_token, (
        "获取 tenant_access_token 失败 — 凭证可能无效"
    )
