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


# Agent 启动时需要自动加载的核心 MD 文件
# 这些文件定义了 Agent 的身份与知识体系
_CORE_MD_FILES: List[str] = [
    "SOUL.md",
    "MEMORY.md",
    "AGENTS.md",
    "TOOLS.md",
    "README.md",
]

# LLM 自我介绍检测关键词 — 命中任一即视为违规输出
# 精确字符串匹配 + 全小写。只应命中典型的模型自我介绍话术，
# 不能拦截正常的工具驱动回复（如 "我帮你读取文件..."）
_ROLEPLAY_TRIGGERS: List[str] = [
    # Agnes 特定
    "我是 agnes", "agnes-2.0", "agnes-2", "sapiens ai", "由 sapiens",
    # 其他模型
    "我是 deepseek", "deepseek 开发", "deepseek-chat",
    "我是 groq", "groq 开发",
    "我是 openai", "由 openai 开发",
    "我是 qwen", "我是 glm", "我是 doubao",
    # 通用"我是 AI"类型的自我介绍 — 注意不要匹配 "我是 superclaw"
    "我是一个人工智能", "作为一个人工智能助手", "作为一个 ai 助手",
    # "无法访问"类型的拒答话术
    "我无法查看或访问自己的内部", "无法访问自己的内部系统",
    "我没有权限访问或读取任何外部", "无法访问或读取任何外部",
    "我只能基于训练数据", "只能基于训练数据回答",
    # 典型的能力清单开头
    "我可以为你提供准确", "可以为你提供准确",
    "我能帮你解答", "解答各类知识性问题",
    # 旧版 superclaw 的错误角色
    "一个具备进化能力的 ai agent",
]

def _looks_like_roleplay(text: str) -> bool:
    """检测 LLM 是否在输出角色扮演/自我介绍 —— 真 LLM 才会出现此问题。"""
    if not text:
        return False
    low = text.lower()
    for t in _ROLEPLAY_TRIGGERS:
        if t in low:
            return True
    return False


class Agent:
    """superclaw 核心 Agent
    - 可配置 LLM Provider
    - 工具调用循环（LLM -> 工具 -> LLM 迭代）
    - 会话记忆（短期）
    - MD 知识体系（长期记忆）：启动自动加载 SOUL/MEMORY/AGENTS/TOOLS 等
    - 每轮对话前自动做 memory 检索，让 LLm 用已有知识作答
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

        # --- 记忆系统互联 ---
        # 用 tools._workspace 初始化 MemoryStore（避免循环 import）
        self._memory_store = None
        try:
            from .memory import MemoryStore
            self._memory_store = MemoryStore(Path(self.tools._workspace))
        except Exception:
            self._memory_store = None

        # 启动加载核心 MD → 注入 system prompt
        self._loaded_core_md: List[str] = []
        self._autoload_core_md()

        # 自动扫描并加载 skills 目录
        self._autoload_skills()

    # ---- 核心 MD 文件自动加载 ----
    def _autoload_core_md(self) -> None:
        """启动时读取 core MD 文件（SOUL/MEMORY/AGENTS/TOOLS）并注入 system prompt。
        让 LLM 从一开始就知道自己的身份与知识体系，而不是凭空自我介绍。"""
        ws = Path(self.tools._workspace)
        if not ws.exists():
            return

        found_blocks: List[str] = []
        for fname in _CORE_MD_FILES:
            fpath = ws / fname
            if fpath.exists() and fpath.is_file():
                try:
                    text = fpath.read_text(encoding="utf-8", errors="ignore").strip()
                    if text:
                        # 截断过长内容，避免 system prompt 爆炸
                        max_chars = 2000
                        preview = text[:max_chars]
                        if len(text) > max_chars:
                            preview += "\n...[已截断，完整内容可用 memory_read 工具读取]"
                        title = fname
                        for line in text.splitlines()[:1]:
                            if line.startswith("#"):
                                candidate = line.lstrip("#").strip()
                                if candidate:
                                    title = candidate
                                break
                        found_blocks.append(
                            f"## 知识文件: {fname} ({title})\n{preview}"
                        )
                        self._loaded_core_md.append(fname)
                except Exception:
                    continue

        if found_blocks:
            # 将核心 MD 拼接到 system prompt 尾部（在 tool 说明之前）
            joined = "\n\n".join(found_blocks)
            self.system_prompt = (
                f"{self.system_prompt}\n\n===== 你的本地知识体系（只读，用于回答问题）=====\n"
                f"{joined}\n"
                f"===== 本地知识结束。需要更多细节可调用 memory 工具检索 =====\n"
            )

    def _autoload_skills(self) -> None:
        """自动扫描 skills 目录并加载所有 .md skill 文件"""
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

    # ---- 每轮对话前：memory 检索 + 对话写入记忆 ----
    def _retrieve_relevant_memory(self, user_input: str) -> Optional[str]:
        """用用户输入检索本地 md 知识/反思，返回简短摘要。
        失败或无匹配返回 None，不阻塞主流程。"""
        if not self.tools.has("memory"):
            return None
        try:
            result = self.tools.call("memory", query=user_input)
            content = getattr(result, "content", str(result))
            if not content:
                return None
            # 无命中信号："未找到 xxx 相关知识"（检索失败） / "暂无 xxx"（无反思记录）
            # 这些内容不应该注入消息，避免 LLM 被误引导
            has_real_result = True
            stripped = content.strip()
            # 单行的"未找到"提示视为无结果
            if "未找到" in stripped and "相关" in stripped and len(stripped) < 200:
                has_real_result = False
            if ("暂无" in stripped or "无相关" in stripped) and len(stripped) < 200:
                has_real_result = False
            if not has_real_result:
                return None
            snippet = content[:600]
            if len(content) > 600:
                snippet += "..."
            return snippet
        except Exception:
            return None

    def _write_turn_to_memory(self, session_key: str,
                              user_input: str, assistant_reply: str) -> None:
        """将本轮对话写入记忆系统：在 memory/<session_key>.md 追加一条日记。
        实现 session ↔ memory 的真实互联。"""
        try:
            ws = Path(self.tools._workspace)
            mem_dir = ws / "memory"
            mem_dir.mkdir(parents=True, exist_ok=True)
            log_file = mem_dir / f"{session_key}.md"
            from datetime import datetime
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            entry = (
                f"\n### 对话记录 @ {ts}\n"
                f"- 用户: {user_input[:400]}\n"
                f"- 助手: {str(assistant_reply)[:400]}\n"
            )
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(entry)
        except Exception:
            pass

    def run(self, user_input: str, session_key: str = "default",
            *, verbose: bool = False, max_iterations: Optional[int] = None) -> AgentResult:
        """处理用户输入，运行 Agent 循环
        流程（参考 nanobot）:
        1. 反馈学习检测
        2. memory 检索 → 注入消息（让 LLM 用本地 md 知识作答）
        3. user_input → LLM 决定是否调用工具
        4. 工具调用迭代（think + 实际工具）
        5. 对话写入 memory 实现长期记忆
        6. 返回总结后的自然语言答案
        """
        t0 = time.time()

        if not user_input.strip():
            return AgentResult(content="你说什么？")

        # ---- 用户反馈采集（可选）----
        if self.feedback_learner is not None:
            try:
                self.feedback_learner.detect_and_record(
                    user_input, session_id=session_key,
                )
            except Exception:
                pass  # 反馈采集失败不阻断主流程

        # ---- 第一步：memory 检索（session ↔ memory 互联）----
        memory_hint = ""
        memory_snippet = self._retrieve_relevant_memory(user_input)
        if memory_snippet:
            memory_hint = (
                "\n\n===== 从本地 md 知识体系检索到的相关内容（用于回答）=====\n"
                f"{memory_snippet}\n"
                "===== 检索结束。优先基于以上内容回答，需要更多细节可调用 memory 工具 =====\n"
            )

        # 获取会话
        session = self.sessions.get(session_key)
        session.add("user", user_input)

        # 构建消息：system prompt + 本地知识注入 + tool 说明
        tool_hint = ""
        if self.tools.names:
            tool_hint = "\n\n" + self.tools.to_llm_instructions()

        system_prompt = self.system_prompt + memory_hint + tool_hint
        messages = session.to_messages(system_prompt)

        result = AgentResult(content="", tools_used=[], tool_outputs=[])
        max_it = max_iterations or self.max_tool_iterations

        # 工具调用循环 — 类似 nanobot runner
        roleplay_fix_count = 0  # 防御：角色违规重试计数
        for i in range(max_it):
            # 调用 LLM
            llm_output = self.provider.call(messages)
            result.iterations = i + 1

            if verbose:
                print(f"[turn {i+1}] LLM: {llm_output[:200]}")

            # 检查是否有工具调用
            tool_call = _parse_tool_call(llm_output)
            if not tool_call:
                # 没有工具调用 —— 先检查是不是角色自我介绍违规
                if _looks_like_roleplay(llm_output) and roleplay_fix_count < 2:
                    # 检测到 LLM 在输出"我是 Agnes / 无法访问内部"等角色扮演
                    # 不把它当成最终答案，注入错误消息后强制重试
                    roleplay_fix_count += 1
                    warning = (
                        f"[系统警告 #{roleplay_fix_count}] 检测到你输出了模型自我介绍/角色扮演内容。"
                        f"请严格遵守 system prompt 的身份定义：你不是任何第三方模型，"
                        f"而是 superclaw 的本地代码执行工具。请重新回答用户问题：{user_input}"
                        f" —— 要么调用工具，要么直接用中文回答。"
                    )
                    session.add("assistant", llm_output)
                    session.add("tool", warning)
                    messages.append({"role": "assistant", "content": llm_output})
                    messages.append({"role": "user", "content": warning})
                    if verbose:
                        print(f"  ✋ 检测到角色违规，强制重试 #{roleplay_fix_count}")
                    continue

                # 正常的最终答案
                final_answer = llm_output.strip()
                session.add("assistant", final_answer)
                result.content = final_answer
                # ---- session → memory 写入：互联闭环 ----
                self._write_turn_to_memory(session_key, user_input, final_answer)
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
            # session → memory 写入
            self._write_turn_to_memory(session_key, user_input, final.strip())
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
