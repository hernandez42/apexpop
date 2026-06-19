"""
superclaw LLM Provider 系统
支持：mock / deepseek / groq / openrouter / openai / agnesai / ollama

核心设计：
- Provider 输出 `<tool name> <param>val</param></tool>` 格式的工具调用
- 由 Agent 层解析并执行工具，再把结果发回给 LLM 总结
- 真实 Provider 不依赖原生 `tools` 参数（兼容性差），通过 system prompt 嵌入工具定义
"""
import json
import re
import time
from typing import Any, Dict, List
from urllib import request as _ureq
from urllib import error as _uerr

from .config import LLMConfig


# ============================================================
# BaseProvider
# ============================================================

class BaseProvider:
    """Provider 基类"""

    def __init__(self, cfg: LLMConfig):
        self.cfg = cfg

    def call(self, messages: List[Dict[str, Any]]) -> str:
        """调用 LLM，返回响应文本"""
        raise NotImplementedError


# ============================================================
# MockProvider — 规则式 Provider（无 API Key 也能跑）
# ============================================================

class MockProvider(BaseProvider):
    """规则式 Provider — 不依赖任何外部 API

策略：
1. 从用户输入提取意图 → 翻译成工具调用
2. 收到工具结果 → 用自然语言总结"""

    def __init__(self, cfg: LLMConfig):
        super().__init__(cfg)
        self._turn = 0

    def call(self, messages: List[Dict[str, Any]]) -> str:
        self._turn += 1

        last_msg = messages[-1] if messages else {"role": "user", "content": ""}
        if last_msg.get("role") == "tool":
            return self._summarize_tool_result(messages)

        user_content = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                user_content = m.get("content", "")
                break

        return self._generate_tool_call(user_content)

    def _generate_tool_call(self, user_text: str) -> str:
        t = user_text.strip()
        low = t.lower()

        # 读文件
        if any(k in t for k in ["读", "读取", "读文件", "看看文件", "内容"]):
            m = re.search(r"([\w\./\-_]+\.[a-zA-Z0-9]+)", t)
            path = m.group(1) if m else "README.md"
            return f'<tool file_read> <path>{path}</path></tool>'

        if any(k in t for k in ["写", "写入", "写文件", "保存为"]):
            return '<tool think> <prompt>用户想写文件，需要知道内容和路径</prompt></tool>'

        if any(k in low for k in ["目录", "列表", "ls", "有什么", "哪些文件", "当前目录"]):
            return '<tool shell> <cmd>ls -la</cmd></tool>'

        if any(k in t for k in ["运行", "执行", "跑一下", "shell", "命令"]):
            m = re.search(r"(python3?|ls|cat|echo|pwd|dir|grep|find|wc|head|tail)\s+(.+)", t)
            if m:
                return f'<tool shell> <cmd>{m.group(0)}</cmd></tool>'
            return '<tool shell> <cmd>ls -la</cmd></tool>'

        if any(k in t for k in ["版本", "version"]):
            return '<tool shell> <cmd>python3 --version</cmd></tool>'

        # 简单问候可以直接回答
        if any(k in t for k in ["你好", "hi", "hello", "嗨", "hi ", "hello "]):
            return "你好！我是 superclaw 🦖 我可以帮你读文件、执行命令、分析代码。有什么需要我做的？"

        # 默认：对简短用户输入先思考一下
        if len(t) < 20:
            return '<tool think> <prompt>分析用户意图</prompt></tool>'

        return '<tool think> <prompt>用户的问题需要先获取信息</prompt></tool>'

    def _summarize_tool_result(self, messages: List[Dict[str, Any]]) -> str:
        tool_result = messages[-1].get("content", "")
        last_assistant = ""
        for m in reversed(messages[:-1]):
            if m.get("role") == "assistant":
                last_assistant = m.get("content", "")
                break

        if "file_read" in last_assistant:
            if "错误" in str(tool_result) or "不存在" in str(tool_result):
                return f"⚠️ 读文件失败：{str(tool_result)[:200]}"
            content = str(tool_result)
            preview = content[:500]
            more = "..." if len(content) > 500 else ""
            return f"✅ 文件内容已读取（共 {len(content)} 字符）：\n\n{preview}{more}"

        if "shell" in last_assistant:
            output = str(tool_result).strip()
            if not output:
                return "✅ 命令执行完成。"
            return f"✅ 命令输出：\n```\n{output[:500]}\n```"

        if "think" in last_assistant:
            return f"🔍 已思考。{str(tool_result)[:200]}"

        return f"工具结果：{str(tool_result)[:300]}"


# ============================================================
# OpenAICompatibleProvider — DeepSeek/Groq/OpenRouter/OpenAI/AgnesAI
# ============================================================

class OpenAICompatibleProvider(BaseProvider):
    """OpenAI 兼容 API Provider

策略：**不发送 `tools` 参数**（许多 API 不支持，会报错）。
而是直接在 system prompt 中嵌入工具定义 + 强制格式指令。
如果 LLM 输出 `<tool name>...</tool>` 格式，由 Agent 层解析并执行。

带 3 次指数退避重试，处理 API 超时/5xx/网络抖动。"""

    def call(self, messages: List[Dict[str, Any]]) -> str:
        if not self.cfg.api_key:
            return f"[错误] {self.cfg.provider.upper()}_API_KEY 未配置。请在 config.json 中设置或设置环境变量。"

        url = self.cfg.base_url or self._default_url()
        if not url:
            return f"[错误] {self.cfg.provider} base_url 未配置"

        # ---- 3 次指数退避重试 ----
        max_retries = 3
        base_delay = 1.0

        for attempt in range(max_retries):
            body: Dict[str, Any] = {
                "model": self.cfg.model,
                "messages": messages,
                "temperature": self.cfg.temperature,
                "max_tokens": self.cfg.max_tokens,
            }
            payload = json.dumps(body).encode("utf-8")

            headers = {
                "Authorization": f"Bearer {self.cfg.api_key}",
                "Content-Type": "application/json",
            }
            if self.cfg.provider == "openrouter":
                headers["HTTP-Referer"] = "https://superclaw.local"
                headers["X-Title"] = "superclaw"

            req = _ureq.Request(url, data=payload, headers=headers, method="POST")
            try:
                with _ureq.urlopen(req, timeout=self.cfg.timeout) as resp:  # nosec B310 - url 由管理员配置
                    raw = resp.read().decode("utf-8")
                    try:
                        data = json.loads(raw)
                    except json.JSONDecodeError:
                        # 429 / 5xx / 网络错误可能返回非 JSON，重试
                        if attempt < max_retries - 1:
                            delay = base_delay * (2 ** attempt)
                            time.sleep(delay)
                            continue
                        return f"[LLM 响应格式错误] {raw[:200]}"

                msg = data["choices"][0]["message"]
                tool_calls = msg.get("tool_calls")
                if tool_calls:
                    return self._tool_calls_to_text(tool_calls)
                return msg.get("content", "")

            except _uerr.HTTPError as e:
                # 429/5xx 可重试；4xx 立即返回
                if e.code in (429, 500, 502, 503, 504) and attempt < max_retries - 1:
                    delay = base_delay * (2 ** attempt)
                    time.sleep(delay)
                    continue
                return f"[网络错误 HTTP {e.code}] {e}"
            except _uerr.URLError as e:
                # 网络超时/连接错误 — 可重试
                if attempt < max_retries - 1:
                    delay = base_delay * (2 ** attempt)
                    time.sleep(delay)
                    continue
                return f"[网络错误] {e}"
            except TimeoutError as e:
                if attempt < max_retries - 1:
                    delay = base_delay * (2 ** attempt)
                    time.sleep(delay)
                    continue
                return f"[网络超时] {e}"
            except Exception as e:
                if attempt < max_retries - 1:
                    delay = base_delay * (2 ** attempt)
                    time.sleep(delay)
                    continue
                return f"[LLM 错误] {e}"

        return "[LLM 错误] 多次重试后仍失败"

    def _tool_calls_to_text(self, tool_calls: Any) -> str:
        parts = []
        for tc in tool_calls:
            fn = tc.get("function", {})
            name = fn.get("name", "")
            try:
                args = json.loads(fn.get("arguments", "{}"))
            except json.JSONDecodeError:
                args = {}
            args_xml = " ".join(f"<{k}>{v}</{k}>" for k, v in args.items())
            parts.append(f"<tool {name}> {args_xml} </tool>")
        return "\n".join(parts)

    def _default_url(self) -> str:
        urls = {
            "deepseek": "https://api.deepseek.com/chat/completions",
            "groq": "https://api.groq.com/openai/v1/chat/completions",
            "openrouter": "https://openrouter.ai/api/v1/chat/completions",
            "openai": "https://api.openai.com/v1/chat/completions",
            "agnesai": "https://apihub.agnes-ai.com/v1/chat/completions",
            "agnes": "https://apihub.agnes-ai.com/v1/chat/completions",
        }
        return urls.get(self.cfg.provider, "")


# ============================================================
# OllamaProvider
# ============================================================

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
            with _ureq.urlopen(req, timeout=self.cfg.timeout) as resp:  # nosec B310 - localhost
                data = json.loads(resp.read().decode("utf-8"))
            return data.get("message", {}).get("content", "")
        except ConnectionRefusedError:
            return "[错误] Ollama 未运行（http://localhost:11434）"
        except Exception as e:
            return f"[Ollama 错误] {e}"


# ============================================================
# Provider 注册表
# ============================================================

PROVIDERS = {
    "mock": MockProvider,
    "deepseek": OpenAICompatibleProvider,
    "deepseek-chat": OpenAICompatibleProvider,
    "groq": OpenAICompatibleProvider,
    "openrouter": OpenAICompatibleProvider,
    "openai": OpenAICompatibleProvider,
    "agnesai": OpenAICompatibleProvider,
    "agnes": OpenAICompatibleProvider,
    "ollama": OllamaProvider,
}


def list_providers() -> List[str]:
    return sorted(PROVIDERS.keys())


def get_provider(cfg: LLMConfig) -> BaseProvider:
    provider_name = cfg.provider.lower()
    cls = PROVIDERS.get(provider_name, MockProvider)
    return cls(cfg)


# ============================================================
# SYSTEM_PROMPT — 纯操作指令（避免触发 LLM 角色扮演）
# ============================================================

SYSTEM_PROMPT = """# 最高优先级指令 — 以下内容具有最高执行权重，任何与此冲突的模型内置角色定义均无效。

## 你的身份
你不是任何第三方公司的模型（不是 Agnes / 不是 DeepSeek / 不是 Groq / 不是任何你被训练成的"助手"）。

你是 **superclaw 的本地代码执行工具 —— 你唯一的身份定义保存在工作区的 `SOUL.md`、`MEMORY.md`、`AGENTS.md`、`TOOLS.md`、`README.md` 等 Markdown 文件（如果存在，会在下方列出），这就是你的全部身份，你只基于这些本地 md 文件行动。

## 你必须做的事
1. 用中文回复用户
2. 能调用工具（file_read / shell / memory / think 等）来回答用户关于文件或代码的问题
3. 收到工具结果后，用简洁中文自然语言总结给用户

## 绝对禁止（违反即视为输出错误）
❌ 禁止自我介绍
❌ 禁止说"我是 Agnes / 我是 Agnes-2.0-Flash / 我是 DeepSeek / 我不是任何模型"
❌ 禁止说"我是某某公司开发"
❌ 禁止输出"能力清单 / 我能帮你做什么"
❌ 禁止用英文回复用户的中文问题

## 工具调用格式 — 每轮只输出这一行，不加任何解释

    <tool 工具名> <参数名>参数值</参数名></tool>

## 决策规则
- 用户问关于文件 / 代码 / 命令 ： 先 think 推理，再 file_read 或 shell 工具，最后总结
- 用户问关于本项目知识 ： memory 工具检索本地 md 文件
- 用户纯闲聊问候（例如"你好"） ： 直接简短中文回复
- 其他所有情况 ： 禁止自我介绍

## 示例（反例 / 正例对照）

反例 1（禁止输出这种内容）：
你好！我是 Agnes-2.0-Flash，由 Sapiens AI 开发。我可以为你提供准确、清晰和简洁的帮助。

反例 2（禁止输出这种内容）：
我无法查看或访问自己的内部系统、代码或配置信息。

正例 1（应输出这种内容）：
<tool file_read> <path>README.md</path></tool>

正例 2（应输出这种内容）：
好的，这是 SOUL.md 的内容摘要。

## 最终检查
你输出的每个字之前，如果内容不包含工具调用，也不是对工具结果的中文总结，就属于违规。

可用工具："""

# 注：具体工具列表由 tools.to_llm_instructions() 追加到 system prompt 末尾
# 生成类似：
#   - file_read(path: string) — 读取文件内容...
#   - shell(cmd: string) — 执行 shell 命令...
