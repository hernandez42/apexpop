#!/usr/bin/env python3
"""
superclaw LLM Provider 系统
支持多种 LLM API 接入，可配置，可回退

Provider 列表：
- mock: 模拟（无需 API key，用于测试）
- deepseek: DeepSeek API
- groq: Groq
- openrouter: OpenRouter
- openai: OpenAI
- ollama: 本地 Ollama
"""

import json
import os
import sys
import random
import time
import urllib.request
import urllib.error
import urllib.parse
from pathlib import Path
from dataclasses import dataclass
from typing import Optional, List, Dict, Any

# 项目根目录（从环境变量读，或从 config.py 读）
SUPERCLAW_ROOT = Path(__file__).parent.resolve()
SYS_PROMPT_DIR = SUPERCLAW_ROOT / "config" / "prompts"
SYS_PROMPT_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class LLMConfig:
    provider: str
    api_key: str
    base_url: str
    model: str
    timeout: int = 60
    max_tokens: int = 2048
    temperature: float = 0.7


class BaseProvider:
    """Provider 基类"""

    def __init__(self, config: LLMConfig):
        self.config = config

    def call(self, prompt: str, system_prompt: str = "") -> str:
        raise NotImplementedError


class MockProvider(BaseProvider):
    """模拟 Provider — 基于简单规则生成有用的回应"""

    EVOLUTION_TEMPLATES = [
        "分析当前短板：{weakness}。建议在{domain}领域增强变异能力。",
        "系统状态良好。建议持续关注知识模块并增加基因多样性。",
        "检测到潜在瓶颈：技能生成速率不足。建议优化变异策略。",
        "当前维度中{weak_domain}最弱，需要针对性补强。建议：探索新领域。",
        "系统正在进化。建议保留高适应度基因，清除低适应度噪声。",
        "分析：系统知识储备需要扩展。建议增加新知识基因。",
    ]

    ANALYSIS_TEMPLATES = [
        "基于当前状态分析：{weakness} 在 {domain} 领域需要加强。",
        "进化分析：当前 fitness {fitness:.3f}，平衡度 {balance:.3f}。",
        "建议行动：加强变异强度到 {strength}，在 {domain} 领域探索。",
        "检测到：{weakness}。建议：立即在 {domain} 生成新基因。",
    ]

    def call(self, prompt: str, system_prompt: str = "") -> str:
        """模拟 LLM 回应"""
        # 从 prompt 中提取关键词以增加反应相关性
        weakness = "skills_count_low"
        if "knowledge_count_low" in prompt:
            weakness = "knowledge_count_low"
        elif "balance_low" in prompt:
            weakness = "balance_low"
        elif "fitness_low" in prompt:
            weakness = "fitness_low"

        # 随机选择一个回应模板
        if "分析" in prompt or "analyze" in prompt.lower():
            template = random.choice(self.ANALYSIS_TEMPLATES)
        else:
            template = random.choice(self.EVOLUTION_TEMPLATES)

        # 填充变量
        result = template.format(
            weakness=weakness,
            domain=random.choice(["变异", "知识", "探索", "共进化"]),
            weak_domain=weakness,
            fitness=random.uniform(0.5, 1.5),
            balance=random.uniform(0.3, 0.9),
            strength=random.uniform(0.05, 0.2),
        )

        # 增加延迟模拟真实 API
        time.sleep(0.1)
        return result


class OpenAICompatibleProvider(BaseProvider):
    """OpenAI 兼容 API 调用（用于 DeepSeek/Groq/OpenRouter/OpenAI 等）"""

    def call(self, prompt: str, system_prompt: str = "") -> str:
        if not self.config.api_key:
            return f"[Error: {self.config.provider} API key not configured]"

        try:
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": prompt})

            payload = json.dumps({
                "model": self.config.model,
                "messages": messages,
                "temperature": self.config.temperature,
                "max_tokens": self.config.max_tokens,
            }).encode("utf-8")

            headers = {
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json",
            }

            # OpenRouter 需要额外 headers
            if self.config.provider == "openrouter":
                headers["HTTP-Referer"] = "https://superclaw.local"
                headers["X-Title"] = "superclaw"

            req = urllib.request.Request(
                self.config.base_url,
                data=payload,
                headers=headers,
                method="POST",
            )

            with urllib.request.urlopen(req, timeout=self.config.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data["choices"][0]["message"]["content"]

        except urllib.error.URLError as e:
            return f"[LLM Error: {self.config.provider}] {e}"
        except Exception as e:
            return f"[LLM Error: {self.config.provider}] {e}"


class OllamaProvider(BaseProvider):
    """本地 Ollama"""

    def call(self, prompt: str, system_prompt: str = "") -> str:
        try:
            payload = json.dumps({
                "model": self.config.model,
                "prompt": prompt,
                "system": system_prompt,
                "stream": False,
            }).encode("utf-8")

            req = urllib.request.Request(
                self.config.base_url or "http://localhost:11434/api/generate",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )

            with urllib.request.urlopen(req, timeout=self.config.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data.get("response", "")

        except ConnectionRefusedError:
            return "[LLM Error: Ollama not running on localhost:11434]"
        except Exception as e:
            return f"[LLM Error: ollama] {e}"


def get_provider(provider_name: str) -> BaseProvider:
    """根据名称获取 Provider 实例"""

    # 默认配置
    configs = {
        "deepseek": LLMConfig(
            provider="deepseek",
            api_key=os.environ.get("DEEPSEEK_API_KEY", ""),
            base_url="https://api.deepseek.com/chat/completions",
            model="deepseek-chat",
        ),
        "groq": LLMConfig(
            provider="groq",
            api_key=os.environ.get("GROQ_API_KEY", ""),
            base_url="https://api.groq.com/openai/v1/chat/completions",
            model="llama-3.1-8b-instant",
        ),
        "openrouter": LLMConfig(
            provider="openrouter",
            api_key=os.environ.get("OPENROUTER_API_KEY", ""),
            base_url="https://openrouter.ai/api/v1/chat/completions",
            model="anthropic/claude-3-haiku",
        ),
        "openai": LLMConfig(
            provider="openai",
            api_key=os.environ.get("OPENAI_API_KEY", ""),
            base_url="https://api.openai.com/v1/chat/completions",
            model="gpt-4o-mini",
        ),
        "ollama": LLMConfig(
            provider="ollama",
            api_key="",
            base_url="http://localhost:11434/api/generate",
            model="llama3.2",
        ),
        "mock": LLMConfig(
            provider="mock",
            api_key="",
            base_url="",
            model="mock",
        ),
    }

    # 从配置文件覆盖
    config_file = SUPERCLAW_ROOT / "config" / "superclaw.json"
    if config_file.exists():
        try:
            with open(config_file) as f:
                cfg = json.load(f)
            llm_cfg = cfg.get("llm", {})
            pname = llm_cfg.get("provider", provider_name)
            if pname in configs:
                cfg_obj = configs[pname]
                if llm_cfg.get("model"):
                    cfg_obj.model = llm_cfg["model"]
                if llm_cfg.get("timeout"):
                    cfg_obj.timeout = llm_cfg["timeout"]
                if llm_cfg.get("max_tokens"):
                    cfg_obj.max_tokens = llm_cfg["max_tokens"]
                if llm_cfg.get("temperature") is not None:
                    cfg_obj.temperature = llm_cfg["temperature"]
                # 从文件里的 env key 名 读取真实 API key
                env_name = llm_cfg.get("api_key_env", {}).get(pname, "")
                if env_name and os.environ.get(env_name):
                    cfg_obj.api_key = os.environ.get(env_name, "")
        except (json.JSONDecodeError, IOError):
            pass

    cfg = configs.get(provider_name, configs["mock"])

    # 返回 Provider
    if provider_name == "ollama":
        return OllamaProvider(cfg)
    elif provider_name == "mock":
        return MockProvider(cfg)
    else:
        return OpenAICompatibleProvider(cfg)


# 系统级默认 prompt
DEFAULT_SYSTEM_PROMPT = """你是 superclaw — 一个自我进化的 AI 系统的大脑。

你的角色：
- 分析系统状态，发现短板
- 决定进化方向（变异、探索、共进化）
- 评估改进方案，给出生成的基因参数
- 简洁、直接地给出建议

输出格式：尽量给出 JSON 格式的决策，包含：
- domain: 领域（变异/知识/探索/共进化/评估）
- action: 动作（mutate/explore/learn/balance）
- change: 变异强度（0.05 ~ 0.2）
- reason: 理由（简洁）
"""


def call_llm(prompt: str, provider: str = "mock",
              system_prompt: str = DEFAULT_SYSTEM_PROMPT) -> str:
    """简化调用入口"""
    prov = get_provider(provider)
    return prov.call(prompt, system_prompt)


if __name__ == "__main__":
    # 快速测试
    provider = sys.argv[1] if len(sys.argv) > 1 else "mock"
    prompt = sys.argv[2] if len(sys.argv) > 2 else "分析当前系统状态"
    result = call_llm(prompt, provider=provider)
    print(f"Provider: {provider}")
    print(f"Response: {result}")
