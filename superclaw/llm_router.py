"""
superclaw LLM 自动路由 SDK — 移植自 LiteLLM 的核心理念

功能:
- 统一接口: 一个 API 调用 100+ LLM 提供商
- 自动路由: 根据任务类型/成本/延迟自动选择最佳模型
- 故障转移: 主模型失败自动切换到备用模型
- 成本优化: 简单任务用便宜模型，复杂任务用高级模型
- 零依赖: 优先用 litellm（如果安装），否则用内置 urllib

使用方式:
    from superclaw.llm_router import LLMRouter

    router = LLMRouter()
    router.add_provider("deepseek", api_key="...", model="deepseek-chat", priority=1)
    router.add_provider("groq", api_key="...", model="llama-3.1-8b-instant", priority=2)
    router.add_provider("mock", priority=99)  # 兜底

    # 自动路由 — 根据任务复杂度选模型
    response = router.complete("简单问题", complexity="low")
    response = router.complete("复杂推理", complexity="high")

    # 指定模型
    response = router.complete("问题", provider="deepseek")
"""
import json
import os
import time
import random
import importlib.util
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from urllib import request as _ureq
from urllib import error as _uerr

# 检测 litellm 是否安装
LITELLM_AVAILABLE = importlib.util.find_spec("litellm") is not None


@dataclass
class ProviderConfig:
    """单个 LLM Provider 配置"""
    name: str                    # provider 名称
    api_key: str = ""            # API Key
    model: str = ""              # 模型名
    base_url: str = ""           # 自定义 base URL
    priority: int = 10           # 优先级（1 最高）
    max_tokens: int = 2048
    temperature: float = 0.7
    timeout: int = 60
    cost_per_1k: float = 0.0    # 每 1K token 成本（美元）
    enabled: bool = True
    # 运行时状态
    _failures: int = 0
    _last_failure: float = 0.0
    _cooldown_until: float = 0.0


@dataclass
class CompletionResult:
    """LLM 调用结果"""
    content: str
    provider: str
    model: str
    tokens_used: int = 0
    cost: float = 0.0
    latency_ms: int = 0
    error: Optional[str] = None
    fallback_used: bool = False


# 复杂度 → 模型偏好映射
_COMPLEXITY_PREFERENCE = {
    "low": ["groq", "deepseek", "openai", "ollama", "mock"],     # 简单任务用快/便宜模型
    "medium": ["deepseek", "openai", "groq", "ollama", "mock"],  # 中等任务
    "high": ["openai", "deepseek", "groq", "ollama", "mock"],    # 复杂任务用强模型
}


class LLMRouter:
    """LLM 自动路由器

    核心理念（来自 LiteLLM）:
    1. 统一接口: 所有 provider 用同一个 complete() 方法
    2. 自动路由: 根据复杂度/成本/延迟选模型
    3. 故障转移: 主模型失败自动切到备用
    4. 冷却机制: 连续失败的 provider 暂时跳过
    """

    def __init__(self):
        self.providers: Dict[str, ProviderConfig] = {}
        self._litellm = None

        # 如果 litellm 可用，优先使用
        if LITELLM_AVAILABLE:
            try:
                import litellm
                self._litellm = litellm
                # litellm 可以自动处理很多 provider
            except ImportError:
                pass

    def add_provider(self, name: str, api_key: str = "", model: str = "",
                     base_url: str = "", priority: int = 10,
                     max_tokens: int = 2048, temperature: float = 0.7,
                     timeout: int = 60, cost_per_1k: float = 0.0) -> None:
        """添加 LLM Provider"""
        self.providers[name] = ProviderConfig(
            name=name, api_key=api_key, model=model, base_url=base_url,
            priority=priority, max_tokens=max_tokens, temperature=temperature,
            timeout=timeout, cost_per_1k=cost_per_1k,
        )

    def add_from_env(self) -> None:
        """从环境变量自动添加已配置的 Provider"""
        env_map = {
            "deepseek": ("DEEPSEEK_API_KEY", "deepseek-chat",
                         "https://api.deepseek.com/chat/completions", 0.001),
            "groq": ("GROQ_API_KEY", "llama-3.1-8b-instant",
                     "https://api.groq.com/openai/v1/chat/completions", 0.0002),
            "openrouter": ("OPENROUTER_API_KEY", "anthropic/claude-3-haiku",
                           "https://openrouter.ai/api/v1/chat/completions", 0.005),
            "openai": ("OPENAI_API_KEY", "gpt-4o-mini",
                       "https://api.openai.com/v1/chat/completions", 0.002),
        }

        for name, (env_key, default_model, default_url, cost) in env_map.items():
            api_key = os.environ.get(env_key, "")
            if api_key:
                self.add_provider(
                    name=name,
                    api_key=api_key,
                    model=os.environ.get(f"{name.upper()}_MODEL", default_model),
                    base_url=os.environ.get(f"{name.upper()}_BASE_URL", default_url),
                    priority=len(self.providers) + 1,
                    cost_per_1k=cost,
                )

        # Ollama（本地，无需 key）
        if os.environ.get("OLLAMA_BASE_URL") or self._check_ollama():
            self.add_provider(
                name="ollama",
                api_key="",
                model=os.environ.get("OLLAMA_MODEL", "llama3.2"),
                base_url=os.environ.get("OLLAMA_BASE_URL",
                                        "http://localhost:11434/api/chat"),
                priority=len(self.providers) + 1,
                cost_per_1k=0.0,  # 本地免费
            )

        # Mock 兜底
        self.add_provider(name="mock", priority=99, cost_per_1k=0.0)

    def _check_ollama(self) -> bool:
        """检查 Ollama 是否在运行"""
        try:
            req = _ureq.Request("http://localhost:11434/api/tags",
                                headers={"User-Agent": "superclaw"})
            with _ureq.urlopen(req, timeout=2) as resp:
                return resp.status == 200
        except Exception:
            return False

    def complete(self, messages: List[Dict[str, Any]],
                 complexity: str = "medium",
                 provider: Optional[str] = None,
                 max_tokens: Optional[int] = None) -> CompletionResult:
        """调用 LLM（自动路由 + 故障转移）

        Args:
            messages: 消息列表 [{"role": "user", "content": "..."}]
            complexity: 任务复杂度 "low"/"medium"/"high"
            provider: 指定 provider（跳过自动路由）
            max_tokens: 最大 token 数

        Returns:
            CompletionResult
        """
        # 确定尝试顺序
        if provider:
            order = [provider] + [p for p in self.providers if p != provider]
        else:
            order = self._route(complexity)

        # 尝试每个 provider
        for pname in order:
            pconfig = self.providers.get(pname)
            if not pconfig or not pconfig.enabled:
                continue

            # 冷却检查
            if time.time() < pconfig._cooldown_until:
                continue

            result = self._call_provider(pconfig, messages, max_tokens)
            if result.error is None:
                return result

            # 记录失败
            pconfig._failures += 1
            pconfig._last_failure = time.time()
            if pconfig._failures >= 3:
                pconfig._cooldown_until = time.time() + 60  # 冷却 60 秒

        # 所有 provider 都失败
        return CompletionResult(
            content="[错误] 所有 LLM Provider 都不可用",
            provider="none", model="none",
            error="all providers failed",
        )

    def _route(self, complexity: str) -> List[str]:
        """自动路由 — 根据复杂度选择 provider 顺序"""
        preference = _COMPLEXITY_PREFERENCE.get(complexity,
                                                 _COMPLEXITY_PREFERENCE["medium"])

        # 按偏好排序，只保留已配置的
        ordered = [p for p in preference if p in self.providers]
        # 补充未在偏好列表中的 provider
        for p in sorted(self.providers.keys(),
                        key=lambda x: self.providers[x].priority):
            if p not in ordered:
                ordered.append(p)

        return ordered

    def _call_provider(self, pconfig: ProviderConfig,
                       messages: List[Dict[str, Any]],
                       max_tokens: Optional[int]) -> CompletionResult:
        """调用单个 provider"""
        t0 = time.time()

        # Mock provider
        if pconfig.name == "mock":
            content = self._mock_response(messages)
            return CompletionResult(
                content=content,
                provider="mock", model="mock",
                latency_ms=int((time.time() - t0) * 1000),
            )

        # 检查 API Key
        if not pconfig.api_key and pconfig.name != "ollama":
            return CompletionResult(
                content="", provider=pconfig.name, model=pconfig.model,
                error=f"{pconfig.name.upper()}_API_KEY 未配置",
            )

        # 使用 litellm（如果可用）
        if self._litellm and pconfig.name != "ollama":
            try:
                response = self._litellm.completion(
                    model=f"{pconfig.name}/{pconfig.model}",
                    messages=messages,
                    api_key=pconfig.api_key,
                    temperature=pconfig.temperature,
                    max_tokens=max_tokens or pconfig.max_tokens,
                    timeout=pconfig.timeout,
                )
                content = response.choices[0].message.content
                tokens = response.usage.total_tokens if response.usage else 0
                return CompletionResult(
                    content=content,
                    provider=pconfig.name, model=pconfig.model,
                    tokens_used=tokens,
                    cost=tokens * pconfig.cost_per_1k / 1000,
                    latency_ms=int((time.time() - t0) * 1000),
                )
            except Exception as e:
                return CompletionResult(
                    content="", provider=pconfig.name, model=pconfig.model,
                    error=str(e), latency_ms=int((time.time() - t0) * 1000),
                )

        # 使用 urllib（内置，无依赖）
        return self._call_urllib(pconfig, messages, max_tokens, t0)

    def _call_urllib(self, pconfig: ProviderConfig,
                     messages: List[Dict[str, Any]],
                     max_tokens: Optional[int], t0: float) -> CompletionResult:
        """使用 urllib 调用 OpenAI 兼容 API"""
        try:
            if pconfig.name == "ollama":
                # Ollama API 格式
                payload = json.dumps({
                    "model": pconfig.model,
                    "messages": messages,
                    "stream": False,
                    "options": {"temperature": pconfig.temperature},
                }).encode("utf-8")
                headers = {"Content-Type": "application/json"}
            else:
                # OpenAI 兼容格式
                payload = json.dumps({
                    "model": pconfig.model,
                    "messages": messages,
                    "temperature": pconfig.temperature,
                    "max_tokens": max_tokens or pconfig.max_tokens,
                }).encode("utf-8")
                headers = {
                    "Authorization": f"Bearer {pconfig.api_key}",
                    "Content-Type": "application/json",
                }
                if pconfig.name == "openrouter":
                    headers["HTTP-Referer"] = "https://superclaw.local"
                    headers["X-Title"] = "superclaw"

            req = _ureq.Request(
                pconfig.base_url, data=payload,
                headers=headers, method="POST",
            )

            with _ureq.urlopen(req, timeout=pconfig.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            content = data.get("choices", [{}])[0] \
                .get("message", {}).get("content", "")
            tokens = data.get("usage", {}).get("total_tokens", 0)

            return CompletionResult(
                content=content,
                provider=pconfig.name, model=pconfig.model,
                tokens_used=tokens,
                cost=tokens * pconfig.cost_per_1k / 1000,
                latency_ms=int((time.time() - t0) * 1000),
            )

        except _uerr.URLError as e:
            return CompletionResult(
                content="", provider=pconfig.name, model=pconfig.model,
                error=f"网络错误: {e}",
                latency_ms=int((time.time() - t0) * 1000),
            )
        except Exception as e:
            return CompletionResult(
                content="", provider=pconfig.name, model=pconfig.model,
                error=str(e),
                latency_ms=int((time.time() - t0) * 1000),
            )

    def _mock_response(self, messages: List[Dict[str, Any]]) -> str:
        """Mock 响应"""
        last_user = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                last_user = m.get("content", "")
                break

        templates = [
            f"我来分析这个问题。你问的是：{last_user[:50]}...",
            f"明白了。让我思考一下：{last_user[:50]}。",
            f"好的，我来处理。{last_user[:30]}...",
        ]
        return random.choice(templates)

    def status(self) -> Dict[str, Any]:
        """获取路由器状态"""
        return {
            "litellm_available": LITELLM_AVAILABLE,
            "providers": {
                name: {
                    "model": p.model,
                    "priority": p.priority,
                    "enabled": p.enabled,
                    "failures": p._failures,
                    "in_cooldown": time.time() < p._cooldown_until,
                    "cost_per_1k": p.cost_per_1k,
                }
                for name, p in self.providers.items()
            },
        }

    def reset_failures(self, provider: Optional[str] = None) -> None:
        """重置失败计数"""
        if provider:
            p = self.providers.get(provider)
            if p:
                p._failures = 0
                p._cooldown_until = 0
        else:
            for p in self.providers.values():
                p._failures = 0
                p._cooldown_until = 0


# 全局单例
_router: Optional[LLMRouter] = None


def get_router() -> LLMRouter:
    """获取全局 LLMRouter 单例"""
    global _router
    if _router is None:
        _router = LLMRouter()
        _router.add_from_env()
    return _router
