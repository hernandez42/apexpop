"""
superclaw 核心 Agent 循环
参考 nanobot 的 AgentLoop 设计，极简但功能完整
"""
import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

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


@dataclass
class AgentResult:
    """Agent 执行结果"""
    content: str
    tools_used: List[str] = field(default_factory=list)
    tool_outputs: List[str] = field(default_factory=list)
    iterations: int = 0
    total_time_ms: int = 0


# 工具调用模式
# 支持两种格式：
# <tool name> <arg=value> ...</tool>
# {"tool": "name", "args": {"key": "value"}}
_TOOL_TAG_RE = re.compile(
    r'<tool\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*>(.*?)</tool>',
    re.DOTALL
)
_ARG_RE = re.compile(r'<(\w+)>(.*?)</\1>', re.DOTALL)


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


class Agent:
    """superclaw 核心 Agent
    - 可配置 LLM Provider
    - 工具调用循环（LLM -> 工具 -> LLM 迭代）
    - 会话记忆
    - Skill Markdown 支持
    """

    def __init__(
        self,
        cfg: Optional[SuperclawConfig] = None,
        provider: Optional[BaseProvider] = None,
        tools: Optional[ToolRegistry] = None,
        sessions: Optional[SessionManager] = None,
        feedback_learner: Optional["FeedbackLearner"] = None,
    ):
        self.cfg = cfg or load_config()
        self.provider = provider or get_provider(self.cfg.llm)
        self.tools = tools or build_default_tools(
            self.cfg.workspace,
            shell=self.cfg.tools.shell,  # nosec B604 - 传给 build_default_tools 的布尔开关，非 subprocess 调用
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

        # ---- 用户反馈学习（可选）----
        self.feedback_learner = feedback_learner

        # 自动扫描并加载 skills 目录
        self._autoload_skills()

    def _autoload_skills(self) -> None:
        """自动扫描 skills/ 目录并加载所有 .md skill 文件"""
        skills_dir = Path(self.cfg.workspace) / "skills"
        if not skills_dir.exists():
            return

        skills = scan_skills(skills_dir)
        for skill in skills:
            if self.add_skill(skill["path"]):
                self.loaded_skills.append(skill["path"])

    def set_system_prompt(self, prompt: str) -> None:
        self.system_prompt = prompt

    def add_skill(self, skill_file: str) -> bool:
        """加载一个 Markdown Skill 文件
        Skill 文件格式：
        # Skill Name
        - 触发词: 关键词1, 关键词2
        - 系统提示: <要加入 system prompt 的内容>
        """
        path = Path(skill_file)
        if not path.exists():
            return False
        try:
            md = path.read_text(encoding="utf-8")
        except Exception:
            return False

        # 简单解析：提取标题和正文
        lines = md.strip().splitlines()
        if not lines:
            return False

        title = lines[0].lstrip("#").strip()
        body = "\n".join(lines[1:]).strip()

        # 将 skill 内容附加到 system prompt
        self.system_prompt = (
            f"{self.system_prompt}\n\n## Skill: {title}\n{body}"
        )
        return True

    def run(self, user_input: str, session_key: str = "default",
            *, verbose: bool = False, max_iterations: Optional[int] = None) -> AgentResult:
        """处理用户输入，运行 Agent 循环
        流程:
        user_input -> [LLM 判断是否需要工具] -> 调用工具 -> LLM 整合 -> 返回
        """
        t0 = time.time()

        if not user_input.strip():
            return AgentResult(content="你说什么？")

        # ---- 用户反馈采集（可选）----
        # 检测用户消息是否包含反馈，是则记录到 FeedbackStore
        if self.feedback_learner is not None:
            try:
                self.feedback_learner.detect_and_record(
                    user_input, session_id=session_key,
                )
            except Exception:
                pass  # 反馈采集失败不阻断主流程

        # 获取会话
        session = self.sessions.get(session_key)
        session.add("user", user_input)

        # 构建消息
        tool_hint = ""
        if self.tools.names:
            tool_hint = "\n\n" + self.tools.to_llm_instructions()

        system_prompt = self.system_prompt + tool_hint
        messages = session.to_messages(system_prompt)

        result = AgentResult(content="", tools_used=[], tool_outputs=[])
        max_it = max_iterations or self.max_tool_iterations

        # 工具调用循环 — 类似 nanobot runner
        for i in range(max_it):
            # 调用 LLM
            llm_output = self.provider.call(messages)
            result.iterations = i + 1

            if verbose:
                print(f"[turn {i+1}] LLM: {llm_output[:200]}")

            # 检查是否有工具调用
            tool_call = _parse_tool_call(llm_output)
            if not tool_call:
                # 没有工具调用，LLM 直接给了最终答案
                final_answer = llm_output.strip()
                session.add("assistant", final_answer)
                result.content = final_answer
                break

            tool_name, tool_args = tool_call
            if not self.tools.has(tool_name):
                # LLM 请求了不存在的工具
                error_msg = f"[系统] 未知工具: {tool_name}. 可用工具: {', '.join(self.tools.names)}"
                session.add("assistant", llm_output)
                session.add("tool", error_msg)
                messages.append({"role": "assistant", "content": llm_output})
                messages.append({"role": "tool", "content": error_msg})
                if verbose:
                    print(f"  -> {error_msg}")
                continue

            # 执行工具
            if verbose:
                print(f"  -> 调用工具: {tool_name}({json.dumps(tool_args, ensure_ascii=False)})")

            tool_result = self.tools.call(tool_name, **tool_args)
            result.tools_used.append(tool_name)
            result.tool_outputs.append(str(tool_result))

            # 将助手消息和工具结果加入对话
            session.add("assistant", llm_output)
            session.add("tool", f"[{tool_name}] {tool_result.content}")
            messages.append({"role": "assistant", "content": llm_output})
            messages.append({"role": "tool", "content": tool_result.content})

            if tool_result.error and verbose:
                print(f"  -> 错误: {tool_result.content[:200]}")

        else:
            # 达到最大迭代但没有得到最终答案 — 让 LLM 总结
            summary_msg = (
                f"你已经执行了 {max_it} 次工具调用。请根据以上信息，给用户一个简洁的最终回答，不要继续调用工具。"
            )
            messages.append({"role": "system", "content": summary_msg})
            final = self.provider.call(messages)
            session.add("assistant", final.strip())
            result.content = final.strip()
            if verbose:
                print(f"[总结] {final[:200]}")

        # 保存会话
        self.sessions.save(session_key)

        result.total_time_ms = int((time.time() - t0) * 1000)
        return result

    def chat(self, session_key: str = "default") -> None:
        """交互式对话模式"""
        print(f"🦖 superclaw v{__import__('superclaw').__version__}")
        print(f"Provider: {self.cfg.llm.provider} | Model: {self.cfg.llm.model}")
        if self.loaded_skills:
            print(f"已加载 Skills: {len(self.loaded_skills)} 个")
        print("输入 'exit' 或 Ctrl+C 退出。")
        print("命令: /clear 清会话 | /tools 看工具 | /skills 看技能 | /memory 查记忆 | /skill <file> 加载 skill\n")

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
                print("  ✓ 会话已清除")
                continue
            elif user_input == "/tools":
                for name in self.tools.names:
                    desc = self.tools.get_description(name) or ""
                    print(f"  - {name}: {desc}")
                continue
            elif user_input == "/skills":
                if self.loaded_skills:
                    for s in self.loaded_skills:
                        print(f"  ✓ {s}")
                else:
                    print("  无已加载 skill")
                continue
            elif user_input == "/memory":
                # 直接查询记忆系统状态
                result = self.tools.call("memory", query="状态")
                print(result.content)
                continue
            elif user_input.startswith("/skill "):
                skill_file = user_input[7:].strip()
                if self.add_skill(skill_file):
                    self.loaded_skills.append(skill_file)
                    print(f"  ✓ 已加载 skill: {skill_file}")
                else:
                    print(f"  ✗ 加载失败: {skill_file}")
                continue
            elif not user_input:
                continue

            print()
            agent_result = self.run(user_input, session_key=session_key, verbose=False)
            tools = ", ".join(set(agent_result.tools_used)) if agent_result.tools_used else "无"
            print(f"🦖: {agent_result.content}")
            print(f"  [工具: {tools} | 迭代: {agent_result.iterations} | 用时: {agent_result.total_time_ms}ms]\n")


# 为 `from superclaw.agent import Agent` 时避免循环导入
__all__ = ["Agent", "AgentResult"]
