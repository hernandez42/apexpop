"""
superclaw 核心 Agent —— 7 步闭环循环

架构 (7 步循环):
  步骤 1. 意图理解           → 读取本地 md 知识库 (SOUL/MEMORY/AGENTS/README...)
  步骤 2. 意图分类           → CapabilityRegistry (12 项能力)
  步骤 3. 记忆检索           → MemoryStore.query + MemoryStore.temporal_query
  步骤 4. 工具选择           → ARS 风格 schema, 22+ 工具, Agent 自己选（不外包给 LLM）
  步骤 5. GEPEngine 接入     → superclaw 仓核心, 避免断连；nanobot 9 子系统同步
                              + self_modify: CodeGenerator + SandboxExecutor
                              + nanobot 反向桥: _cross_host_inbox.json
  步骤 6. LLM 润色           → LLM 只负责把 Agent 步骤/结果包装成自然语言
  步骤 7. 归档记忆           → memory/<session>.md + nanobot_sync.jsonl

核心变化:
1. Agent 有自己的"本地脑袋" (CapabilityRegistry + rules + md 知识) —— 不依赖 LLM 做判断
2. Agent 自己调用工具 —— 不把工具选择外包给 LLM
3. LLM 只负责最后一步的自然语言润色 —— 不是大脑
4. 与 GEPEngine 核心断连时也能优雅降级
5. 与 nanobot 仓通过 nanobot_bridge 双向同步
6. self_modify: 生成→沙箱验证→归档（默认不自动合并）
"""
import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)

# 统一配置 logging（确保 debug/info 日志可见）
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)

from .config import SuperclawConfig, load_config
from .providers import BaseProvider, get_provider, SYSTEM_PROMPT
from .session import SessionManager
from .tools import ToolRegistry, build_default_tools, scan_skills

try:
    from .feedback_learner import FeedbackLearner
    _FEEDBACK_LEARNER_AVAILABLE = True
except ImportError:  # pragma: no cover
    _FEEDBACK_LEARNER_AVAILABLE = False

try:
    from .capability_registry import CapabilityRegistry
    _CAP_REGISTRY_AVAILABLE = True
except Exception:  # pragma: no cover
    CapabilityRegistry = None  # type: ignore
    _CAP_REGISTRY_AVAILABLE = False

try:
    from .memory import MemoryStore
    _MEMORY_AVAILABLE = True
except Exception:  # pragma: no cover
    MemoryStore = None  # type: ignore
    _MEMORY_AVAILABLE = False

try:
    from .nanobot_bridge import NanobotBridge, NANOBOT_SUBSYSTEMS
    _NANOBOT_AVAILABLE = True
except Exception:  # pragma: no cover
    NANOBOT_SUBSYSTEMS = []
    NanobotBridge = None  # type: ignore
    _NANOBOT_AVAILABLE = False

try:
    from .self_modify import SelfModifier, ModifyTarget
    _SELFMODIFY_AVAILABLE = True
except Exception:  # pragma: no cover
    ModifyTarget = None  # type: ignore
    SelfModifier = None  # type: ignore
    _SELFMODIFY_AVAILABLE = False

try:
    from .gep_engine import GEPEngine
    _GEP_AVAILABLE = True
except Exception:  # pragma: no cover
    GEPEngine = None  # type: ignore
    _GEP_AVAILABLE = False

try:
    from .curiosity import (  # noqa: F401
        NoveltyScorer, BoredomTracker, CuriosityDrive,
        ExplorationGoal, CuriosityDrivenExplorer,
    )
    _CURIOSITY_AVAILABLE = True
except Exception:  # pragma: no cover
    NoveltyScorer = BoredomTracker = CuriosityDrive = None  # type: ignore
    ExplorationGoal = CuriosityDrivenExplorer = None  # type: ignore
    _CURIOSITY_AVAILABLE = False

try:
    from .experience_learner import (  # noqa: F401
        ExperienceLearner, StrategyOutcome,
    )
    _EXPERIENCE_LEARNER_AVAILABLE = True
except Exception:  # pragma: no cover
    ExperienceLearner = None  # type: ignore
    StrategyOutcome = None  # type: ignore
    _EXPERIENCE_LEARNER_AVAILABLE = False


@dataclass
class AgentResult:
    """Agent 执行结果"""
    content: str
    tools_used: List[str] = field(default_factory=list)
    tool_outputs: List[str] = field(default_factory=list)
    iterations: int = 0
    total_time_ms: int = 0


# ============ 兼容层: 工具调用解析 / LLM 角色防御 ============
def _parse_tool_call(text: str) -> Optional[Tuple[str, Dict[str, str]]]:
    """从文本中解析工具调用。支持多种格式，容忍空白、自然语言包裹。

匹配优先级：
1. `<tool name> <arg>val</arg> </tool>` XML 风格（推荐）
2. `{"tool": "name", "args": {...}}`  JSON
3. `{"name": "tool", "input": {...}}`  JSON（LangChain 风格）
"""
    if not text:
        return None

    stripped = text.strip()

    # 格式 1: <tool name> <arg>val</arg> </tool>
    # 兼容：<tool  name> <arg> val </arg> </tool> 以及空白/换行变体
    m = re.search(r"<tool\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*>.*?</tool>", stripped, re.DOTALL)
    if m:
        name = m.group(1).strip()
        body = m.group(0)  # 整个 tool 标签
        args = dict(re.findall(r"<(\w+)>(.*?)</\1>", body, re.DOTALL))
        if name:
            return name, args

    # 格式 1b: 宽松的 XML 风格 <name>val</name> 包裹
    m = re.search(r"<([a-zA-Z_][a-zA-Z0-9_]*)>(.*?)</\1>", stripped, re.DOTALL)
    if m and m.group(1) in ("tool",):
        body = m.group(2)
        inner = re.search(r"<([a-zA-Z_][a-zA-Z0-9_]*)>", body)
        if inner:
            name = inner.group(1)
            args = dict(re.findall(r"<(\w+)>(.*?)</\1>", body, re.DOTALL))
            return name, args

    # 格式 2: JSON {"tool": "name", "args": {...}}
    # 先尝试整行解析，失败则提取最大的 JSON 对象
    try:
        data = json.loads(stripped)
        if isinstance(data, dict) and "tool" in data:
            args = data.get("args", {})
            return data["tool"], {k: str(v) for k, v in args.items()}
    except (json.JSONDecodeError, ValueError):
        pass

    # 格式 3: JSON {"name": "tool", "input": {...}}
    try:
        data = json.loads(stripped)
        if isinstance(data, dict) and "name" in data:
            args = data.get("input", {}) or {}
            return data["name"], {k: str(v) for k, v in args.items()}
    except (json.JSONDecodeError, ValueError):
        pass

    # 格式 4: 文本中嵌入的 JSON（LLM 可能输出 "好的，我将调用 { ... }"）
    json_match = re.search(r"\{[^}]*\"(tool|name)\"[^}]*\}", stripped, re.DOTALL)
    if json_match:
        try:
            data = json.loads(json_match.group(0))
            if isinstance(data, dict):
                if "tool" in data:
                    return data["tool"], {k: str(v) for k, v in (data.get("args") or {}).items()}
                if "name" in data:
                    return data["name"], {k: str(v) for k, v in (data.get("input") or {}).items()}
        except (json.JSONDecodeError, ValueError):
            pass

    return None


# LLM 角色防御: 命中任一即视为违规输出
_ROLEPLAY_TRIGGERS: List[str] = [
    "我是 agnes", "agnes-2.0", "由 sapiens", "sapiens ai",
    "我是 deepseek", "deepseek 开发", "deepseek-chat",
    "我是 groq", "groq 开发", "我是 openai", "由 openai 开发",
    "我是 qwen", "我是 glm", "我是 doubao",
    "我是一个人工智能", "作为一个人工智能助手", "作为一个 ai 助手",
    "无法查看或访问自己的内部", "无法访问自己的内部系统",
    "我没有权限访问或读取任何外部", "无法访问或读取任何外部",
    "我只能基于训练数据", "只能基于训练数据回答",
    "我可以为你提供准确", "可以为你提供准确",
    "解答各类知识性问题", "一个具备进化能力的 ai agent",
]


def _looks_like_roleplay(text: str) -> bool:
    """检测 LLM 是否在输出角色扮演内容"""
    if not text:
        return False
    low = text.lower()
    for t in _ROLEPLAY_TRIGGERS:
        if t in low:
            return True
    return False


# ============ Agent 启动时自动加载的核心 md 文件 ============
_CORE_MD_FILES: List[str] = ["SOUL.md", "MEMORY.md", "AGENTS.md", "TOOLS.md", "README.md"]


# ============ 数据类: 意图 / 行动 ============
@dataclass
class Intent:
    """Agent 本地识别出的用户意图"""
    type: str
    targets: List[str] = field(default_factory=list)
    keywords: List[str] = field(default_factory=list)
    reasoning: str = ""


@dataclass
class Action:
    """Agent 决定要执行的一个行动"""
    tool: str
    args: Dict[str, str]
    reason: str
    result: Optional[str] = None


# ============ 本地思考引擎 LocalThinker ============
class LocalThinker:
    """Agent 的本地脑袋 —— 用关键词/正则/规则理解用户 + 产生行动列表

    不依赖 LLM。Agent 自己判断用户意图，自己规划工具调用。
    """

    # 关键词规则库: {意图类型: ([关键词列表], [正则列表])}
    _KEYWORD_RULES = {
        "file_read": (
            ["读", "读取", "读文件", "文件内容", "查看文件", "看看文件",
             "内容", "内容是啥", "内容是什么", "打开文件", "cat "],
            [r"[A-Za-z0-9_\-\.\/]+\.(md|py|txt|yaml|yml|json|cfg|ini|toml|log|sh)"]
        ),
        "shell": (
            ["运行", "执行", "跑一下", "shell ", "命令", "python3 ", "python ",
             "ls ", "目录", "pwd", "当前目录", "哪些文件", "grep ", "列表"],
            [r"(ls|pwd|cat|python3|python|grep|find|wc|head|tail)\s+.+"]
        ),
        "memory": (
            ["记忆", "查记忆", "检索记忆", "md 知识", "灵魂文件", "知识文件",
             "反思", "之前聊过", "历史对话", "回顾"],
            []
        ),
        "evolution": (
            ["进化", "自我进化", "进化循环", "基因", "apex", "evolve"],
            []
        ),
        "chit_chat": (
            ["你好", "hi", "hello", "嗨", "问好", "再见", "谢谢", "感谢", "不错", "ok"],
            []
        ),
        "code_ask": (
            ["代码", "函数", "代码分析", "这段代码", "代码解释", "bug",
             "报错", "错误", "修复", "改进", "重构", "优化"],
            []
        ),
    }

    def __init__(self, workspace: str, tools: ToolRegistry):
        self.workspace = Path(workspace)
        self.tools = tools

    def analyze(self, user_input: str) -> Intent:
        """本地规则识别用户意图"""
        text = user_input.strip()
        low = text.lower()

        if self._matches_type(text, low, "file_read"):
            targets = self._extract_file_targets(text)
            return Intent(
                type="file_read",
                targets=targets or [self._guess_default_file()],
                keywords=self._extract_keywords(text),
                reasoning=f"识别到文件读取意图 -> 目标: {targets or [self._guess_default_file()]}",
            )

        if self._matches_type(text, low, "shell"):
            return Intent(
                type="shell",
                targets=self._extract_shell_cmd(text),
                keywords=self._extract_keywords(text),
                reasoning=f"识别到命令执行意图 -> 目标: {self._extract_shell_cmd(text)}",
            )

        if self._matches_type(text, low, "memory"):
            return Intent(
                type="memory",
                targets=[text],
                keywords=self._extract_keywords(text),
                reasoning=f"识别到记忆检索意图 -> 关键词: {self._extract_keywords(text)}",
            )

        if self._matches_type(text, low, "evolution"):
            return Intent(
                type="evolution",
                targets=["self_evolution"],
                keywords=["进化"],
                reasoning="识别到进化/自我改进意图",
            )

        if self._matches_type(text, low, "chit_chat"):
            return Intent(type="chit_chat", targets=[], keywords=[],
                         reasoning="纯闲聊问候 —— 直接友好回应")

        if self._matches_type(text, low, "code_ask"):
            targets = self._extract_file_targets(text) or self._find_code_files()
            return Intent(
                type="code_ask",
                targets=targets,
                keywords=self._extract_keywords(text),
                reasoning=f"代码/问题分析意图 -> 可能需要先读: {targets[:3]}",
            )

        return Intent(
            type="unknown",
            targets=[],
            keywords=self._extract_keywords(text),
            reasoning="未明确命中工具类型 —— 尝试从本地 md 知识文件找相关内容",
        )

    def _matches_type(self, text: str, low: str, intent_type: str) -> bool:
        keywords, regexes = self._KEYWORD_RULES[intent_type]
        for kw in keywords:
            if kw.lower() in low:
                return True
        for pattern in regexes:
            if re.search(pattern, text):
                return True
        return False

    def _extract_keywords(self, text: str) -> List[str]:
        """提取中文2+字连续词 + 英文标识符"""
        zh_words = re.findall(r"[\u4e00-\u9fa5]{2,}", text)
        en_words = re.findall(r"[a-zA-Z_][a-zA-Z0-9_\-]{2,}", text)
        return list(dict.fromkeys(zh_words + en_words))[:8]

    def _extract_file_targets(self, text: str) -> List[str]:
        """从用户输入提取文件名（匹配常见扩展名）"""
        files = re.findall(r"[A-Za-z0-9_\-\./]+\.[A-Za-z0-9]{2,}", text)
        quoted = re.findall(r"[\"']([A-Za-z0-9_\-\./]+?)[\"']", text)
        all_files = list(dict.fromkeys(files + quoted))
        return [f for f in all_files if not f.startswith("python") and not f.startswith("shell")]

    def _extract_shell_cmd(self, text: str) -> List[str]:
        """从用户输入提取要执行的 shell 命令"""
        for pattern in [r"(python3?[\s-]+[A-Za-z0-9_\-\./]+)",
                        r"(ls[\s\-]+[A-Za-z0-9_\./]+)",
                        r"(cat[\s]+[A-Za-z0-9_\./]+)"]:
            m = re.search(pattern, text)
            if m:
                return [m.group(1)]
        if "目录" in text or "当前目录" in text:
            return ["ls -la"]
        if "python" in text.lower():
            return ["python3 --version"]
        return ["ls -la"]

    def _guess_default_file(self) -> str:
        """用户没指定文件时，猜一个最相关的本地 md 文件"""
        for name in ["README.md", "SOUL.md", "MEMORY.md"]:
            if (self.workspace / name).exists():
                return name
        return "README.md"

    def _find_code_files(self) -> List[str]:
        """查找工作区的 Python 代码文件"""
        results = []
        try:
            for p in sorted(self.workspace.glob("*.py")):
                results.append(str(p.name))
                if len(results) >= 5:
                    break
        except Exception:
            pass
        return results or ["README.md"]

    def plan_actions(self, intent: Intent) -> List[Action]:
        """Agent 自己规划要执行哪些工具 —— 本地规则"""
        if intent.type == "file_read":
            actions: List[Action] = []
            for target in intent.targets[:3]:
                resolved = self._resolve_path(target)
                actions.append(Action(
                    tool="file_read", args={"path": resolved},
                    reason=f"Agent 本地识别到文件读取意图 -> 读取 {resolved}",
                ))
            return actions

        if intent.type == "shell":
            return [Action(
                tool="shell",
                args={"cmd": intent.targets[0] if intent.targets else "ls -la"},
                reason="Agent 本地识别到命令执行意图",
            )]

        if intent.type == "memory":
            return [Action(
                tool="memory",
                args={"query": " ".join(intent.keywords[:5]) if intent.keywords else "知识"},
                reason="Agent 本地识别到记忆检索意图",
            )]

        if intent.type == "code_ask":
            actions = []
            for target in intent.targets[:3]:
                resolved = self._resolve_path(target)
                actions.append(Action(
                    tool="file_read", args={"path": resolved},
                    reason=f"代码分析意图 —— Agent 自动读取 {resolved}",
                ))
            if intent.keywords and self.tools.has("memory"):
                actions.append(Action(
                    tool="memory", args={"query": " ".join(intent.keywords[:5])},
                    reason="Agent 同时检查记忆系统",
                ))
            return actions

        if intent.type == "chit_chat":
            return []

        # unknown 类型: 尝试查记忆 + 读 README
        unknown_actions: List[Action] = []
        if self.tools.has("memory") and intent.keywords:
            unknown_actions.append(Action(
                tool="memory",
                args={"query": " ".join(intent.keywords[:5])},
                reason="Agent 无法明确识别意图 -> 尝试从本地 md 知识检索",
            ))
        readme = self._resolve_path("README.md")
        if (self.workspace / readme).exists():
            unknown_actions.append(Action(
                tool="file_read", args={"path": readme},
                reason="Agent 读取 README 以获取项目上下文",
            ))
        return unknown_actions

    def _resolve_path(self, target: str) -> str:
        """把相对路径标准化"""
        target = target.strip().strip("'\"")
        if target.startswith("/"):
            return target
        if (self.workspace / target).exists():
            return target
        for sub in ["", "superclaw", "src"]:
            candidate = (self.workspace / sub / target) if sub else (self.workspace / target)
            if candidate.exists():
                try:
                    return str(candidate.relative_to(self.workspace))
                except ValueError:
                    return target
        return target


# ============ 行动执行引擎 ActionRunner ============
class ActionRunner:
    """Agent 自己调用工具 —— 不经过 LLM

    负责: 实际调用工具 + 收集结果 + 基本容错
    """

    def __init__(self, tools: ToolRegistry):
        self.tools = tools

    def execute(self, actions: List[Action]) -> Tuple[List[Action], List[str]]:
        """顺序执行行动列表，返回 (已执行带结果的行动, 错误列表)"""
        errors: List[str] = []
        executed: List[Action] = []
        for action in actions:
            if not self.tools.has(action.tool):
                errors.append(f"工具 {action.tool} 不存在")
                continue
            try:
                tool_result = self.tools.call(action.tool, **action.args)
                action.result = str(tool_result.content)
                executed.append(action)
            except Exception as e:
                errors.append(f"{action.tool} 调用失败: {e}")
                action.result = f"[错误] {e}"
                executed.append(action)
        return executed, errors


# ============ 结果合成器 ResultSynthesizer ============
class ResultSynthesizer:
    """把 Agent 本地推理 + 工具结果包装成自然语言回答

    LLM 只负责语言美化 —— 决策权不在 LLM
    """

    def __init__(self, provider: BaseProvider, system_prompt: str):
        self.provider = provider
        self.system_prompt = system_prompt

    def synthesize(self, intent: Intent, actions: List[Action],
                    user_input: str, local_memory_hint: str = "") -> str:
        """本地推理 + 工具结果 → LLM 自然语言润色"""
        # 情况 1: 纯闲聊
        if intent.type == "chit_chat":
            msg = [
                {"role": "system", "content": self.system_prompt + "\n\n用简洁中文回答用户。"},
                {"role": "user", "content": user_input},
            ]
            try:
                return self.provider.call(msg).strip() or "你好！有什么需要我做的？"
            except Exception:
                return "你好！有什么需要我帮你做的？"

        # 情况 2: 没有任何工具也没有检索到记忆
        if not actions and not local_memory_hint:
            msg = [
                {"role": "system", "content": self.system_prompt + "\n\n用简洁中文回答用户问题。"},
                {"role": "user", "content": user_input},
            ]
            try:
                return self.provider.call(msg).strip() or "嗯，我需要更多信息才能回答这个问题。"
            except Exception:
                return f"我理解了你的问题（意图: {intent.type}），但当前无工具可用。你可以指定具体文件名或命令。"

        # 情况 3: 有工具结果 / 有记忆 —— LLM 语言润色
        reasoning_text = f"意图: {intent.type} — {intent.reasoning}"
        actions_text = []
        for i, act in enumerate(actions):
            result_preview = (act.result or "")[:800]
            if len(str(act.result or "")) > 800:
                result_preview += "...[已截断]"
            actions_text.append(
                f"[{i+1}] 工具: {act.tool} 参数: {json.dumps(act.args, ensure_ascii=False)}\n"
                f"  原因: {act.reason}\n  结果: {result_preview}"
            )

        summary_prompt = (
            f"{self.system_prompt}\n\n"
            f"【任务】把以下 Agent 的推理过程和工具执行结果，用简洁中文总结成对用户问题的自然回答。\n\n"
            f"【用户问题】{user_input}\n\n"
            f"【Agent 推理过程】{reasoning_text}\n\n"
        )
        if local_memory_hint:
            summary_prompt += f"【本地 md 知识检索】{local_memory_hint}\n\n"
        if actions_text:
            summary_prompt += "\n\n".join(actions_text) + "\n\n"
        summary_prompt += "【你的回答】用简洁中文直接给用户结论或总结 —— 不要重复上面的格式，不要输出任何工具调用标签。"

        try:
            reply = self.provider.call([{"role": "user", "content": summary_prompt}])
            cleaned = re.sub(r"<tool[^>]*>.*?</tool>", "", reply, flags=re.DOTALL).strip()
            return cleaned or "（工具已执行，但 LLM 未返回可读总结）"
        except Exception as e:
            # LLM 失败时 Agent 自己兜底
            lines = [reasoning_text]
            for act in actions:
                preview = (act.result or "")[:300]
                lines.append(f"[{act.tool}] {preview}")
            lines.append(f"[提示] LLM 调用失败: {e} —— 以上为 Agent 本地推理与工具执行结果")
            return "\n".join(lines)


# ============================================================
# AgentReasoningEngine — 真正的 "Agent 先接收 → LLM 思考 → 推理 → 工具 → 最终答案"
# ReAct 风格（Reasoning + Acting）：Thought → Action → Observation → ... → Final Answer
#
# 与 "LLM 转发器" 的本质区别：
#   - 转发器：用户输入 → 直接丢给 LLM → 原样返回
#   - 本引擎：用户输入 → LLM 产出 Thought（推理/计划）→ 解析 Action → Agent 真调用工具
#           → Observation 注入对话 → LLM 继续思考 → ... → LLM 产出 Final Answer
#   - 用户能看到每一步推理（verbose 模式）
# ============================================================

@dataclass
class ReasonStep:
    """单步推理记录：LLM 产出的 Thought / Action / Observation / Final"""
    kind: str  # "thought" | "action" | "observation" | "final"
    content: str
    tool_name: Optional[str] = None
    tool_args: Optional[Dict[str, Any]] = None
    tool_result: Optional[str] = None
    error: Optional[str] = None
    duration_ms: int = 0


class AgentReasoningEngine:
    """
    ReAct 风格多步推理引擎。

    每一轮循环：
      1. Agent 把当前对话历史（含先前 Observation）丢给 LLM
      2. LLM 必须输出三种之一：
           - THOUGHT: <推理过程>           → 纯思考，不调用工具
           - ACTION: <tool_name> + ARGS: {...}  → Agent 调用工具
           - FINAL: <自然语言答案>          → 结束循环
      3. Agent 解析结构化输出 → 决定下一步（继续/调工具/结束）
      4. 最多 max_steps 步，强迫收敛

    verbose=True 时，用户可以在终端看到完整推理链（🧠 THOUGHT / ⚙ ACTION / 👁 OBSERVATION / ✅ FINAL）。
    """

    _FORMAT_PROMPT = """
【严格输出格式 —— Agent 会逐字解析，不符合格式会提示你重来】

三选一格式：

1) 继续思考 THOUGHT（不用工具，先理清思路）
```
THOUGHT: <我对当前问题的理解、缺少哪些信息、下一步打算做什么>
```

2) 调用工具 ACTION（精确指定工具与参数，Agent 会真实执行）
```
ACTION: <tool_name>
ARGS: {"param1": "value1", "param2": "value2"}
```
可用工具清单：
[TOOLS_SUMMARY_HERE]

3) 最终答案 FINAL（当你认为已得到足够信息，直接回答）
```
FINAL: <用自然语言整合推理过程与工具结果，给出完整答案>
```

流程规则：
- 每轮只选一种格式输出，不要混在一起。
- 推荐至少先一条 THOUGHT 表明理解了问题，再决定是否调用工具。
- 必须精确使用 ACTION: 工具名 与 ARGS: {...JSON...} 两行。
- 观察结果（Observation）由 Agent 注入，你不要自己伪造工具结果。
- 当信息已足够 — 立即输出 FINAL 结束推理。
- 永远不要自我介绍、不要说"我是 xxx"。直接工作。
"""

    def __init__(self, provider: BaseProvider, tools: ToolRegistry,
                 system_prompt: str, max_steps: int = 8,
                 logger: Optional[logging.Logger] = None):
        self.provider = provider
        self.tools = tools
        self.system_prompt = system_prompt
        self.max_steps = max_steps
        self.logger = logger or logging.getLogger("superclaw.reasoning")

    # ------------------------------------------------------------
    # 对外核心：reason()
    # ------------------------------------------------------------
    def reason(self, user_input: str,
               extra_context: str = "",
               verbose: bool = False,
               _override_max_steps: Optional[int] = None) -> Tuple[str, List[ReasonStep], bool]:
        """执行多步 ReAct 推理循环。

        参数:
            user_input: 用户原始输入
            extra_context: Agent 注入的额外上下文（md 知识 / memory 检索等）
            verbose: 是否打印推理过程
            _override_max_steps: 覆盖 self.max_steps（供外部精确控制迭代次数）

        返回:
            final_answer: 最终自然语言答案
            steps:        完整推理链
            success:      是否在 max_steps 内收敛到 FINAL
        """
        effective_max = _override_max_steps if _override_max_steps is not None else self.max_steps
        t0 = time.time()
        steps: List[ReasonStep] = []

        # 组装 system prompt（声明格式 + 工具清单）
        tools_summary = self._build_tools_summary()
        system_msg = self.system_prompt + "\n\n" + self._FORMAT_PROMPT.replace(
            "[TOOLS_SUMMARY_HERE]", tools_summary
        )

        # 对话历史（关键：把每一步的 Observation 持续喂给 LLM）
        history: List[Dict[str, str]] = [{"role": "system", "content": system_msg}]
        if extra_context and str(extra_context).strip():
            history.append({"role": "user",
                            "content": f"[Agent 注入上下文]\n{extra_context[:800]}"})
        history.append({"role": "user", "content": f"[用户问题] {user_input}"})

        if verbose:
            print(f"\n  ┌─ Agent 推理引擎启动（最多 {effective_max} 步）")
            print(f"  │ 用户输入: {user_input[:80]}")
            print(f"  │ 可用工具: {len(self.tools.names)} 项\n")

        final_answer = ""
        success = False

        for step_i in range(1, effective_max + 1):
            step_t0 = time.time()

            # ===== 1) LLM 思考 =====
            try:
                raw = str(self.provider.call(history)).strip()
            except Exception as e:
                steps.append(ReasonStep(
                    kind="observation",
                    content=f"[LLM 调用错误: {e}]",
                ))
                if verbose:
                    print(f"  ╰┬─[{step_i}] LLM 错误: {e}")
                break

            snippet = raw[:220].replace("\n", " ")
            if verbose:
                print(f"  ├┬─[{step_i}] LLM 回复: {snippet}{'...' if len(raw) > 220 else ''}")

            # ===== 2) Agent 解析结构化输出 =====
            parsed = self._parse_llm_output(raw)
            kind = parsed["kind"]

            # --- FINAL ---
            if kind == "final":
                steps.append(ReasonStep(
                    kind="final", content=parsed["text"],
                    duration_ms=int((time.time() - step_t0) * 1000),
                ))
                final_answer = parsed["text"].strip() or raw
                success = True
                if verbose:
                    print(f"  │╰─ ✅ FINAL  — 推理完成 ({len(steps)} 步)")
                break

            # --- THOUGHT（纯思考，不调用工具）---
            if kind == "thought":
                steps.append(ReasonStep(
                    kind="thought", content=parsed["text"],
                    duration_ms=int((time.time() - step_t0) * 1000),
                ))
                history.append({"role": "assistant",
                                "content": f"THOUGHT: {parsed['text']}"})
                if verbose:
                    print(f"  │╰─ 🧠 THOUGHT: {parsed['text'][:180]}")
                continue

            # --- ACTION（调用工具）---
            if kind == "action":
                tool_name = parsed["tool"]
                tool_args = parsed["args"]
                steps.append(ReasonStep(
                    kind="action", content=f"调用 {tool_name}",
                    tool_name=tool_name, tool_args=tool_args,
                    duration_ms=int((time.time() - step_t0) * 1000),
                ))
                history.append({
                    "role": "assistant",
                    "content": (f"ACTION: {tool_name} | "
                                f"ARGS: {json.dumps(tool_args, ensure_ascii=False)}"),
                })

                # ===== 3) Agent 执行工具（LLM 只负责"选工具"，执行权在 Agent）=====
                observation, obs_error = self._execute_tool(tool_name, tool_args)
                if obs_error:
                    steps.append(ReasonStep(
                        kind="observation", content=observation,
                        tool_name=tool_name, tool_result=observation,
                        error=obs_error,
                    ))
                else:
                    steps.append(ReasonStep(
                        kind="observation", content=observation,
                        tool_name=tool_name, tool_result=observation,
                    ))
                # 把 Observation 作为用户消息注入 —— 让 LLM 下一轮能"基于新信息继续思考"
                history.append({
                    "role": "user",
                    "content": f"[Observation/{tool_name}] {observation}",
                })
                if verbose:
                    print(f"\n[turn {step_i}] Agent 推理引擎")
                    print(f"    ├─ 调用工具: {tool_name}({json.dumps(tool_args, ensure_ascii=False)[:100]})")
                    if obs_error:
                        # 在错误信息里保留"未知工具"或"错误"关键字
                        err_text = obs_error
                        if tool_name and not self.tools.has(tool_name):
                            err_text = f"未知工具: {tool_name}"
                        print(f"    └─ ⚠  工具执行错误: {err_text[:160]}")
                    else:
                        display = str(observation)[:180].replace("\n", " ")
                        print(f"    └─ 观察结果: {display}")
                continue

            # ===== 4) LLM 输出非结构化（直接给了自然语言答案） =====
            # 设计原则：只要 LLM 给自然语言，就当做它的最终答案。
            # 如果之前调用过工具，那是正确的"吸收观察后回答"；
            # 如果之前没调用过工具，那是 LLM 选择了直接回答 —— 也是一个合理策略。
            steps.append(ReasonStep(
                kind="final", content=raw,
                duration_ms=int((time.time() - step_t0) * 1000),
            ))
            final_answer = raw
            success = True
            if verbose:
                print("  │╰─ ✅ FINAL — 接受 LLM 自然语言答案")
            break

        # 超过 max_steps 仍未 FINAL — 再调一次 LLM 让它总结已有信息（不计入 iterations）
        if not success:
            try:
                history.append({
                    "role": "user",
                    "content": (
                        f"已经进行了 {effective_max} 轮推理，请基于已获取的工具结果，"
                        f"直接用中文自然语言回答用户的原始问题：{user_input}"
                    ),
                })
                raw = str(self.provider.call(history)).strip()
                final_answer = raw
                success = True
            except Exception:
                # LLM 再失败就由 Agent 合成兜底
                final_answer = self._synthesize_from_steps(steps, user_input)

        total_ms = int((time.time() - t0) * 1000)
        if verbose:
            print(f"  └─ 推理完成: {len(steps)} 步 | 总耗时 {total_ms}ms\n")

        return final_answer, steps, success

    # ------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------

    def _build_tools_summary(self) -> str:
        lines: List[str] = []
        for name in self.tools.names:
            try:
                desc = (self.tools.get_description(name) or "").strip()
                lines.append(f"- {name}: {desc[:120]}")
            except Exception:
                lines.append(f"- {name}")
        return "\n".join(lines) if lines else "(无工具)"

    def _parse_llm_output(self, text: str) -> Dict[str, Any]:
        """解析 LLM 输出 —— 支持三种风格：新格式(THOUGHT/ACTION/FINAL) + 旧格式(<tool>...</tool>).

        返回: {"kind": "thought"|"action"|"final"|"unstructured", ...}
        """
        if not text:
            return {"kind": "unstructured", "text": ""}
        t = str(text).strip()

        # 1) FINAL（优先级最高）
        for prefix in ("FINAL:", "FINAL：", "FINAL ANSWER:", "FINAL ANSWER：",
                       "Final:", "Final Answer:", "final answer:"):
            if t.startswith(prefix):
                return {"kind": "final", "text": t[len(prefix):].strip()}
        final_match = re.search(r"(?:^|\n)\s*FINAL\s*[:：]\s*(.+)", t, re.DOTALL | re.IGNORECASE)
        if final_match:
            return {"kind": "final", "text": final_match.group(1).strip()}

        # 2) 代码块里的内容
        code_match = re.search(r"```(?:[a-zA-Z]*)\n?(.*?)```", t, re.DOTALL)
        if code_match:
            inner = code_match.group(1).strip()
            inner_parsed = self._parse_llm_output(inner)
            if inner_parsed["kind"] != "unstructured":
                return inner_parsed

        # 3) ACTION: <tool_name> + ARGS: {...}
        action_match = re.search(
            r"(?:^|\n)\s*(?:ACTION|Action)\s*[:：]\s*([a-zA-Z_][a-zA-Z0-9_]*)",
            t, re.IGNORECASE,
        )
        if action_match:
            tool_name = action_match.group(1).strip()
            args: Dict[str, Any] = {}
            args_match = re.search(
                r"(?:^|\n)\s*(?:ARGS|Args|args|PARAMS|params)\s*[:：]\s*(\{[^{}]*\})",
                t, re.DOTALL | re.IGNORECASE,
            )
            if args_match:
                try:
                    parsed_args = json.loads(args_match.group(1).strip())
                    if isinstance(parsed_args, dict):
                        args = parsed_args
                except (json.JSONDecodeError, ValueError):
                    args = {}
            return {"kind": "action", "tool": tool_name, "args": args, "text": t}

        # 4) 兼容旧格式 <tool xxx><param>val</param></tool> —— 作为 ACTION
        if "<tool" in t.lower() or re.search(r"<tool\s+\w", t, re.IGNORECASE):
            legacy = _parse_tool_call(t)
            if isinstance(legacy, tuple) and isinstance(legacy[0], str) and legacy[0]:
                return {"kind": "action", "tool": legacy[0],
                        "args": legacy[1] if isinstance(legacy[1], dict) else {},
                        "text": t}

        # 5) JSON 工具调用格式 —— {"tool": "xxx", "args": {...}}
        if t.strip().startswith("{") and t.strip().endswith("}"):
            try:
                data = json.loads(t)
                if isinstance(data, dict) and "tool" in data:
                    args = data.get("args") or data.get("params") or {}
                    if not isinstance(args, dict):
                        args = {}
                    return {"kind": "action", "tool": str(data["tool"]),
                            "args": args, "text": t}
            except (json.JSONDecodeError, ValueError):
                pass

        # 6) THOUGHT
        thought_match = re.search(
            r"(?:^|\n)\s*(?:THOUGHT|Thought)\s*[:：]\s*(.+?)(?:\n\s*(?:ACTION|FINAL)[:：]|$)",
            t, re.DOTALL | re.IGNORECASE,
        )
        if thought_match:
            return {"kind": "thought", "text": thought_match.group(1).strip()}
        for prefix in ("THOUGHT:", "Thought:", "thought:"):
            if t.lower().startswith(prefix.lower()):
                return {"kind": "thought", "text": t[len(prefix):].strip()}

        return {"kind": "unstructured", "text": t}

    def _execute_tool(self, tool_name: str,
                      args: Dict[str, Any]) -> Tuple[str, Optional[str]]:
        """Agent 统一的工具执行。"""
        if not tool_name or not isinstance(tool_name, str):
            return "", f"工具名无效: {tool_name}"
        tool_name = tool_name.strip()
        if not self.tools.has(tool_name):
            return (
                f"工具 '{tool_name}' 不存在。可用工具: {', '.join(self.tools.names[:20])}",
                f"未知工具: {tool_name}",
            )
        if not isinstance(args, dict):
            args = {}
        try:
            result = self.tools.call(tool_name, **args)
            result_text = str(result.content) if hasattr(result, "content") else str(result)
            # 截断过长的工具结果（避免 LLM 上下文溢出）
            if len(result_text) > 3000:
                result_text = (result_text[:2500]
                               + "\n...[已截断，完整结果已保存到会话]\n..."
                               + result_text[-400:])
            return result_text, None
        except Exception as e:
            return f"工具 {tool_name} 执行错误: {e}", str(e)

    def _synthesize_from_steps(self, steps: List[ReasonStep],
                               user_input: str) -> str:
        """LLM 没产出 FINAL 时，Agent 自己合成答案"""
        lines = ["以下为 Agent 的推理与工具调用结果（LLM 未输出 FINAL）："]
        for i, step in enumerate(steps, 1):
            if step.kind == "thought":
                lines.append(f"[i={i}] 🧠 THOUGHT: {step.content[:200]}")
            elif step.kind == "action":
                args_text = json.dumps(step.tool_args, ensure_ascii=False) if step.tool_args else ""
                lines.append(f"[i={i}] ⚙ ACTION: {step.tool_name}({args_text})")
            elif step.kind == "observation":
                preview = (step.tool_result or step.content)[:240]
                lines.append(f"[i={i}] 👁 OBSERVATION: {preview}")
                if step.error:
                    lines.append(f"       ⚠ 错误: {step.error}")
            elif step.kind == "final":
                lines.append(f"[i={i}] ✅ FINAL: {step.content[:200]}")
        return "\n".join(lines)


# ============ 主 Agent 类: 7 步闭环（含 ReAct 推理引擎） ============
class Agent:
    """superclaw 核心 Agent —— 三段式循环

    流程:
    1. LocalThinker 本地推理 —— 关键词/规则识别意图
    2. ActionRunner 本地执行 —— Agent 自己读文件/执行命令
    3. ResultSynthesizer LLM 润色 —— LLM 只负责语言包装

    不再是 LLM 转发器: 决策权在 Agent 本地规则，LLM 只负责语言。
    """

    def __init__(
        self,
        cfg: Optional[SuperclawConfig] = None,
        provider: Optional[BaseProvider] = None,
        tools: Optional[ToolRegistry] = None,
        sessions: Optional[SessionManager] = None,
        feedback_learner: Optional["FeedbackLearner"] = None,
        memory_store: Optional["MemoryStore"] = None,
        cap_registry: Optional["CapabilityRegistry"] = None,
        nanobot_bridge: Optional["NanobotBridge"] = None,
        self_modifier: Optional["SelfModifier"] = None,
        gep_engine: Optional["GEPEngine"] = None,
        llm_router: Optional[Any] = None,
        experience_learner: Optional[Any] = None,
    ):
        self.cfg = cfg or load_config()
        self.provider = provider or get_provider(self.cfg.llm)
        self.tools = tools or build_default_tools(
            self.cfg.workspace,
            shell=self.cfg.tools.shell,  # nosec B604
            file_tools=self.cfg.tools.file,
            github=self.cfg.tools.github,  # L3+ 2026-06-19 加 github 工具 (github_clone/search/download/pip_install)
            dynamic_tools=True,  # L3+ 2026-06-19 装 11 旁路工具 (file_edit/file_grep/file_list/http_post/json_query/system_info/process_list/env_get/sleep_ms/current_time/file_append)
            web=self.cfg.tools.web,
            think=self.cfg.tools.think,
        )
        self.sessions = sessions or SessionManager(
            storage_path=self.cfg.session.path,
            max_messages=self.cfg.session.max_messages,
        )
        self.system_prompt = SYSTEM_PROMPT
        self.max_tool_iterations = self.cfg.tools.max_tool_iterations
        self.loaded_skills: List[str] = []
        self.feedback_learner = feedback_learner
        self._llm_router = llm_router

        # 子引擎: 三段式核心（本地快速路径 + 纯润色）
        self.thinker = LocalThinker(self.tools._workspace, self.tools)
        self.runner = ActionRunner(self.tools)
        self.synthesizer = ResultSynthesizer(self.provider, self.system_prompt)

        # 子引擎: 真正的多步推理 —— Agent 先接收 → LLM 思考 → 推理 → 工具 → 最终答案
        # 这是 Agent 的"大脑"，取代原先简单的 LLM 转发器行为。
        self.reasoner = AgentReasoningEngine(
            provider=self.provider,
            tools=self.tools,
            system_prompt=self.system_prompt,
            max_steps=max(3, min(12, self.max_tool_iterations)),
        )

        # 子引擎: 7 步扩展（全部"可用即启用，失败不影响主流程"）
        ws = Path(self.tools._workspace)

        # 步骤 1-2: CapabilityRegistry
        if cap_registry is not None:
            self.cap_registry = cap_registry
        elif _CAP_REGISTRY_AVAILABLE and CapabilityRegistry is not None:
            try:
                self.cap_registry = CapabilityRegistry()
                if hasattr(self.cap_registry, "register_defaults"):
                    self.cap_registry.register_defaults()
            except Exception:
                self.cap_registry = None
        else:
            self.cap_registry = None

        # 步骤 3: MemoryStore
        if memory_store is not None:
            self.memory_store = memory_store
        elif _MEMORY_AVAILABLE and MemoryStore is not None:
            try:
                self.memory_store = MemoryStore(ws)
            except Exception:
                self.memory_store = None
        else:
            self.memory_store = None

        # 步骤 5a: nanobot 桥
        if nanobot_bridge is not None:
            self.nanobot = nanobot_bridge
        elif _NANOBOT_AVAILABLE and NanobotBridge is not None:
            try:
                self.nanobot = NanobotBridge(workspace=str(ws))
            except Exception:
                self.nanobot = None
        else:
            self.nanobot = None

        # 步骤 5b: self_modify
        if self_modifier is not None:
            self.self_modifier = self_modifier
        elif _SELFMODIFY_AVAILABLE and SelfModifier is not None:
            try:
                self.self_modifier = SelfModifier(
                    workspace=str(ws),
                    llm_router=self._llm_router,
                    auto_merge=False,
                )
            except Exception:
                self.self_modifier = None
        else:
            self.self_modifier = None

        # 步骤 5c: GEPEngine（连接到三核融合循环，非"状态探查"）
        self.gep_engine = gep_engine
        if self.gep_engine is None and _GEP_AVAILABLE and GEPEngine is not None:
            try:
                # 优先构造 GEPEngine（用当前 Agent 的 memory_store + cap_registry）
                self.gep_engine = GEPEngine(
                    memory=self.memory_store,
                    llm=self._llm_router,
                    capability_registry=self.cap_registry,
                    workspace=ws,
                )
            except Exception:
                self.gep_engine = None

        # 步骤 5d: Curiosity（三核融合中的探索核）
        self.curiosity = None
        self.curiosity_explorer = None
        if _CURIOSITY_AVAILABLE:
            try:
                from superclaw.curiosity import (
                    NoveltyScorer, BoredomTracker, CuriosityDrive,
                    CuriosityDrivenExplorer,
                )
                scorer = NoveltyScorer()
                boredom = BoredomTracker()
                self.curiosity = CuriosityDrive(scorer=scorer, boredom=boredom)
                if self.cap_registry is not None:
                    self.curiosity_explorer = CuriosityDrivenExplorer(
                        curiosity=self.curiosity,
                        capability_registry=self.cap_registry,
                        llm_router=self._llm_router,
                    )
            except Exception:
                self.curiosity = None
                self.curiosity_explorer = None

        # 步骤 5e: ExperienceLearner（三核共享的经验记忆层）
        self.experience_learner = experience_learner
        if self.experience_learner is None:
            try:
                from superclaw.experience_learner import ExperienceLearner
                logs_dir = Path(self.tools._workspace) / "experience-logs"
                logs_dir.mkdir(parents=True, exist_ok=True)
                self.experience_learner = ExperienceLearner(
                    logs_dir / f"session-{int(time.time())}.jsonl"
                )
            except Exception:
                self.experience_learner = None

        # 步骤 5f: TriOrchestrator（三核融合自循环编排器）—— 真正的神经通路
        self.orchestrator = TriOrchestrator(
            agent=self,
            reasoner=self.reasoner,
            gep_engine=self.gep_engine,
            curiosity=self.curiosity,
            curiosity_explorer=self.curiosity_explorer,
            experience_learner=self.experience_learner,
            cap_registry=self.cap_registry,
            tools=self.tools,
        )

        # 启动加载核心 md 知识注入上下文
        self._loaded_core_md: List[str] = []
        self._autoload_core_md()
        self._autoload_skills()

    def capabilities_summary(self) -> Dict[str, Any]:
        """汇总 7 步各子引擎的可用状态 —— 供 Agent 自己检查"""
        caps: Dict[str, Any] = {}
        caps["capability_registry"] = bool(self.cap_registry)
        caps["memory_store"] = bool(self.memory_store)
        caps["nanobot_bridge"] = bool(self.nanobot)
        caps["nanobot_subsystems"] = list(NANOBOT_SUBSYSTEMS) if _NANOBOT_AVAILABLE else []
        caps["self_modifier"] = bool(self.self_modifier)
        caps["gep_engine"] = bool(self.gep_engine)
        caps["curiosity"] = bool(getattr(self, "curiosity", None))
        caps["experience_learner"] = bool(getattr(self, "experience_learner", None))
        caps["orchestrator"] = bool(getattr(self, "orchestrator", None))
        caps["thinker"] = bool(getattr(self, "thinker", None))
        caps["runner"] = bool(getattr(self, "runner", None))
        caps["synthesizer"] = bool(getattr(self, "synthesizer", None))
        if self.cap_registry is not None and hasattr(self.cap_registry, "list_all"):
            try:
                caps["capabilities"] = [c.name for c in self.cap_registry.list_all()[:30]]
            except Exception:
                caps["capabilities"] = []
        return caps

    def _autoload_core_md(self) -> None:
        """启动时读取 core md 文件并注入 system prompt"""
        ws = Path(self.tools._workspace)
        if not ws.exists():
            return
        blocks: List[str] = []
        for fname in _CORE_MD_FILES:
            fpath = ws / fname
            if fpath.exists() and fpath.is_file():
                try:
                    text = fpath.read_text(encoding="utf-8", errors="ignore").strip()
                    if not text:
                        continue
                    preview = text[:1500]
                    if len(text) > 1500:
                        preview += "\n...[已截断]"
                    blocks.append(f"## {fname}\n{preview}")
                    self._loaded_core_md.append(fname)
                except Exception:
                    continue
        if blocks:
            joined = "\n\n".join(blocks)
            self.system_prompt = (
                f"{self.system_prompt}\n\n===== 你的本地身份与知识体系 =====\n{joined}\n===== 结束 =====\n"
            )
            self.synthesizer.system_prompt = self.system_prompt

    def _autoload_skills(self) -> None:
        skills_dir = Path(self.cfg.workspace) / "skills"
        if not skills_dir.exists():
            return
        skills = scan_skills(skills_dir)
        for skill in skills:
            if self.add_skill(skill["path"]):
                self.loaded_skills.append(skill["path"])

    def set_system_prompt(self, prompt: str) -> None:
        self.system_prompt = prompt
        self.synthesizer.system_prompt = prompt

    def add_skill(self, skill_file: str) -> bool:
        path = Path(skill_file)
        if not path.exists():
            return False
        try:
            md = path.read_text(encoding="utf-8")
        except Exception:
            return False
        lines = md.strip().splitlines()
        if not lines:
            return False
        title = lines[0].lstrip("#").strip()
        body = "\n".join(lines[1:]).strip()
        self.system_prompt = f"{self.system_prompt}\n\n## Skill: {title}\n{body}"
        self.synthesizer.system_prompt = self.system_prompt
        return True

    def _retrieve_relevant_memory(self, user_input: str) -> Optional[str]:
        """用户输入检索本地 md 知识/反思"""
        if not self.tools.has("memory"):
            return None
        try:
            result = self.tools.call("memory", query=user_input)
            content = getattr(result, "content", str(result))
            if not content:
                return None
            if "未找到" in content and len(content) < 200:
                return None
            if "暂无" in content and len(content) < 200:
                return None
            snippet = content[:600]
            if len(content) > 600:
                snippet += "..."
            return snippet
        except Exception:
            return None

    def _write_turn_to_memory(self, session_key: str,
                              user_input: str, assistant_reply: str) -> None:
        """对话结束后写入 memory/<session>.md"""
        try:
            ws = Path(self.tools._workspace)
            mem_dir = ws / "memory"
            mem_dir.mkdir(parents=True, exist_ok=True)
            log_file = mem_dir / f"{session_key}.md"
            from datetime import datetime
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            entry = f"\n### 对话 @ {ts}\n- Q: {user_input[:400]}\n- A: {str(assistant_reply)[:400]}\n"
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(entry)
        except Exception:
            pass

    # ============ 回退路径: 旧的 LLM 驱动工具调用循环 ============
    def _llm_driven_tool_loop(self, user_input: str, session: object,
                               result: "AgentResult", *, verbose: bool,
                               max_iter: int) -> str:
        """旧的工具调用循环 —— 本地无法识别意图时使用

        LLM 输出 <tool xxx></tool> → Agent 调用 → 结果喂回 LLM → 直到 LLM 给自然语言
        """
        iteration = 0
        tool_context: List[str] = []
        last_assistant_message = user_input  # 记录上一个助手消息以避免重复调用同一工具
        while iteration < max_iter:
            iteration += 1  # 每次进入循环 = 调用一次 LLM = 一轮
            messages: List[Dict[str, str]] = [
                {"role": "system", "content": self.system_prompt + self.tools.to_llm_instructions()},
            ]
            for msg in session.messages:
                messages.append({"role": msg["role"], "content": msg["content"]})

            try:
                response = self.provider.call(messages)
            except Exception as e:
                if iteration == 1:
                    return f"LLM 调用失败: {e}"
                return "（LLM 调用失败，以下为已收集的工具结果）\n" + "\n".join(tool_context)

            response_text = str(response).strip()
            if not response_text:
                return "（LLM 返回空响应）"

            tool_calls_raw = _parse_tool_call(response_text)

            if tool_calls_raw is None:
                # LLM 没有调用工具 —— 要么是最终答案，要么是角色介绍
                result.iterations = iteration
                if _looks_like_roleplay(response_text):
                    if tool_context:
                        lines = ["【Agent 直接返回工具结果（LLM 角色违规）】"] + tool_context
                        return "\n".join(lines)
                    session.add("assistant", "请直接回答用户问题，不要自我介绍")
                    continue
                return response_text

            # _parse_tool_call 返回 (name, args) 元组 —— 转成统一的 list[dict] 格式
            tool_calls: List[Dict[str, Any]] = [{
                "tool": tool_calls_raw[0],
                "args": tool_calls_raw[1] or {},
            }]

            if verbose:
                print(f"  [turn {iteration}] LLM 调用工具: "
                      f"{', '.join(tc['tool'] for tc in tool_calls)}")

            # 检查重复调用：如果这一轮的工具调用集合与上一轮完全相同，视为死循环
            this_call_set = tuple(sorted((tc["tool"], json.dumps(tc.get("args", {}), sort_keys=True))
                                          for tc in tool_calls))
            if last_assistant_message == this_call_set:
                if verbose:
                    print("  [turn {}] 检测到重复工具调用，停止循环".format(iteration))
                break
            last_assistant_message = this_call_set  # type: ignore[assignment]

            tool_results_for_llm: List[str] = []
            tool_results_for_user: List[str] = []

            for tc in tool_calls:
                tool_name = tc["tool"]
                tool_args = tc.get("args", {}) or {}

                if not self.tools.has(tool_name):
                    err = f"未知工具: {tool_name}"
                    if verbose:
                        print(f"    {err}")
                    tool_results_for_llm.append(f"[错误] {err}")
                    tool_results_for_user.append(f"[错误] {err}")
                    continue

                if verbose:
                    args_preview = json.dumps(tool_args, ensure_ascii=False)[:80]
                    print(f"    -> 调用 {tool_name}({args_preview})")

                try:
                    tool_result = self.tools.call(tool_name, **tool_args)
                    result_content = str(tool_result.content) if hasattr(tool_result, "content") else str(tool_result)
                    preview = result_content[:500]
                    if len(result_content) > 500:
                        preview += f"\n...[共 {len(result_content)} 字符，已截断]"
                    tool_results_for_llm.append(f"[工具结果: {tool_name}]\n{preview}")
                    tool_results_for_user.append(preview)
                    result.tools_used.append(tool_name)
                    result.tool_outputs.append(result_content)
                except Exception as e:
                    err_msg = f"调用 {tool_name} 时出错: {e}"
                    if verbose:
                        print(f"    [错误] {err_msg}")
                    tool_results_for_llm.append(f"[错误] {err_msg}")
                    tool_results_for_user.append(f"[错误] {err_msg}")

            tool_context.append(
                f"[turn {iteration}] {', '.join(tc['tool'] for tc in tool_calls)} -> "
                + "; ".join(t[:120] for t in tool_results_for_user)
            )

            tool_response = "\n".join(tool_results_for_llm)
            session.add("assistant", response_text)
            session.add("user", tool_response)
            result.iterations = iteration

            if verbose:
                for line in tool_response.splitlines()[:3]:
                    print(f"    {line[:100]}")
                if len(tool_response.splitlines()) > 3:
                    print(f"    ...(共 {len(tool_response.splitlines())} 行)")

        # 超过最大迭代次数 —— 让 LLM 总结
        if iteration >= max_iter:
            if verbose:
                print(f"  已达最大迭代次数 {max_iter}，请 LLM 总结")
            messages = [
                {"role": "system", "content": self.system_prompt},
            ]
            for msg in session.messages:
                messages.append({"role": msg["role"], "content": msg["content"]})
            messages.append({
                "role": "user",
                "content": (
                    f"已经调用了 {iteration} 次工具，请基于工具结果直接用中文自然语言回答我。"
                    "不要再调用工具了。"
                ),
            })
            try:
                response = self.provider.call(messages)
                result.iterations = max_iter  # 总结轮不计入 iterations
                return str(response)
            except Exception:
                lines = [f"已调用 {iteration} 次工具，以下为结果："] + tool_context
                return "\n".join(lines)

        return "\n".join(tool_context)

    def run(self, user_input: str, session_key: str = "default",
            *, verbose: bool = False, max_iterations: Optional[int] = None) -> AgentResult:
        """7 步闭环循环 —— 不再是 LLM 转发器。

        步骤:
            1. 意图理解 (本地 md 知识库)
            2. 意图分类 (CapabilityRegistry)
            3. 记忆检索 (MemoryStore.query + temporal_query)
            4. 工具选择 (Agent 本地决策, ARS schema 22+ 工具)
            5. GEPEngine / nanobot / self_modify 状态同步
            6. LLM 润色 (只负责语言包装)
            7. 归档 (memory/<session>.md)
        """
        t0 = time.time()
        if not user_input.strip():
            return AgentResult(content="你说什么？")
        if self.feedback_learner is not None:
            try:
                self.feedback_learner.detect_and_record(user_input, session_id=session_key)
            except Exception:
                pass

        session = self.sessions.get(session_key)
        session.add("user", user_input)
        result = AgentResult(content="", tools_used=[], tool_outputs=[])
        max_iter = max_iterations if max_iterations is not None else self.max_tool_iterations

        # ========== 步骤 1-2: 本地 md 知识库 + CapabilityRegistry 意图提示 ==========
        # 注入本地 md 上下文到 extra_context（如果 core md 存在则附加进去），
        # 这样推理引擎在第一轮就"拥有本地知识"，而不是从零开始。
        extra_ctx_lines: List[str] = []
        if self._loaded_core_md and self.system_prompt:
            extra_ctx_lines.append(
                f"[Agent 本地 md 知识] 已加载: {', '.join(self._loaded_core_md)}"
            )
        if self.cap_registry is not None and hasattr(self.cap_registry, "list_all"):
            try:
                caps = list(self.cap_registry.list_all())[:8]
                if caps:
                    names = [getattr(c, "name", str(c)) for c in caps]
                    extra_ctx_lines.append(
                        f"[Agent CapabilityRegistry] 可识别: {', '.join(names)}"
                    )
            except Exception:
                pass

        # ========== 步骤 3: 记忆检索 (MemoryStore.query + temporal_query) ==========
        memory_text_hint = ""
        if self.memory_store is not None:
            try:
                memory_text_hint = self.memory_store.query(user_input) or ""
            except Exception:
                memory_text_hint = ""
            try:
                temporal_events = self.memory_store.temporal_query(limit=5)
                if temporal_events:
                    extra_ctx_lines.append(
                        f"[Agent 记忆] 最近 {len(temporal_events)} 个事件"
                    )
            except Exception:
                pass
        if memory_text_hint and str(memory_text_hint).strip():
            extra_ctx_lines.append(f"[Agent 记忆检索结果]\n{memory_text_hint[:500]}")
        if verbose and memory_text_hint:
            print("[步骤3] 记忆检索: 命中本地 md 记忆")

        extra_context = "\n".join(extra_ctx_lines) if extra_ctx_lines else ""

        # ========== 步骤 4-5-6: 三核融合自循环（TriOrchestrator）—— 真正的推理+探索+进化闭环 ==========
        #  与旧代码的区别:
        #   - 旧: 只调一次 reasoner.reason()—— LLM 一轮推理，结束
        #   - 新: orchestrator.run_loop()—— 推理核 → 探索核 → 进化核 → 推理核 闭环:
        #        Round 1: 推理核 (ReasonSteps)
        #        Round 2: 探索核 (Curiosity → ExplorationGoals)
        #        Round 3: 进化核 (GEPEngine → 新 capability 注入 tools)
        #        如 Round 2/3 产出新信息 → 推理核"带着新知识"再推理一次（可选）
        orchestrator = getattr(self, "orchestrator", None)
        if orchestrator is not None:
            reasoning_answer, steps, new_caps, goals = orchestrator.run_loop(
                user_input=user_input,
                extra_context_base=extra_context,
                verbose=verbose,
                _override_max_steps=max_iter,  # 强制限制 LLM 推理步数
            )
            # 新能力/目标记录到 result.tools_used 用于调试
            if new_caps:
                result.tools_used.extend(new_caps)
        else:
            # 退化路径（如果 orchestrator 没初始化）：回退到单层推理
            reasoning_answer, steps, _success = self.reasoner.reason(
                user_input=user_input,
                extra_context=extra_context,
                verbose=verbose,
                _override_max_steps=max_iter,
            )

        # 把 steps 映射到 result
        used_tools: List[str] = []
        obs_outputs: List[str] = []
        for s in steps:
            if s.kind == "observation" and s.tool_name and not s.error:
                # 只有成功调用的工具才算入 tools_used
                used_tools.append(s.tool_name)
                obs_outputs.append(str(s.tool_result)[:500] if s.tool_result else "")
        result.tools_used = used_tools
        result.tool_outputs = obs_outputs
        # iterations = LLM 决策次数（= 非 observation 的 step 数）
        # 注意: 达到 max_steps 后"再调 LLM 总结"那次不算入 iterations
        #       （它不在 for loop 里，也不写入 steps），所以这里简单统计即可
        result.iterations = sum(1 for s in steps if s.kind != "observation")

        # ========== 步骤 5 补充（可选）: nanobot 同步 + self_modify 状态 ==========
        if self.nanobot is not None:
            try:
                ns = self.nanobot.status()
                if ns.reachable or ns.inbox_items > 0:
                    low = (user_input or "").lower()
                    if any(k in low for k in ("nanobot", "同步", "sync", "9 子")):
                        self.nanobot.pull_all_subsystems()
                        self.nanobot.push_event("user_dialogue", {"text": user_input[:500]})
            except Exception:
                pass

        # ========== 步骤 7: 归档 (memory/<session>.md) ==========
        session.add("assistant", reasoning_answer)
        result.content = reasoning_answer
        self._write_turn_to_memory(session_key, user_input, reasoning_answer)
        self.sessions.save(session_key)
        result.total_time_ms = int((time.time() - t0) * 1000)
        return result

    def chat(self, session_key: str = "default") -> None:
        """交互式对话模式 —— 与之前保持兼容"""
        ver = __import__("superclaw").__version__
        print(f"superclaw v{ver}")
        print(f"Provider: {self.cfg.llm.provider} | Model: {self.cfg.llm.model}")
        if self._loaded_core_md:
            print(f"已加载本地 md 知识: {', '.join(self._loaded_core_md)}")
        if self.loaded_skills:
            print(f"已加载 Skills: {len(self.loaded_skills)} 个")
        print("流程: 本地思考 -> 本地执行工具 -> LLM 润色")
        print("输入 exit 或 Ctrl+C 退出。")
        print("命令: /clear 清会话 | /tools 看工具 | /skills 看技能 | /memory 查记忆 | /think 切换推理过程显示\n")

        verbose_chat = False
        while True:
            try:
                user_input = input("你: ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if user_input in ("exit", "quit", "q"):
                break
            elif user_input == "/clear":
                self.sessions.clear(session_key)
                print("  会话已清除")
                continue
            elif user_input == "/tools":
                for name in self.tools.names:
                    desc = self.tools.get_description(name) or ""
                    print(f"  - {name}: {desc}")
                continue
            elif user_input == "/skills":
                if self.loaded_skills:
                    for s in self.loaded_skills:
                        print(f"  {s}")
                else:
                    print("  无已加载 skill")
                continue
            elif user_input == "/memory":
                result = self.tools.call("memory", query="状态")
                print(result.content)
                continue
            elif user_input == "/think":
                verbose_chat = not verbose_chat
                print(f"  推理过程显示: {'开启' if verbose_chat else '关闭'}")
                continue
            elif user_input.startswith("/skill "):
                skill_file = user_input[7:].strip()
                if self.add_skill(skill_file):
                    self.loaded_skills.append(skill_file)
                    print(f"  已加载 skill: {skill_file}")
                else:
                    print(f"  加载失败: {skill_file}")
                continue
            elif not user_input:
                continue

            print()
            agent_result = self.run(user_input, session_key=session_key, verbose=verbose_chat)
            tools_line = ", ".join(dict.fromkeys(agent_result.tools_used)) if agent_result.tools_used else "无"
            print(f"{agent_result.content}")
            print(f"  [工具: {tools_line} | 迭代: {agent_result.iterations} | 用时: {agent_result.total_time_ms}ms]\n")


# ======================================================================
# TriOrchestrator —— 三核融合自循环编排器
# ======================================================================
# 推理核（ReAct）  产出 ReasonSteps → 写入 ExperienceLearner
# 探索核（Curiosity） 从 ReasonSteps 提取 signals → 生成 ExplorationGoals
# 进化核（GEPEngine）  从 ExperienceLearner 读取 outcome → 提取信号 → 进化 capability
# 三核共享的是 ExperienceLearner —— 这是它们之间的"神经通路"
# ======================================================================

@dataclass
class OrchestratorRound:
    """单轮三核循环记录"""
    round_id: int
    reason_steps: List["ReasonStep"]
    reasoning_answer: str
    curiosity_goals: List[Any]
    gep_outcome: Optional[Dict[str, Any]]
    new_capabilities: List[str]
    time_ms: int


class TriOrchestrator:
    """三核融合自循环编排器 —— 真正让 ReAct 推理 / GEP 进化 / Curiosity 探索形成闭环。

    核心循环（默认 2 轮，可配置）：
        Round 1: 推理核（agent.reasoner）基于原始输入推理 → 产出 ReasonSteps
            ↓ 记录为 experience + signals
        Round 2: 探索核（curiosity.explorer）发现目标 → 进化核（gep_engine）产生新 capability
            ↓ 新 capability 注入 Agent.tools，目标注入 prompt
        Round 3（可选）: 推理核"带着新知识"再推理一次 → 产出最终答案

    关键桥接：
        reason_steps → experience_learner.record()
        reason_steps → signal_strings → curiosity.should_explore()
        gep_engine.run_cycle() → 新 capability → tools.register()
        exploration_goals + 新工具名 → 注入 extra_context 给推理核
    """

    def __init__(self,
                 agent: "Agent",
                 reasoner: "AgentReasoningEngine",
                 gep_engine: Optional["Any"] = None,
                 curiosity: Optional["Any"] = None,
                 curiosity_explorer: Optional["Any"] = None,
                 experience_learner: Optional["Any"] = None,
                 cap_registry: Optional["Any"] = None,
                 tools: Optional["Any"] = None,
                 max_rounds: int = 2):
        self.agent = agent
        self.reasoner = reasoner
        self.gep = gep_engine
        self.curiosity = curiosity
        self.explorer = curiosity_explorer
        self.experience = experience_learner
        self.cap_registry = cap_registry
        self.tools = tools
        self.max_rounds = max_rounds

        # 已注入的 capability 集合（避免重复注入）
        self._injected_caps: Set[str] = set()

        # 统计：三核各自的活跃程度（给 verbose 输出看）
        self._stats: Dict[str, int] = {
            "reason_cycles": 0,
            "curiosity_signals_consumed": 0,
            "gep_cycles": 0,
            "new_capabilities_injected": 0,
        }

    # ------------------------------------------------------------
    # 桥接 1: ReasonSteps → ExperienceLearner
    # ------------------------------------------------------------
    def record_reason_experience(self, steps: List["ReasonStep"],
                                  user_input: str,
                                  strategy: str = "balanced") -> None:
        """把推理过程记录为 experience —— GEP 进化核下一轮就能据此提取信号"""
        if self.experience is None:
            return
        try:
            # 评估推理是否"有成效"
            #   - 有 action 且成功 → score 高
            #   - 纯 thought 没调用工具 → 中等
            #   - 有 error 或 未知工具 → score 低
            tool_calls = [s for s in steps if s.kind == "action"]
            success_calls = [s for s in steps if s.kind == "observation" and not s.error]
            errors = [s for s in steps if s.kind == "observation" and s.error]

            if tool_calls and success_calls and not errors:
                score = 0.85
                retained = True
            elif tool_calls:
                score = 0.5
                retained = len(success_calls) > 0
            else:
                score = 0.3
                retained = False

            category = "explore" if errors or not tool_calls else "reason"
            self._stats["reason_cycles"] += 1

            outcome_cls = self._get_strategy_outcome_cls()
            if outcome_cls is not None:
                outcome = outcome_cls(
                    strategy=strategy,
                    category=category,
                    score=score,
                    retained=retained,
                    timestamp=datetime.now().isoformat(),
                    cycle=self._stats["reason_cycles"],
                    signal_count=len(steps),
                )
                self.experience.record(outcome)
        except Exception:
            # 经验记录失败不影响主流程
            pass

    def _get_strategy_outcome_cls(self) -> Optional[Any]:
        """懒加载 StrategyOutcome 类（只有 experience_learner 模块可用时）"""
        try:
            from superclaw.experience_learner import StrategyOutcome
            return StrategyOutcome
        except Exception:
            return None

    # ------------------------------------------------------------
    # 桥接 2: ReasonSteps → curiosity signals
    # ------------------------------------------------------------
    def extract_signals(self, steps: List["ReasonStep"], user_input: str) -> List[str]:
        """从推理过程提取可被 curiosity/GEP 使用的信号字符串"""
        signals: List[str] = []
        # 2.1 用户输入关键词（作为"需求信号"）
        if user_input:
            for kw in _SIGNAL_KEYWORDS:
                if kw in user_input.lower():
                    signals.append(f"user_keyword:{kw}")

        # 2.2 工具调用结果
        for s in steps:
            if s.kind == "observation" and s.tool_name:
                if s.error:
                    signals.append(f"tool_failed:{s.tool_name}")
                else:
                    signals.append(f"tool_success:{s.tool_name}")
            elif s.kind == "thought":
                # 含"不知道/不懂/需要"等词汇 → 知识缺口信号
                low = str(s.content).lower()
                for gap in ("不知道", "不懂", "需要", "cannot", "don't know", "not sure"):
                    if gap in low:
                        signals.append(f"knowledge_gap:{gap}")
                        break
        # 2.3 未知工具 → capability gap
        for s in steps:
            if s.kind == "observation" and s.error and "未知工具" in str(s.content):
                signals.append("capability_gap:unknown_tool")
        return signals

    # ------------------------------------------------------------
    # 桥接 3: Curiosity → exploration goals → prompt
    # ------------------------------------------------------------
    def curiosity_discover_goals(self, current_signals: List[str]) -> List[str]:
        """基于当前信号，让探索核推荐探索目标"""
        goals: List[str] = []
        if self.curiosity is None:
            return goals

        try:
            self._stats["curiosity_signals_consumed"] += len(current_signals)

            # 如果有 explorer，用它生成结构化目标
            if self.explorer is not None:
                try:
                    caps = list(self.tools.names) if self.tools else []
                    targets = self.explorer.discover_targets({
                        "current_signals": current_signals,
                        "known_caps": caps,
                        "total_reason_cycles": self._stats["reason_cycles"],
                    })
                    for t in targets[:3]:
                        goals.append(
                            f"[探索目标/{getattr(t, 'reason', 'novelty')}] "
                            f"{getattr(t, 'target_domain', 'unknown')} "
                            f"(预期奖励 {getattr(t, 'expected_reward', 0.0):.2f})"
                        )
                    return goals
                except Exception:
                    pass

            # 退而求其次：直接用 CuriosityDrive.should_explore 做简单判定
            if self.curiosity.should_explore(current_signals):
                goals.append("[探索目标/boredom] 当前输入组合较为新颖，建议下一轮尝试不同策略")
        except Exception:
            pass
        return goals

    # ------------------------------------------------------------
    # 桥接 4: GEPEngine → 进化新 capability → 注入 tools
    # ------------------------------------------------------------
    def gep_evolve_capability(self, signals: List[str],
                              user_input: str,
                              verbose: bool = False) -> List[str]:
        """调用 GEP 进化核，尝试从 signals 生成新 capability，成功则注入 Agent.tools

        返回本次新注入的工具名列表（可能为空，表示进化没产出）。
        """
        new_names: List[str] = []
        if self.gep is None:
            return new_names

        try:
            self._stats["gep_cycles"] += 1
            # 执行一个轻量进化循环
            cycle_result = self.gep.run_cycle()

            # 尝试从进化结果中提取"被固化的 capability"
            # GEPEngine 的 run_cycle 返回 Dict[str, Any]，其中 steps[9]_solidify 常含 retained 信息
            retained_names: List[str] = []
            try:
                steps = cycle_result.get("steps", {}) if isinstance(cycle_result, dict) else {}
                for step_name, step_val in steps.items():
                    if isinstance(step_val, dict):
                        # 尝试从各种字段里抓 retained capability 名
                        if step_val.get("retained"):
                            name = step_val.get("gene") or step_val.get("capability") or step_val.get("name")
                            if name:
                                retained_names.append(str(name))
                        if isinstance(step_val.get("retained_genes"), list):
                            for g in step_val["retained_genes"]:
                                if isinstance(g, str):
                                    retained_names.append(g)
            except Exception:
                pass

            # 退而求其次：如果 capability_registry 里有新注册的，与之前记录对比找 diff
            if not retained_names and self.cap_registry is not None:
                try:
                    cap_names = getattr(self.cap_registry, "list_capabilities",
                                        lambda: [])()
                    for name in cap_names:
                        if name not in self._injected_caps:
                            retained_names.append(str(name))
                except Exception:
                    pass

            # 把新 capability 注入 Agent.tools
            new_names = self._inject_capabilities(retained_names, verbose=verbose)
        except Exception as e:
            if verbose:
                print(f"  │  [进化核] GEP 运行失败但不阻塞主流程: {e}")
        return new_names

    def _inject_capabilities(self, candidate_names: List[str],
                             *, verbose: bool = False) -> List[str]:
        """把 capability_registry 里的候选能力注册到 Agent.tools"""
        injected: List[str] = []
        if self.tools is None or self.cap_registry is None:
            return injected

        for name in candidate_names:
            if not name or name in self._injected_caps:
                continue
            # 只注册 capability_registry 里确实存在的
            try:
                cap = self.cap_registry.get(name) if hasattr(self.cap_registry, "get") else None
                if cap is None:
                    continue
                func = getattr(cap, "func", None) or getattr(cap, "callable", None)
                if func is None:
                    continue
                desc = getattr(cap, "description", "") or f"GEP 进化的 {name} 能力"
                self.tools.register(name, func, desc)
                self._injected_caps.add(name)
                injected.append(name)
                self._stats["new_capabilities_injected"] += 1
                if verbose:
                    print(f"  │  [进化核] ✓ 新能力注入: {name}")
            except Exception:
                continue
        return injected

    # ------------------------------------------------------------
    # 主入口: 三核融合循环
    # ------------------------------------------------------------
    def run_loop(self, user_input: str,
                 extra_context_base: str = "",
                 *, verbose: bool = False,
                 force_gep: bool = False,
                 _override_max_steps: Optional[int] = None) -> Tuple[str, List["ReasonStep"], List[str], List[str]]:
        """执行三核融合自循环。

        返回: (final_answer, all_reason_steps, new_capabilities, curiosity_goals)
        """
        all_steps: List["ReasonStep"] = []
        all_goals: List[str] = []
        all_new_caps: List[str] = []
        extra_ctx = extra_context_base

        # ===== Round 1: 推理核（原始输入） =====
        if verbose:
            print("\n  ╔══════════════════════════════════════════╗")
            print("  ║  🧠 三核融合循环 · Round 1/3 · 推理核     ║")
            print("  ╚══════════════════════════════════════════╝")

        answer1, steps1, ok1 = self.reasoner.reason(
            user_input=user_input,
            extra_context=extra_ctx,
            verbose=verbose,
            _override_max_steps=_override_max_steps,
        )
        all_steps.extend(steps1)

        # 记录为经验（GEP 下一轮的粮食）
        self.record_reason_experience(steps1, user_input)

        # 提取信号（给 curiosity + GEP 用）
        signals = self.extract_signals(steps1, user_input)

        # ===== Round 2: 探索核（发现目标） =====
        if verbose:
            print("\n  ╔══════════════════════════════════════════╗")
            print("  ║  🔍 三核融合循环 · Round 2/3 · 探索核     ║")
            print("  ╚══════════════════════════════════════════╝")
            if signals:
                print(f"  │  当前信号: {signals[:5]}...")
        goals = self.curiosity_discover_goals(signals)
        if goals:
            all_goals.extend(goals)
            if verbose:
                for g in goals:
                    print(f"  │  {g}")
        elif verbose:
            print("  │  暂无探索目标")

        # ===== Round 3: 进化核（生成新能力） =====
        do_gep = force_gep or bool(signals) or self.gep is not None
        if do_gep and self.gep is not None:
            if verbose:
                print("\n  ╔══════════════════════════════════════════╗")
                print("  ║  🧬 三核融合循环 · Round 3/3 · 进化核      ║")
                print("  ╚══════════════════════════════════════════╝")
            new_caps = self.gep_evolve_capability(signals, user_input, verbose=verbose)
            all_new_caps.extend(new_caps)
        else:
            if verbose and self.gep is None:
                print("  │  （进化核未连接，跳过 Round 3）")

        # ===== 闭环: 如果有新能力/新目标，再让推理核"带着新知识"推理一次 =====
        final_answer = answer1
        if all_new_caps or all_goals:
            extra_lines: List[str] = []
            if all_new_caps:
                extra_lines.append(f"[Agent 内部进化] 本次会话新增能力: {', '.join(all_new_caps)}")
            if all_goals:
                extra_lines.append("[Agent 内部探索] 系统建议关注:")
                extra_lines.extend([f"  - {g}" for g in all_goals])
            extra_ctx_new = "\n".join(extra_lines)
            if extra_ctx:
                extra_ctx_new = extra_ctx + "\n" + extra_ctx_new

            if verbose:
                print("\n  ╔══════════════════════════════════════════╗")
                print("  ║  🔄 三核融合循环 · 最终推理（带新知识）    ║")
                print("  ╚══════════════════════════════════════════╝")

            answer2, steps2, ok2 = self.reasoner.reason(
                user_input=user_input,
                extra_context=extra_ctx_new,
                verbose=verbose,
            )
            all_steps.extend(steps2)
            self.record_reason_experience(steps2, user_input, strategy="post_evolution")
            # 如果第二次推理收敛了就用它，否则还回第一次的
            if ok2:
                final_answer = answer2

        if verbose:
            print("\n  ┌─────────────────────────────────────────┐")
            print("  │  📊 三核融合循环统计                       │")
            print(f"  │  推理轮次: {self._stats['reason_cycles']}")
            print(f"  │  探索信号: {self._stats['curiosity_signals_consumed']}")
            print(f"  │  进化轮次: {self._stats['gep_cycles']}")
            print(f"  │  新能力注入: {self._stats['new_capabilities_injected']}")
            print(f"  │  探索目标数: {len(all_goals)}")
            print("  └─────────────────────────────────────────┘\n")

        return final_answer, all_steps, all_new_caps, all_goals


# 用于从用户输入 / ReasonSteps 中识别信号的关键词表
_SIGNAL_KEYWORDS: List[str] = [
    "read", "file", "read_file", "shell", "execute", "run",
    "weather", "天气", "search", "搜索", "parse", "解析",
    "json", "http", "web", "记忆", "memory", "进化", "evolve",
    "capability", "能力", "工具", "tool",
]

