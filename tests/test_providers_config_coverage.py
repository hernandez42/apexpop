"""覆盖率补充测试 — superclaw.providers / superclaw.config / superclaw.llm_router

目标：把三个模块的行覆盖率提升到 85%+。
- providers.py: MockProvider / OpenAICompatibleProvider / OllamaProvider / 工厂函数
- config.py: _env_override / load_config / dataclass 默认值
- llm_router.py: _call_urllib / _check_ollama / litellm 路径 / get_router / 路由边界

所有网络调用均通过 unittest.mock.patch 拦截，测试离线、隔离。
"""
import json
import sys
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from superclaw.config import (
    DEFAULT_CONFIG,
    LLMConfig,
    SessionConfig,
    ToolsConfig,
    SuperclawConfig,
    _env_override,
    load_config,
)
from superclaw.providers import (
    BaseProvider,
    MockProvider,
    OpenAICompatibleProvider,
    OllamaProvider,
    PROVIDERS,
    SYSTEM_PROMPT,
    get_provider,
    list_providers,
)
from superclaw import llm_router as lr_mod
from superclaw.llm_router import (
    LLMRouter,
    ProviderConfig,
    CompletionResult,
    LITELLM_AVAILABLE,
    get_router,
)


# ============================================================
# 辅助工具
# ============================================================

class _FakeResponse:
    """模拟 urllib HTTP 响应（支持 context manager + read + status）。"""

    def __init__(self, data: bytes, status: int = 200):
        self._data = data
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self) -> bytes:
        return self._data


def _openai_body(content="hello", tokens=42):
    return json.dumps({
        "choices": [{"message": {"content": content}}],
        "usage": {"total_tokens": tokens},
    }).encode("utf-8")


def _ollama_body(content="ollama-says"):
    return json.dumps({"message": {"content": content}}).encode("utf-8")


def _offline_router():
    """创建离线路由器（不走 litellm，不连网）。"""
    r = LLMRouter()
    r._litellm = None
    return r


@pytest.fixture()
def isolated_home(tmp_workspace, monkeypatch):
    """隔离 HOME，避免 ~/.superclaw/config.json 干扰 load_config 测试。"""
    monkeypatch.setenv("HOME", str(tmp_workspace))
    return tmp_workspace


# ============================================================
# providers.py — MockProvider
# ============================================================

def test_mock_provider_returns_nonempty_string():
    cfg = LLMConfig(provider="mock")
    p = MockProvider(cfg)
    out = p.call([{"role": "user", "content": "请帮我处理这件事"}])
    assert isinstance(out, str)
    assert len(out) > 0


def test_mock_provider_action_keywords_branches():
    """覆盖 MockProvider.call 中各 action 分支（执行/读取/搜索/分析）。"""
    cfg = LLMConfig(provider="mock")
    p = MockProvider(cfg)
    for prompt in ["请执行命令", "请运行脚本", "请读取文件", "请搜索信息", "请分析思考"]:
        out = p.call([{"role": "user", "content": prompt}])
        assert isinstance(out, str)
        assert len(out) > 0


def test_mock_provider_default_action_branch():
    """不命中任何关键词时走默认 action='了解情况'。"""
    cfg = LLMConfig(provider="mock")
    p = MockProvider(cfg)
    out = p.call([{"role": "user", "content": "随便说点什么"}])
    assert isinstance(out, str)


def test_mock_provider_no_user_message():
    cfg = LLMConfig(provider="mock")
    p = MockProvider(cfg)
    out = p.call([{"role": "system", "content": "system-prompt"}])
    assert isinstance(out, str)


def test_mock_provider_long_prompt_truncated():
    cfg = LLMConfig(provider="mock")
    p = MockProvider(cfg)
    long_text = "分析" * 200
    out = p.call([{"role": "user", "content": long_text}])
    assert isinstance(out, str)


def test_base_provider_call_raises_not_implemented():
    b = BaseProvider(LLMConfig())
    with pytest.raises(NotImplementedError):
        b.call([])


def test_base_provider_stores_cfg():
    cfg = LLMConfig(provider="mock", model="m")
    b = BaseProvider(cfg)
    assert b.cfg is cfg


# ============================================================
# providers.py — OpenAICompatibleProvider
# ============================================================

def test_openai_compatible_missing_api_key_message():
    cfg = LLMConfig(provider="deepseek", api_key="")
    p = OpenAICompatibleProvider(cfg)
    out = p.call([{"role": "user", "content": "hi"}])
    assert "DEEPSEEK_API_KEY" in out
    assert "未配置" in out


def test_openai_compatible_no_url_and_no_default():
    """provider 既无 base_url 又不在默认列表 → 返回 base_url 未配置。"""
    cfg = LLMConfig(provider="bogus", api_key="k", base_url="")
    p = OpenAICompatibleProvider(cfg)
    out = p.call([{"role": "user", "content": "hi"}])
    assert "base_url 未配置" in out


def test_default_url_for_each_known_provider():
    cases = [
        ("deepseek", "https://api.deepseek.com/chat/completions"),
        ("groq", "https://api.groq.com/openai/v1/chat/completions"),
        ("openrouter", "https://openrouter.ai/api/v1/chat/completions"),
        ("openai", "https://api.openai.com/v1/chat/completions"),
    ]
    for name, expected in cases:
        p = OpenAICompatibleProvider(LLMConfig(provider=name, api_key="k"))
        assert p._default_url() == expected


def test_default_url_unknown_provider_empty():
    p = OpenAICompatibleProvider(LLMConfig(provider="bogus", api_key="k"))
    assert p._default_url() == ""


def test_deepseek_call_success_verifies_request_and_response():
    cfg = LLMConfig(provider="deepseek", api_key="key-abc", model="deepseek-chat",
                    temperature=0.5, max_tokens=100, timeout=30)
    p = OpenAICompatibleProvider(cfg)
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["headers"] = {k.lower(): v for k, v in req.headers.items()}
        captured["data"] = json.loads(req.data.decode("utf-8"))
        captured["method"] = req.method
        captured["timeout"] = timeout
        return _FakeResponse(_openai_body("deepseek-reply", 99))

    with patch("superclaw.providers._ureq.urlopen", side_effect=fake_urlopen):
        out = p.call([{"role": "user", "content": "hello"}])

    assert out == "deepseek-reply"
    assert captured["url"] == "https://api.deepseek.com/chat/completions"
    assert captured["method"] == "POST"
    assert captured["timeout"] == 30
    assert captured["headers"]["authorization"] == "Bearer key-abc"
    assert captured["headers"]["content-type"] == "application/json"
    assert captured["data"]["model"] == "deepseek-chat"
    assert captured["data"]["temperature"] == 0.5
    assert captured["data"]["max_tokens"] == 100
    assert captured["data"]["messages"] == [{"role": "user", "content": "hello"}]


def test_openai_compatible_custom_base_url_used():
    cfg = LLMConfig(provider="deepseek", api_key="k",
                    base_url="https://custom.example.com/v1/chat")
    p = OpenAICompatibleProvider(cfg)
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        return _FakeResponse(_openai_body("ok"))

    with patch("superclaw.providers._ureq.urlopen", side_effect=fake_urlopen):
        assert p.call([{"role": "user", "content": "hi"}]) == "ok"
    assert captured["url"] == "https://custom.example.com/v1/chat"


def test_groq_call_success_uses_groq_url():
    cfg = LLMConfig(provider="groq", api_key="gk", model="llama-3.1-8b-instant")
    p = OpenAICompatibleProvider(cfg)
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        return _FakeResponse(_openai_body("groq-reply"))

    with patch("superclaw.providers._ureq.urlopen", side_effect=fake_urlopen):
        assert p.call([{"role": "user", "content": "hi"}]) == "groq-reply"
    assert captured["url"] == "https://api.groq.com/openai/v1/chat/completions"


def test_openai_call_success_uses_openai_url():
    cfg = LLMConfig(provider="openai", api_key="ok", model="gpt-4o-mini")
    p = OpenAICompatibleProvider(cfg)
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        return _FakeResponse(_openai_body("openai-reply"))

    with patch("superclaw.providers._ureq.urlopen", side_effect=fake_urlopen):
        assert p.call([{"role": "user", "content": "hi"}]) == "openai-reply"
    assert captured["url"] == "https://api.openai.com/v1/chat/completions"


def test_openrouter_call_adds_extra_headers():
    cfg = LLMConfig(provider="openrouter", api_key="or-key",
                    model="anthropic/claude-3-haiku")
    p = OpenAICompatibleProvider(cfg)
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["headers"] = {k.lower(): v for k, v in req.headers.items()}
        return _FakeResponse(_openai_body("or-reply"))

    with patch("superclaw.providers._ureq.urlopen", side_effect=fake_urlopen):
        assert p.call([{"role": "user", "content": "hi"}]) == "or-reply"
    assert captured["headers"]["http-referer"] == "https://superclaw.local"
    assert captured["headers"]["x-title"] == "superclaw"
    assert captured["headers"]["authorization"] == "Bearer or-key"


def test_openai_compatible_url_error_returned():
    from urllib import error as uerr
    cfg = LLMConfig(provider="deepseek", api_key="k")
    p = OpenAICompatibleProvider(cfg)

    def fake_urlopen(req, timeout=None):
        raise uerr.URLError("connection refused")

    with patch("superclaw.providers._ureq.urlopen", side_effect=fake_urlopen):
        out = p.call([{"role": "user", "content": "hi"}])
    assert "网络错误" in out
    assert "connection refused" in out


def test_openai_compatible_generic_exception_returned():
    cfg = LLMConfig(provider="deepseek", api_key="k")
    p = OpenAICompatibleProvider(cfg)

    def fake_urlopen(req, timeout=None):
        raise ValueError("boom")

    with patch("superclaw.providers._ureq.urlopen", side_effect=fake_urlopen):
        out = p.call([{"role": "user", "content": "hi"}])
    assert "LLM 错误" in out
    assert "boom" in out


def test_openai_compatible_timeout_passed_to_urlopen():
    cfg = LLMConfig(provider="deepseek", api_key="k", timeout=7)
    p = OpenAICompatibleProvider(cfg)
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["timeout"] = timeout
        return _FakeResponse(_openai_body("ok"))

    with patch("superclaw.providers._ureq.urlopen", side_effect=fake_urlopen):
        p.call([{"role": "user", "content": "hi"}])
    assert captured["timeout"] == 7


# ============================================================
# providers.py — OllamaProvider
# ============================================================

def test_ollama_call_success_default_url_no_auth():
    cfg = LLMConfig(provider="ollama", model="llama3.2", timeout=10)
    p = OllamaProvider(cfg)
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["headers"] = {k.lower(): v for k, v in req.headers.items()}
        captured["data"] = json.loads(req.data.decode("utf-8"))
        captured["timeout"] = timeout
        return _FakeResponse(_ollama_body("ollama-ok"))

    with patch("superclaw.providers._ureq.urlopen", side_effect=fake_urlopen):
        out = p.call([{"role": "user", "content": "hi"}])

    assert out == "ollama-ok"
    assert captured["url"] == "http://localhost:11434/api/chat"
    assert "authorization" not in captured["headers"]
    assert captured["data"]["model"] == "llama3.2"
    assert captured["data"]["stream"] is False
    assert captured["timeout"] == 10


def test_ollama_call_custom_base_url():
    cfg = LLMConfig(provider="ollama", base_url="http://my-host:1234/api/chat")
    p = OllamaProvider(cfg)
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        return _FakeResponse(_ollama_body("ok"))

    with patch("superclaw.providers._ureq.urlopen", side_effect=fake_urlopen):
        p.call([{"role": "user", "content": "hi"}])
    assert captured["url"] == "http://my-host:1234/api/chat"


def test_ollama_default_model_when_empty():
    cfg = LLMConfig(provider="ollama", model="")
    p = OllamaProvider(cfg)
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["data"] = json.loads(req.data.decode("utf-8"))
        return _FakeResponse(_ollama_body("ok"))

    with patch("superclaw.providers._ureq.urlopen", side_effect=fake_urlopen):
        p.call([{"role": "user", "content": "hi"}])
    assert captured["data"]["model"] == "llama3.2"


def test_ollama_connection_refused_message():
    cfg = LLMConfig(provider="ollama")
    p = OllamaProvider(cfg)

    def fake_urlopen(req, timeout=None):
        raise ConnectionRefusedError("nope")

    with patch("superclaw.providers._ureq.urlopen", side_effect=fake_urlopen):
        out = p.call([{"role": "user", "content": "hi"}])
    assert "Ollama 未运行" in out
    assert "localhost:11434" in out


def test_ollama_generic_exception_message():
    cfg = LLMConfig(provider="ollama")
    p = OllamaProvider(cfg)

    def fake_urlopen(req, timeout=None):
        raise RuntimeError("boom")

    with patch("superclaw.providers._ureq.urlopen", side_effect=fake_urlopen):
        out = p.call([{"role": "user", "content": "hi"}])
    assert "Ollama 错误" in out
    assert "boom" in out


def test_ollama_empty_response_returns_empty_string():
    cfg = LLMConfig(provider="ollama")
    p = OllamaProvider(cfg)

    def fake_urlopen(req, timeout=None):
        return _FakeResponse(json.dumps({}).encode("utf-8"))

    with patch("superclaw.providers._ureq.urlopen", side_effect=fake_urlopen):
        out = p.call([{"role": "user", "content": "hi"}])
    assert out == ""


# ============================================================
# providers.py — 工厂函数 / 模块级
# ============================================================

def test_list_providers_returns_sorted_keys():
    providers = list_providers()
    assert providers == sorted(providers)
    for name in ["mock", "deepseek", "groq", "openrouter", "openai", "ollama"]:
        assert name in providers


def test_providers_dict_contains_all():
    assert set(PROVIDERS.keys()) == {
        "mock", "deepseek", "groq", "openrouter", "openai", "ollama"
    }


def test_get_provider_returns_correct_class():
    assert isinstance(get_provider(LLMConfig(provider="mock")), MockProvider)
    assert isinstance(
        get_provider(LLMConfig(provider="deepseek", api_key="k")),
        OpenAICompatibleProvider,
    )
    assert isinstance(
        get_provider(LLMConfig(provider="groq", api_key="k")),
        OpenAICompatibleProvider,
    )
    assert isinstance(
        get_provider(LLMConfig(provider="openai", api_key="k")),
        OpenAICompatibleProvider,
    )
    assert isinstance(
        get_provider(LLMConfig(provider="openrouter", api_key="k")),
        OpenAICompatibleProvider,
    )
    assert isinstance(get_provider(LLMConfig(provider="ollama")), OllamaProvider)


def test_get_provider_unknown_falls_back_to_mock():
    p = get_provider(LLMConfig(provider="does-not-exist"))
    assert isinstance(p, MockProvider)


def test_get_provider_case_insensitive():
    p = get_provider(LLMConfig(provider="MOCK"))
    assert isinstance(p, MockProvider)


def test_system_prompt_is_nonempty_string():
    assert isinstance(SYSTEM_PROMPT, str)
    assert "superclaw" in SYSTEM_PROMPT


# ============================================================
# config.py — dataclass 默认值
# ============================================================

def test_default_config_dict_constants():
    assert DEFAULT_CONFIG["llm"]["provider"] == "mock"
    assert DEFAULT_CONFIG["llm"]["max_tokens"] == 2048
    assert DEFAULT_CONFIG["tools"]["shell"] is True
    assert DEFAULT_CONFIG["tools"]["web"] is False
    assert DEFAULT_CONFIG["session"]["max_messages"] == 50


def test_llm_config_defaults():
    c = LLMConfig()
    assert c.provider == "mock"
    assert c.model == "mock-model"
    assert c.api_key == ""
    assert c.base_url == ""
    assert c.temperature == 0.7
    assert c.max_tokens == 2048
    assert c.timeout == 60


def test_session_config_defaults():
    s = SessionConfig()
    assert s.max_messages == 50
    assert s.path == "~/.superclaw/sessions"


def test_tools_config_defaults():
    t = ToolsConfig()
    assert t.shell is True
    assert t.file is True
    assert t.web is False
    assert t.think is True
    assert t.max_tool_iterations == 5


def test_superclaw_config_aggregates_subconfigs():
    c = SuperclawConfig()
    assert isinstance(c.llm, LLMConfig)
    assert isinstance(c.session, SessionConfig)
    assert isinstance(c.tools, ToolsConfig)
    assert isinstance(c.workspace, str)


# ============================================================
# config.py — _env_override
# ============================================================

def test_env_override_provider_and_model(monkeypatch):
    monkeypatch.setenv("SUPERCLAW_PROVIDER", "groq")
    monkeypatch.setenv("SUPERCLAW_MODEL", "llama-x")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.delenv("GROQ_BASE_URL", raising=False)
    cfg = _env_override(json.loads(json.dumps(DEFAULT_CONFIG)))
    assert cfg["llm"]["provider"] == "groq"
    assert cfg["llm"]["model"] == "llama-x"


def test_env_override_provider_specific_api_key(monkeypatch):
    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "groq-secret")
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    cfg["llm"]["provider"] = "groq"
    cfg = _env_override(cfg)
    assert cfg["llm"]["api_key"] == "groq-secret"


def test_env_override_generic_api_key_fallback(monkeypatch):
    """provider 对应的 *_API_KEY 不存在时回退到 API_KEY。"""
    monkeypatch.delenv("MOCK_API_KEY", raising=False)
    monkeypatch.setenv("API_KEY", "generic-key")
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    cfg["llm"]["provider"] = "mock"
    cfg = _env_override(cfg)
    assert cfg["llm"]["api_key"] == "generic-key"


def test_env_override_base_url(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://custom.example.com")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("API_KEY", raising=False)
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    cfg["llm"]["provider"] = "deepseek"
    cfg = _env_override(cfg)
    assert cfg["llm"]["base_url"] == "https://custom.example.com"


def test_env_override_no_env_keeps_config(monkeypatch):
    for k in ["SUPERCLAW_PROVIDER", "SUPERCLAW_MODEL", "API_KEY",
              "MOCK_API_KEY", "MOCK_BASE_URL"]:
        monkeypatch.delenv(k, raising=False)
    original = json.loads(json.dumps(DEFAULT_CONFIG))
    cfg = _env_override(json.loads(json.dumps(DEFAULT_CONFIG)))
    assert cfg == original


def test_env_override_empty_api_key_does_not_overwrite(monkeypatch):
    """provider 对应 *_API_KEY 与 API_KEY 都缺失时，不覆盖现有 api_key。"""
    monkeypatch.delenv("MOCK_API_KEY", raising=False)
    monkeypatch.delenv("API_KEY", raising=False)
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    cfg["llm"]["provider"] = "mock"
    cfg["llm"]["api_key"] = "preexisting"
    cfg = _env_override(cfg)
    assert cfg["llm"]["api_key"] == "preexisting"


# ============================================================
# config.py — load_config
# ============================================================

def test_load_config_no_file_returns_defaults(isolated_home, monkeypatch):
    for k in ["SUPERCLAW_PROVIDER", "SUPERCLAW_MODEL", "API_KEY",
              "MOCK_API_KEY", "MOCK_BASE_URL"]:
        monkeypatch.delenv(k, raising=False)
    cfg = load_config()
    assert cfg.llm.provider == "mock"
    assert cfg.llm.model == "mock-model"
    assert cfg.llm.api_key == ""
    assert cfg.tools.shell is True
    assert cfg.tools.web is False
    assert cfg.session.max_messages == 50


def test_load_config_explicit_path_not_found(isolated_home, monkeypatch):
    for k in ["SUPERCLAW_PROVIDER", "API_KEY", "MOCK_API_KEY"]:
        monkeypatch.delenv(k, raising=False)
    cfg = load_config("/nonexistent/path/config.json")
    assert cfg.llm.provider == "mock"


def test_load_config_from_explicit_path(isolated_home):
    config_file = isolated_home / "my.json"
    config_file.write_text(json.dumps({
        "llm": {"provider": "deepseek", "model": "deepseek-chat", "api_key": "secret"},
        "tools": {"shell": False},
    }))
    cfg = load_config(str(config_file))
    assert cfg.llm.provider == "deepseek"
    assert cfg.llm.model == "deepseek-chat"
    assert cfg.llm.api_key == "secret"
    # 覆盖的 tools.shell 生效
    assert cfg.tools.shell is False
    # 未覆盖的字段保留默认
    assert cfg.tools.file is True
    assert cfg.llm.timeout == 60


def test_load_config_from_cwd_config_json(isolated_home, monkeypatch):
    (isolated_home / "config.json").write_text(json.dumps({
        "llm": {"provider": "groq"},
    }))
    for k in ["SUPERCLAW_PROVIDER", "GROQ_API_KEY", "API_KEY"]:
        monkeypatch.delenv(k, raising=False)
    cfg = load_config()
    assert cfg.llm.provider == "groq"


def test_load_config_from_config_subdir(isolated_home, monkeypatch):
    (isolated_home / "config").mkdir()
    (isolated_home / "config" / "config.json").write_text(json.dumps({
        "llm": {"provider": "openai"},
    }))
    for k in ["SUPERCLAW_PROVIDER", "OPENAI_API_KEY", "API_KEY"]:
        monkeypatch.delenv(k, raising=False)
    cfg = load_config()
    assert cfg.llm.provider == "openai"


def test_load_config_invalid_json_skipped(isolated_home, monkeypatch):
    (isolated_home / "config.json").write_text("{ not valid json")
    for k in ["SUPERCLAW_PROVIDER", "API_KEY", "MOCK_API_KEY"]:
        monkeypatch.delenv(k, raising=False)
    cfg = load_config()
    # 无效 JSON 被跳过 → 返回默认
    assert cfg.llm.provider == "mock"


def test_load_config_env_overrides_file(isolated_home, monkeypatch):
    config_file = isolated_home / "c.json"
    config_file.write_text(json.dumps({"llm": {"provider": "deepseek"}}))
    monkeypatch.setenv("SUPERCLAW_PROVIDER", "openai")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("API_KEY", raising=False)
    cfg = load_config(str(config_file))
    assert cfg.llm.provider == "openai"


def test_load_config_env_api_key(isolated_home, monkeypatch):
    config_file = isolated_home / "c.json"
    config_file.write_text(json.dumps({"llm": {"provider": "deepseek"}}))
    monkeypatch.setenv("DEEPSEEK_API_KEY", "env-key")
    monkeypatch.delenv("API_KEY", raising=False)
    cfg = load_config(str(config_file))
    assert cfg.llm.provider == "deepseek"
    assert cfg.llm.api_key == "env-key"


def test_load_config_partial_section_merge(isolated_home, monkeypatch):
    """只提供 llm 的部分字段，其余保留默认。"""
    config_file = isolated_home / "c.json"
    config_file.write_text(json.dumps({"llm": {"max_tokens": 999}}))
    for k in ["SUPERCLAW_PROVIDER", "API_KEY", "MOCK_API_KEY"]:
        monkeypatch.delenv(k, raising=False)
    cfg = load_config(str(config_file))
    assert cfg.llm.max_tokens == 999
    assert cfg.llm.provider == "mock"  # 未提供 → 默认


# ============================================================
# llm_router.py — _call_urllib（OpenAI 兼容 + Ollama + 错误分支）
# ============================================================

def test_call_urllib_openai_success_and_cost():
    router = _offline_router()
    router.add_provider("deepseek", api_key="dk", model="deepseek-chat",
                        base_url="https://api.deepseek.com/chat/completions",
                        cost_per_1k=0.002, priority=1,
                        temperature=0.3, max_tokens=50, timeout=15)
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["headers"] = {k.lower(): v for k, v in req.headers.items()}
        captured["data"] = json.loads(req.data.decode("utf-8"))
        captured["timeout"] = timeout
        return _FakeResponse(_openai_body("urllib-ok", 500))

    with patch("superclaw.llm_router._ureq.urlopen", side_effect=fake_urlopen):
        result = router.complete([{"role": "user", "content": "hi"}])

    assert result.error is None
    assert result.content == "urllib-ok"
    assert result.provider == "deepseek"
    assert result.model == "deepseek-chat"
    assert result.tokens_used == 500
    # cost = 500 * 0.002 / 1000 = 0.001
    assert abs(result.cost - 0.001) < 1e-9
    assert result.latency_ms >= 0
    assert captured["url"] == "https://api.deepseek.com/chat/completions"
    assert captured["headers"]["authorization"] == "Bearer dk"
    assert captured["data"]["model"] == "deepseek-chat"
    assert captured["data"]["temperature"] == 0.3
    assert captured["data"]["max_tokens"] == 50
    assert captured["timeout"] == 15


def test_call_urllib_max_tokens_override():
    router = _offline_router()
    router.add_provider("deepseek", api_key="dk", model="m",
                        base_url="https://x.example.com", priority=1, max_tokens=50)
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["data"] = json.loads(req.data.decode("utf-8"))
        return _FakeResponse(_openai_body("ok"))

    with patch("superclaw.llm_router._ureq.urlopen", side_effect=fake_urlopen):
        router.complete([{"role": "user", "content": "hi"}], max_tokens=200)
    assert captured["data"]["max_tokens"] == 200


def test_call_urllib_ollama_format():
    """覆盖 _call_urllib 中 ollama 请求格式分支（payload/headers）。

    注意：_call_urllib 统一用 OpenAI choices 格式解析响应，而真实 ollama
    响应 {"message": {...}} 无 choices 键 → content 解析为 ""。此处验证
    请求构造（ollama 格式 + 无 auth）与无错误返回，反映源码实际行为。
    """
    router = _offline_router()
    router.add_provider("ollama", model="llama3.2",
                        base_url="http://localhost:11434/api/chat",
                        priority=1, temperature=0.8)
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["headers"] = {k.lower(): v for k, v in req.headers.items()}
        captured["data"] = json.loads(req.data.decode("utf-8"))
        return _FakeResponse(_ollama_body("ollama-urllib"))

    with patch("superclaw.llm_router._ureq.urlopen", side_effect=fake_urlopen):
        result = router.complete([{"role": "user", "content": "hi"}])

    assert result.error is None
    assert result.provider == "ollama"
    # 请求按 ollama 格式构造
    assert captured["data"]["model"] == "llama3.2"
    assert captured["data"]["stream"] is False
    assert captured["data"]["options"]["temperature"] == 0.8
    assert "authorization" not in captured["headers"]
    # 响应按 OpenAI 格式解析 → ollama 响应无 choices → content 为空
    assert result.content == ""


def test_call_urllib_openrouter_extra_headers():
    router = _offline_router()
    router.add_provider("openrouter", api_key="or", model="claude",
                        base_url="https://openrouter.ai/api/v1/chat/completions",
                        priority=1)
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["headers"] = {k.lower(): v for k, v in req.headers.items()}
        return _FakeResponse(_openai_body("ok"))

    with patch("superclaw.llm_router._ureq.urlopen", side_effect=fake_urlopen):
        router.complete([{"role": "user", "content": "hi"}])
    assert captured["headers"]["http-referer"] == "https://superclaw.local"
    assert captured["headers"]["x-title"] == "superclaw"


def test_call_urllib_url_error_branch_direct():
    from urllib import error as uerr
    router = _offline_router()
    pconfig = ProviderConfig(name="deepseek", api_key="k", model="m",
                             base_url="https://x.example.com", timeout=5)

    def fake_urlopen(req, timeout=None):
        raise uerr.URLError("timeout")

    with patch("superclaw.llm_router._ureq.urlopen", side_effect=fake_urlopen):
        result = router._call_urllib(
            pconfig, [{"role": "user", "content": "hi"}], None, time.time()
        )
    assert result.error is not None
    assert "网络错误" in result.error
    assert result.provider == "deepseek"
    assert result.latency_ms >= 0


def test_call_urllib_generic_exception_branch_direct():
    router = _offline_router()
    pconfig = ProviderConfig(name="deepseek", api_key="k", model="m",
                             base_url="https://x.example.com", timeout=5)

    def fake_urlopen(req, timeout=None):
        raise KeyError("boom")

    with patch("superclaw.llm_router._ureq.urlopen", side_effect=fake_urlopen):
        result = router._call_urllib(
            pconfig, [{"role": "user", "content": "hi"}], None, time.time()
        )
    assert result.error == "'boom'"
    assert result.provider == "deepseek"


def test_call_urllib_no_choices_key_returns_empty_content():
    """响应无 choices 键时，data.get("choices", [{}]) 回退到 [{}] → content 为空。"""
    router = _offline_router()
    router.add_provider("deepseek", api_key="k", model="m",
                        base_url="https://x.example.com", priority=1)

    def fake_urlopen(req, timeout=None):
        return _FakeResponse(json.dumps({"usage": {"total_tokens": 0}}).encode())

    with patch("superclaw.llm_router._ureq.urlopen", side_effect=fake_urlopen):
        result = router.complete([{"role": "user", "content": "hi"}])
    assert result.error is None
    assert result.content == ""
    assert result.tokens_used == 0


def test_call_urllib_empty_choices_list_raises_index_error():
    """响应 choices 为空列表时 [][0] 抛 IndexError → 走 generic Exception 分支。"""
    router = _offline_router()
    router.add_provider("deepseek", api_key="k", model="m",
                        base_url="https://x.example.com", priority=1)

    def fake_urlopen(req, timeout=None):
        return _FakeResponse(json.dumps({"choices": [], "usage": {}}).encode())

    with patch("superclaw.llm_router._ureq.urlopen", side_effect=fake_urlopen):
        result = router._call_urllib(
            router.providers["deepseek"],
            [{"role": "user", "content": "hi"}], None, time.time()
        )
    assert result.error is not None  # IndexError 被捕获
    assert result.provider == "deepseek"


# ============================================================
# llm_router.py — _call_provider（api_key 检查 / mock 分支）
# ============================================================

def test_call_provider_missing_api_key_error():
    router = _offline_router()
    pconfig = ProviderConfig(name="deepseek", api_key="", model="m")
    result = router._call_provider(
        pconfig, [{"role": "user", "content": "hi"}], None
    )
    assert result.error == "DEEPSEEK_API_KEY 未配置"
    assert result.content == ""
    assert result.provider == "deepseek"


def test_call_provider_mock_branch():
    router = _offline_router()
    pconfig = ProviderConfig(name="mock", model="mock")
    result = router._call_provider(
        pconfig, [{"role": "user", "content": "hi"}], None
    )
    assert result.error is None
    assert result.provider == "mock"
    assert result.model == "mock"
    assert len(result.content) > 0


# ============================================================
# llm_router.py — _check_ollama
# ============================================================

def test_check_ollama_running_returns_true():
    router = _offline_router()

    def fake_urlopen(req, timeout=None):
        return _FakeResponse(b"{}", status=200)

    with patch("superclaw.llm_router._ureq.urlopen", side_effect=fake_urlopen):
        assert router._check_ollama() is True


def test_check_ollama_not_running_returns_false():
    router = _offline_router()

    def fake_urlopen(req, timeout=None):
        raise ConnectionRefusedError("nope")

    with patch("superclaw.llm_router._ureq.urlopen", side_effect=fake_urlopen):
        assert router._check_ollama() is False


def test_check_ollama_non_200_returns_false():
    router = _offline_router()

    def fake_urlopen(req, timeout=None):
        return _FakeResponse(b"{}", status=500)

    with patch("superclaw.llm_router._ureq.urlopen", side_effect=fake_urlopen):
        assert router._check_ollama() is False


# ============================================================
# llm_router.py — add_from_env（含 Ollama / 全 key / 自定义）
# ============================================================

def test_add_from_env_ollama_via_base_url(monkeypatch):
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://my-ollama:11500/api/chat")
    for k in ["DEEPSEEK_API_KEY", "GROQ_API_KEY", "OPENAI_API_KEY",
              "OPENROUTER_API_KEY"]:
        monkeypatch.delenv(k, raising=False)
    router = _offline_router()
    router.add_from_env()
    assert "ollama" in router.providers
    assert router.providers["ollama"].base_url == "http://my-ollama:11500/api/chat"
    assert router.providers["ollama"].api_key == ""


def test_add_from_env_ollama_via_check(monkeypatch):
    """无 OLLAMA_BASE_URL 但 _check_ollama=True 时也添加 ollama。"""
    for k in ["DEEPSEEK_API_KEY", "GROQ_API_KEY", "OPENAI_API_KEY",
              "OPENROUTER_API_KEY", "OLLAMA_BASE_URL"]:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(LLMRouter, "_check_ollama", lambda self: True)
    router = _offline_router()
    router.add_from_env()
    assert "ollama" in router.providers
    assert router.providers["ollama"].base_url == "http://localhost:11434/api/chat"


def test_add_from_env_ollama_custom_model(monkeypatch):
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://x/api/chat")
    monkeypatch.setenv("OLLAMA_MODEL", "mistral")
    for k in ["DEEPSEEK_API_KEY", "GROQ_API_KEY", "OPENAI_API_KEY",
              "OPENROUTER_API_KEY"]:
        monkeypatch.delenv(k, raising=False)
    router = _offline_router()
    router.add_from_env()
    assert router.providers["ollama"].model == "mistral"


def test_add_from_env_all_keys(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "dk")
    monkeypatch.setenv("GROQ_API_KEY", "gk")
    monkeypatch.setenv("OPENAI_API_KEY", "ok")
    monkeypatch.setenv("OPENROUTER_API_KEY", "ork")
    monkeypatch.setattr(LLMRouter, "_check_ollama", lambda self: False)
    router = _offline_router()
    router.add_from_env()
    for name in ["deepseek", "groq", "openai", "openrouter", "mock"]:
        assert name in router.providers
    assert "ollama" not in router.providers
    # 验证各 provider 的 cost_per_1k 与默认 model/url
    assert router.providers["deepseek"].cost_per_1k == 0.001
    assert router.providers["deepseek"].model == "deepseek-chat"
    assert router.providers["groq"].cost_per_1k == 0.0002
    assert router.providers["groq"].model == "llama-3.1-8b-instant"
    assert router.providers["openrouter"].cost_per_1k == 0.005
    assert router.providers["openai"].cost_per_1k == 0.002
    assert router.providers["openai"].model == "gpt-4o-mini"


def test_add_from_env_custom_base_url_and_model(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "dk")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://custom.deepseek.com")
    monkeypatch.setenv("DEEPSEEK_MODEL", "custom-model")
    monkeypatch.setattr(LLMRouter, "_check_ollama", lambda self: False)
    router = _offline_router()
    router.add_from_env()
    assert router.providers["deepseek"].base_url == "https://custom.deepseek.com"
    assert router.providers["deepseek"].model == "custom-model"


def test_add_from_env_only_mock_when_no_keys(monkeypatch):
    for k in ["DEEPSEEK_API_KEY", "GROQ_API_KEY", "OPENAI_API_KEY",
              "OPENROUTER_API_KEY", "OLLAMA_BASE_URL"]:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(LLMRouter, "_check_ollama", lambda self: False)
    router = _offline_router()
    router.add_from_env()
    assert list(router.providers.keys()) == ["mock"]
    assert router.providers["mock"].priority == 99


# ============================================================
# llm_router.py — _route 边界
# ============================================================

def test_route_includes_providers_not_in_preference():
    """偏好列表外的 provider 按 priority 追加到末尾（覆盖 append 分支）。"""
    router = _offline_router()
    router.add_provider("groq", api_key="k", priority=1)
    router.add_provider("custom-llm", api_key="k", priority=2)
    router.add_provider("mock", priority=3)
    order = router._route("low")
    assert "groq" in order
    assert "custom-llm" in order
    assert "mock" in order
    # 偏好列表内的 groq 在 custom-llm 之前
    assert order.index("groq") < order.index("custom-llm")


def test_route_low_medium_high_select_different_first():
    router = _offline_router()
    router.add_provider("groq", api_key="k", priority=5)
    router.add_provider("openai", api_key="k", priority=5)
    router.add_provider("deepseek", api_key="k", priority=5)
    assert router._route("low")[0] == "groq"
    assert router._route("medium")[0] == "deepseek"
    assert router._route("high")[0] == "openai"


# ============================================================
# llm_router.py — complete() 行为
# ============================================================

def test_complete_skips_disabled_provider():
    router = _offline_router()
    router.add_provider("deepseek", api_key="k", model="m", priority=1)
    router.providers["deepseek"].enabled = False
    router.add_provider("mock", priority=2)
    result = router.complete([{"role": "user", "content": "hi"}])
    assert result.provider == "mock"


def test_complete_with_unconfigured_provider_falls_through():
    router = _offline_router()
    router.add_provider("mock", priority=1)
    result = router.complete(
        [{"role": "user", "content": "hi"}], provider="nonexistent"
    )
    # 指定 provider 不存在 → 跳过 → 走到 mock
    assert result.provider == "mock"
    assert result.error is None


def test_complete_all_fail_with_cooldown():
    router = _offline_router()
    router.add_provider("deepseek", api_key="", model="m", priority=1)
    router.providers["deepseek"]._cooldown_until = time.time() + 100
    result = router.complete([{"role": "user", "content": "hi"}])
    assert result.error == "all providers failed"
    assert result.provider == "none"
    assert result.model == "none"


def test_complete_all_providers_fail_returns_error_result():
    router = _offline_router()
    router.add_provider("deepseek", api_key="", model="m", priority=1)
    router.add_provider("openai", api_key="", model="m", priority=2)
    result = router.complete([{"role": "user", "content": "hi"}])
    assert result.error == "all providers failed"
    assert result.provider == "none"


def test_complete_provider_param_overrides_route(monkeypatch):
    router = _offline_router()
    router.add_provider("groq", api_key="fake", model="llama", priority=1)
    router.add_provider("openai", api_key="fake", model="gpt", priority=2)
    called = []

    def fake_urllib(self, pconfig, messages, max_tokens, t0):
        called.append(pconfig.name)
        return CompletionResult(content="ok", provider=pconfig.name,
                                model=pconfig.model)

    monkeypatch.setattr(LLMRouter, "_call_urllib", fake_urllib)
    # 指定 openai（priority=2）→ 应优先调用 openai
    result = router.complete(
        [{"role": "user", "content": "hi"}], provider="openai"
    )
    assert result.provider == "openai"
    assert called[0] == "openai"


# ============================================================
# llm_router.py — 冷却机制边界
# ============================================================

def test_cooldown_triggered_exactly_at_three_failures():
    router = _offline_router()
    router.add_provider("deepseek", api_key="", model="m", priority=1)
    router.add_provider("mock", priority=2)
    for _ in range(3):
        router.complete([{"role": "user", "content": "hi"}])
    ds = router.providers["deepseek"]
    assert ds._failures == 3
    assert ds._cooldown_until > time.time()
    assert ds._last_failure > 0


def test_cooldown_skips_provider_on_fourth_call():
    router = _offline_router()
    router.add_provider("deepseek", api_key="", model="m", priority=1)
    router.add_provider("mock", priority=2)
    for _ in range(3):
        router.complete([{"role": "user", "content": "hi"}])
    assert router.providers["deepseek"]._failures == 3
    # 第 4 次：deepseek 在冷却中 → 跳过，failures 不增加
    router.complete([{"role": "user", "content": "hi"}])
    assert router.providers["deepseek"]._failures == 3


def test_cooldown_recovery_after_expiry():
    router = _offline_router()
    router.add_provider("deepseek", api_key="", model="m", priority=1)
    router.add_provider("mock", priority=2)
    for _ in range(3):
        router.complete([{"role": "user", "content": "hi"}])
    ds = router.providers["deepseek"]
    # 把冷却时间设到过去 → provider 恢复可被尝试
    ds._cooldown_until = time.time() - 1
    router.complete([{"role": "user", "content": "hi"}])
    # deepseek 被再次尝试并失败 → failures 增加
    assert ds._failures == 4


def test_reset_failures_unknown_provider_noop():
    router = _offline_router()
    router.add_provider("mock", priority=1)
    router.reset_failures("nonexistent")  # 不应报错
    assert router.providers["mock"]._failures == 0


def test_reset_failures_specific_and_all():
    router = _offline_router()
    router.add_provider("deepseek", api_key="", model="m", priority=1)
    router.add_provider("mock", priority=2)
    for _ in range(3):
        router.complete([{"role": "user", "content": "hi"}])
    assert router.providers["deepseek"]._failures == 3
    # 重置单个
    router.reset_failures("deepseek")
    assert router.providers["deepseek"]._failures == 0
    assert router.providers["deepseek"]._cooldown_until == 0
    # 再次触发后重置全部
    for _ in range(3):
        router.complete([{"role": "user", "content": "hi"}])
    router.reset_failures()
    assert router.providers["deepseek"]._failures == 0
    assert router.providers["mock"]._failures == 0


# ============================================================
# llm_router.py — status / dataclass 字段
# ============================================================

def test_status_reports_all_provider_fields():
    router = _offline_router()
    router.add_provider("deepseek", api_key="k", model="m", priority=1,
                        cost_per_1k=0.001)
    router.add_provider("mock", priority=2)
    status = router.status()
    assert status["litellm_available"] == LITELLM_AVAILABLE
    assert "deepseek" in status["providers"]
    assert "mock" in status["providers"]
    ds = status["providers"]["deepseek"]
    assert ds["model"] == "m"
    assert ds["priority"] == 1
    assert ds["enabled"] is True
    assert ds["failures"] == 0
    assert ds["in_cooldown"] is False
    assert ds["cost_per_1k"] == 0.001


def test_status_shows_cooldown_true():
    router = _offline_router()
    router.add_provider("deepseek", api_key="", model="m", priority=1)
    router.add_provider("mock", priority=2)
    for _ in range(3):
        router.complete([{"role": "user", "content": "hi"}])
    status = router.status()
    assert status["providers"]["deepseek"]["in_cooldown"] is True
    assert status["providers"]["deepseek"]["failures"] == 3
    assert status["providers"]["mock"]["in_cooldown"] is False


def test_provider_config_runtime_state_fields():
    p = ProviderConfig(name="x")
    assert p._failures == 0
    assert p._last_failure == 0.0
    assert p._cooldown_until == 0.0
    p._failures = 2
    p._last_failure = 123.0
    p._cooldown_until = 456.0
    assert p._failures == 2


def test_completion_result_all_fields():
    r = CompletionResult(
        content="c", provider="p", model="m",
        tokens_used=10, cost=0.1, latency_ms=5,
        error=None, fallback_used=True,
    )
    assert r.content == "c"
    assert r.provider == "p"
    assert r.model == "m"
    assert r.tokens_used == 10
    assert r.cost == 0.1
    assert r.latency_ms == 5
    assert r.error is None
    assert r.fallback_used is True


def test_mock_response_no_user_message():
    router = _offline_router()
    out = router._mock_response([{"role": "system", "content": "sys"}])
    assert isinstance(out, str)


# ============================================================
# llm_router.py — litellm 路径
# ============================================================

def test_init_litellm_available_loads_module(monkeypatch):
    """LITELLM_AVAILABLE=True 且 import 成功 → _litellm 被赋值。"""
    fake_litellm = MagicMock(name="litellm-module")
    monkeypatch.setitem(sys.modules, "litellm", fake_litellm)
    monkeypatch.setattr(lr_mod, "LITELLM_AVAILABLE", True)
    router = LLMRouter()
    assert router._litellm is fake_litellm


def test_init_litellm_import_error_handled(monkeypatch):
    """LITELLM_AVAILABLE=True 但 import 失败 → _litellm 保持 None。"""
    monkeypatch.setattr(lr_mod, "LITELLM_AVAILABLE", True)
    monkeypatch.setitem(sys.modules, "litellm", None)  # import 抛 ImportError
    router = LLMRouter()
    assert router._litellm is None


def test_litellm_completion_success_path():
    router = _offline_router()
    fake_response = MagicMock()
    fake_response.choices = [MagicMock()]
    fake_response.choices[0].message.content = "litellm-reply"
    fake_response.usage.total_tokens = 777
    fake_litellm = MagicMock()
    fake_litellm.completion.return_value = fake_response
    router._litellm = fake_litellm
    router.add_provider("deepseek", api_key="dk", model="deepseek-chat",
                        cost_per_1k=0.001, priority=1,
                        temperature=0.2, max_tokens=100, timeout=15)
    result = router.complete([{"role": "user", "content": "hi"}])
    assert result.error is None
    assert result.content == "litellm-reply"
    assert result.tokens_used == 777
    assert abs(result.cost - 777 * 0.001 / 1000) < 1e-9
    # 验证 litellm.completion 调用参数
    kwargs = fake_litellm.completion.call_args.kwargs
    assert kwargs["model"] == "deepseek/deepseek-chat"
    assert kwargs["api_key"] == "dk"
    assert kwargs["temperature"] == 0.2
    assert kwargs["max_tokens"] == 100
    assert kwargs["timeout"] == 15


def test_litellm_completion_max_tokens_override():
    router = _offline_router()
    fake_response = MagicMock()
    fake_response.choices = [MagicMock()]
    fake_response.choices[0].message.content = "reply"
    fake_response.usage.total_tokens = 5
    fake_litellm = MagicMock()
    fake_litellm.completion.return_value = fake_response
    router._litellm = fake_litellm
    router.add_provider("deepseek", api_key="dk", model="m",
                        max_tokens=50, priority=1)
    router.complete([{"role": "user", "content": "hi"}], max_tokens=300)
    assert fake_litellm.completion.call_args.kwargs["max_tokens"] == 300


def test_litellm_completion_no_usage():
    router = _offline_router()
    fake_response = MagicMock()
    fake_response.choices = [MagicMock()]
    fake_response.choices[0].message.content = "reply"
    fake_response.usage = None
    fake_litellm = MagicMock()
    fake_litellm.completion.return_value = fake_response
    router._litellm = fake_litellm
    router.add_provider("groq", api_key="gk", model="llama", priority=1)
    result = router.complete([{"role": "user", "content": "hi"}])
    assert result.error is None
    assert result.tokens_used == 0
    assert result.cost == 0.0


def test_litellm_completion_error_branch():
    router = _offline_router()
    fake_litellm = MagicMock()
    fake_litellm.completion.side_effect = RuntimeError("litellm boom")
    router._litellm = fake_litellm
    router.add_provider("deepseek", api_key="dk", model="m", priority=1)
    pconfig = router.providers["deepseek"]
    result = router._call_provider(
        pconfig, [{"role": "user", "content": "hi"}], None
    )
    assert result.error == "litellm boom"
    assert result.provider == "deepseek"
    assert result.latency_ms >= 0


def test_ollama_skips_litellm_goes_to_urllib():
    """_litellm 已设置但 ollama 仍走 urllib 路径（_call_provider 中 name != 'ollama' 判断）。"""
    router = _offline_router()
    fake_litellm = MagicMock()
    router._litellm = fake_litellm
    router.add_provider("ollama", model="llama3.2",
                        base_url="http://localhost:11434/api/chat", priority=1)
    urlopen_called = []

    def fake_urlopen(req, timeout=None):
        urlopen_called.append(req)
        return _FakeResponse(_ollama_body("ollama-no-litellm"))

    with patch("superclaw.llm_router._ureq.urlopen", side_effect=fake_urlopen):
        result = router.complete([{"role": "user", "content": "hi"}])
    # ollama 走了 urllib，没走 litellm
    assert len(urlopen_called) == 1
    fake_litellm.completion.assert_not_called()
    assert result.provider == "ollama"
    assert result.error is None
    # 响应按 OpenAI 格式解析 → ollama 响应无 choices → content 为空（源码实际行为）
    assert result.content == ""


# ============================================================
# llm_router.py — get_router 单例
# ============================================================

def test_get_router_returns_singleton(monkeypatch):
    monkeypatch.setattr(lr_mod, "_router", None)
    monkeypatch.setattr(LLMRouter, "_check_ollama", lambda self: False)
    for k in ["DEEPSEEK_API_KEY", "GROQ_API_KEY", "OPENAI_API_KEY",
              "OPENROUTER_API_KEY", "OLLAMA_BASE_URL"]:
        monkeypatch.delenv(k, raising=False)
    r1 = get_router()
    r2 = get_router()
    assert r1 is r2
    # add_from_env 至少添加了 mock 兜底
    assert "mock" in r1.providers
    # 清理全局状态，避免影响其他测试
    monkeypatch.setattr(lr_mod, "_router", None)
