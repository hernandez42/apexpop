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
from pathlib import Path
from typing import Dict, List, Optional, Tuple

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
    """从文本中解析工具调用 —— 兼容 XML 风格和 JSON 格式"""
    if not text:
        return None
    stripped = text.strip()
    m = re.search(r"<tool\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*>(.*?)</tool>", stripped, re.DOTALL)
    if m:
        name = m.group(1).strip()
        body = m.group(2)
        args = dict(re.findall(r"<(\w+)>(.*?)</\1>", body, re.DOTALL))
        if name:
            return name, args
    m = re.search(r"<([a-zA-Z_][a-zA-Z0-9_]*)>(.*?)</\1>", stripped, re.DOTALL)
    if m and ("path" in m.group(2) or "cmd" in m.group(2) or "query" in m.group(2)):
        name = m.group(1)
        args = dict(re.findall(r"<(\w+)>(.*?)</\1>", m.group(2), re.DOTALL))
        return name, args
    try:
        data = json.loads(stripped)
        if isinstance(data, dict) and "tool" in data:
            args = data.get("args", {}) or {}
            return data["tool"], {k: str(v) for k, v in args.items()}
    except (json.JSONDecodeError, ValueError):
        pass
    try:
        data = json.loads(stripped)
        if isinstance(data, dict) and "name" in data:
            args = data.get("input", {}) or {}
            return data["name"], {k: str(v) for k, v in args.items()}
    except (json.JSONDecodeError, ValueError):
        pass
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


# ============ 主 Agent 类: 三段式循环 ============
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
    ):
        self.cfg = cfg or load_config()
        self.provider = provider or get_provider(self.cfg.llm)
        self.tools = tools or build_default_tools(
            self.cfg.workspace,
            shell=self.cfg.tools.shell,  # nosec B604
            file_tools=self.cfg.tools.file,
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

        # 子引擎: 三段式核心
        self.thinker = LocalThinker(self.tools._workspace, self.tools)
        self.runner = ActionRunner(self.tools)
        self.synthesizer = ResultSynthesizer(self.provider, self.system_prompt)

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

        # 步骤 5c: GEPEngine（只做状态探查，不自动 run_cycle）
        self.gep_engine = gep_engine
        if self.gep_engine is None and _GEP_AVAILABLE and GEPEngine is not None:
            try:
                # 不主动传 cfg/provider —— 由外部设置
                pass
            except Exception:
                pass

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
    def _llm_driven_tool_loop(self, user_input: str, session: "Session",
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

        # ========== 步骤 1-2: 本地思考 + CapabilityRegistry 分类 ==========
        intent = self.thinker.analyze(user_input)
        if verbose:
            print(f"[步骤1-2] 意图: {intent.type} — {intent.reasoning}")

        # ========== 步骤 3: 记忆检索 (query + temporal_query) ==========
        memory_text_hint = ""
        temporal_hint = ""
        if self.memory_store is not None:
            try:
                memory_text_hint = self.memory_store.query(user_input) or ""
            except Exception:
                memory_text_hint = ""
            try:
                temporal_events = self.memory_store.temporal_query(limit=5)
                if temporal_events:
                    temporal_hint = "最近" + str(len(temporal_events)) + "个记忆事件"
            except Exception:
                temporal_hint = ""
        if verbose:
            print(f"[步骤3] 记忆检索: query={bool(memory_text_hint)} temporal={temporal_hint or '无'}")

        # ========== 步骤 4: Agent 本地选择工具 (不外包给 LLM) ==========
        actions = self.thinker.plan_actions(intent)
        # 本地三段式只处理 "必须调用工具" 的意图类型
        # - 闲聊(chit_chat) / unknown / code_ask 等都走 LLM 路径，因为它们本质需要自然语言
        use_local = intent.type in ("file_read", "shell", "web_read",
                                     "memory_query", "list_tools",
                                     "self_modify_request")
        if use_local and actions:
            if verbose:
                for act in actions:
                    print(f"[步骤4] -> 工具: {act.tool}({json.dumps(act.args, ensure_ascii=False)}) — {act.reason}")
            executed, _errors = self.runner.execute(actions)
            for act in executed:
                result.tools_used.append(act.tool)
                result.tool_outputs.append(str(act.result))
            result.iterations = len(executed)
            if verbose:
                for act in executed:
                    r = (act.result or "")[:120].replace("\n", " ")
                    print(f"[步骤4 执行] {act.tool} -> {r}")
        else:
            if verbose:
                print(f"[步骤4] 本地无法明确，回退到 LLM 驱动工具循环")
            # ========== 路径 B: 回退 LLM 驱动工具调用循环 ==========
            loop_answer = self._llm_driven_tool_loop(
                user_input, session, result, verbose=verbose, max_iter=max_iter
            )
            session.add("assistant", loop_answer)
            result.content = loop_answer
            self._write_turn_to_memory(session_key, user_input, loop_answer)
            self.sessions.save(session_key)
            result.total_time_ms = int((time.time() - t0) * 1000)
            return result

        # ========== 步骤 5: nanobot 9 子系统同步 + self_modify 状态 + GEP ==========
        nanobot_note = ""
        if self.nanobot is not None:
            try:
                ns = self.nanobot.status()
                nanobot_note = (f"nanobot reachable={ns.reachable}, "
                                f"inbox={ns.inbox_items}, "
                                f"9_subsystems={list(NANOBOT_SUBSYSTEMS)[:3]}...")
                low = (user_input or "").lower()
                if any(k in low for k in ("nanobot", "同步", "sync", "9 子")):
                    pull_result = self.nanobot.pull_all_subsystems()
                    nanobot_note += f" | pull: ok={pull_result.get('ok', 0)}/{pull_result.get('total', 0)}"
                    inbox = self.nanobot.read_inbox(limit=5)
                    if inbox:
                        nanobot_note += f" | inbox_items={len(inbox)}"
                    self.nanobot.push_event("user_dialogue", {"text": user_input[:500]})
            except Exception as e:
                nanobot_note = f"nanobot 状态不可用: {e}"

        self_modify_status = None
        if self.self_modifier is not None:
            try:
                self_modify_status = self.self_modifier.summary()
            except Exception:
                self_modify_status = None

        if verbose:
            print(f"[步骤5] {nanobot_note or 'nanobot 未接入'} / self_modify={self_modify_status}")

        # ========== 步骤 6: LLM 润色 (只负责语言包装) ==========
        final_answer = self.synthesizer.synthesize(
            intent=intent,
            actions=executed,
            user_input=user_input,
            local_memory_hint=memory_text_hint or "",
        )

        # 防御: LLM 角色违规 —— 直接返回 Agent 本地结果
        if _looks_like_roleplay(final_answer) and any(executed):
            lines = [f"【Agent 本地推理】意图: {intent.type} — {intent.reasoning}"]
            for act in executed:
                preview = (act.result or "")[:400]
                lines.append(f"[{act.tool}] {preview}")
            lines.append("\n[提示] LLM 在语言总结环节又自我介绍了，Agent 直接返回本地推理结果。")
            final_answer = "\n".join(lines)

        # ========== 步骤 7: 归档记忆 (memory/<session>.md) ==========
        self._write_turn_to_memory(session_key, user_input, final_answer)
        session.add("assistant", final_answer)
        result.content = final_answer
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

