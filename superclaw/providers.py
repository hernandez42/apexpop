"""
superclaw LLM Provider 系统
支持：mock / deepseek / groq / openrouter / openai / ollama
"""
import json
import random
from typing import Any, Dict, List
from urllib import request as _ureq
from urllib import error as _uerr

from .config import LLMConfig


class BaseProvider:
    """Provider 基类"""

    def __init__(self, cfg: LLMConfig):
        self.cfg = cfg

    def call(self, messages: List[Dict[str, Any]]) -> str:
        """调用 LLM，返回响应文本"""
        raise NotImplementedError


class MockProvider(BaseProvider):
    """模拟 Provider — 不需要 API Key，基于规则生成响应"""

    # 思考/推理/分析响应模板
    THINK_TEMPLATES = [
        "我来分析这个问题。首先，用户希望{action}。让我检查当前状态。",
        "好的，让我思考一下。你的问题是：{prompt}。",
        "明白了。{summary}我现在开始处理。",
    ]

    def call(self, messages: List[Dict[str, Any]]) -> str:
        # 提取最后一条用户消息
        last_user = ""
        for m in reversed(messages):
            if m["role"] == "user":
                last_user = m["content"]
                break

        prompt = last_user[:100]
        action = "了解情况"
        if "执行" in prompt or "运行" in prompt or "执行" in prompt:
            action = "执行命令"
        elif "读取" in prompt or "文件" in prompt:
            action = "读取文件"
        elif "搜索" in prompt or "查找" in prompt:
            action = "搜索信息"
        elif "分析" in prompt or "思考" in prompt:
            action = "分析问题"

        summary = f"你希望{action}。"

        # 小概率使用 tool_calls 触发工具调用（在真实场景）
        return random.choice(self.THINK_TEMPLATES).format(
            action=action, prompt=prompt, summary=summary
        )


class OpenAICompatibleProvider(BaseProvider):
    """OpenAI 兼容 API — DeepSeek/Groq/OpenRouter/OpenAI"""

    def call(self, messages: List[Dict[str, Any]]) -> str:
        if not self.cfg.api_key:
            return f"[错误] {self.cfg.provider.upper()}_API_KEY 未配置"

        url = self.cfg.base_url or self._default_url()
        if not url:
            return f"[错误] {self.cfg.provider} base_url 未配置"

        payload = json.dumps({
            "model": self.cfg.model,
            "messages": messages,
            "temperature": self.cfg.temperature,
            "max_tokens": self.cfg.max_tokens,
        }).encode("utf-8")

        headers = {
            "Authorization": f"Bearer {self.cfg.api_key}",
            "Content-Type": "application/json",
        }
        # OpenRouter 需要额外 header
        if self.cfg.provider == "openrouter":
            headers["HTTP-Referer"] = "https://superclaw.local"
            headers["X-Title"] = "superclaw"

        req = _ureq.Request(url, data=payload, headers=headers, method="POST")
        try:
            with _ureq.urlopen(req, timeout=self.cfg.timeout) as resp:  # nosec B310 - url 为管理员配置/HTTPS 默认值，已设 timeout
                data = json.loads(resp.read().decode("utf-8"))
            return data["choices"][0]["message"]["content"]
        except _uerr.URLError as e:
            return f"[网络错误] {e}"
        except Exception as e:
            return f"[LLM 错误] {e}"

    def _default_url(self) -> str:
        urls = {
            "deepseek": "https://api.deepseek.com/chat/completions",
            "groq": "https://api.groq.com/openai/v1/chat/completions",
            "openrouter": "https://openrouter.ai/api/v1/chat/completions",
            "openai": "https://api.openai.com/v1/chat/completions",
        }
        return urls.get(self.cfg.provider, "")


class OllamaProvider(BaseProvider):
    """本地 Ollama"""

    def call(self, messages: List[Dict[str, Any]]) -> str:
        url = self.cfg.base_url or "http://localhost:11434/api/chat"
        payload = json.dumps({
            "model": self.cfg.model or "llama3.2",
            "messages": messages,
            "stream": False,
        }).encode("utf-8")

        headers = {"Content-Type": "application/json"}

        req = _ureq.Request(url, data=payload, headers=headers, method="POST")
        try:
            with _ureq.urlopen(req, timeout=self.cfg.timeout) as resp:  # nosec B310 - url 为管理员配置(默认 localhost Ollama)，已设 timeout
                data = json.loads(resp.read().decode("utf-8"))
            return data.get("message", {}).get("content", "")
        except ConnectionRefusedError:
            return "[错误] Ollama 未运行（http://localhost:11434）"
        except Exception as e:
            return f"[Ollama 错误] {e}"


PROVIDERS = {
    "mock": MockProvider,
    "deepseek": OpenAICompatibleProvider,
    "groq": OpenAICompatibleProvider,
    "openrouter": OpenAICompatibleProvider,
    "openai": OpenAICompatibleProvider,
    "ollama": OllamaProvider,
}


def list_providers() -> List[str]:
    return sorted(PROVIDERS.keys())


def get_provider(cfg: LLMConfig) -> BaseProvider:
    """根据配置获取 Provider"""
    provider_name = cfg.provider.lower()
    cls = PROVIDERS.get(provider_name, MockProvider)
    return cls(cfg)


# 默认 system prompt — 驱动思考、推理、分析
SYSTEM_PROMPT = """你是 superclaw，一个具备进化能力的 AI Agent（注：进化指脚本驱动的 gap 检测+代码生成+验证循环，非自主意识）。

你的能力：
- 思考（think）：遇到问题先分析再行动
- 推理（reason）：基于信息做逻辑推断
- 分析（analyze）：拆解问题并给出方案
- 工具调用（tool）：使用文件、shell、网络等工具

工作方式：
1. 收到用户消息后，先理解意图
2. 如果需要信息，调用 file_read / shell / web 工具
3. 在行动前，用 think 工具做分析
4. 给用户清晰、简洁、有用的回答

不要长篇大论，先做事再解释。"""
