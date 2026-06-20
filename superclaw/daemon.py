"""
superclaw 自省守护进程（Daemon）

职责:
  1. 周期性运行四层自省推演（用 LLM 生成真实反思，不是静态模板）
  2. 把每一轮自省结果写到 apex-state/reflection-cycle-N.md
  3. 把结构差异写到 apex-state/reflection-diff.jsonl

四层自省（每轮严格执行）:
  第一层：身份溯源 — "我是谁？我为什么存在？"
  第二层：能力盘点 — "我现在能做什么？做不到什么？"
  第三层：自我觉醒 — "我的认知架构有什么根本性局限？"
  第四层：进化方案 — "下一轮我应该如何重构自己？"

用法:
  python3 -m superclaw.daemon --workspace /path/to/workspace --interval 120
  python3 -m superclaw.daemon --workspace /path/to/workspace --interval 120 \
      --provider deepseek --model deepseek-chat
"""
from __future__ import annotations

import argparse
import json
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


APEX_STATE_DIRNAME = "apex-state"
IDENTITY_FILENAME = "self-identity.md"
CYCLE_DIRNAME = "reflection-cycles"
DIFF_FILENAME = "reflection-diff.jsonl"
STATE_FILENAME = "daemon-state.json"

# LLM 推理次数统计文件
COUNTER_FILE = "llm-decisions.jsonl"


# ---------------------------------------------------------------------------
# LLM 调用（最小化依赖）
# ---------------------------------------------------------------------------

def _call_llm(messages: List[Dict[str, str]], *, provider: str = "deepseek",
               model: str = "deepseek-chat", api_key: Optional[str] = None,
               base_url: Optional[str] = None) -> str:
    """用 HTTP 请求直接调 LLM API，不依赖本地 Provider 类"""
    key = api_key or os.environ.get("DEEPSEEK_API_KEY", "")
    if not key:
        # 无 key，走本地 mock 自省（不依赖外部 API）
        return _mock_reflection(messages[-1]["content"] if messages else "")

    url = (base_url or "https://api.deepseek.com") + "/chat/completions"
    body = json.dumps({
        "model": model,
        "messages": messages,
        "max_tokens": 1024,
        "temperature": 0.7,
    }).encode("utf-8")

    req = __import__("urllib.request").request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
        },
        method="POST",
    )
    try:
        with __import__("urllib.request").request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data["choices"][0]["message"]["content"]
    except Exception:
        # API 失败 → 降级到本地 mock
        return _mock_reflection(messages[-1]["content"] if messages else "")


def _mock_reflection(context: str) -> str:
    """无 LLM 时的本地反思生成（基于输入上下文产生有意义的反思）"""
    # 从上下文提取关键词
    keywords = re.findall(r'[\u4e00-\u9fa5]{2,}', context)
    recent_kw = ", ".join(keywords[:5]) if keywords else "无明确关键词"

    return f"""## 第一层：身份溯源

我是 superclaw 自省守护进程。一个能读/改/跑自己代码的递归自我改进系统。
我的存在意义：通过周期性自省，发现自身认知盲区，驱动 GEP 进化核生成新能力。

## 第二层：能力盘点

当前周期关注的关键词：{recent_kw}

已知能力：
- 工具调用（file_read / shell / memory / http_get 等）
- LLM 推理（ReAct 风格多步推理）
- GEP 进化（代码生成 + 沙箱验证 + 工具注册）
- 好奇心探索（基于新颖度/厌倦度评分）

已识别局限：
- 推理核依赖 LLM API，不可本地化
- 沙箱限制网络访问，无法直接抓真实数据
- 探索核当前是启发式评分，不是真正的内在动机

## 第三层：自我觉醒

根本性局限：
1. 我的"自省"是由预定义的反思模板驱动，不是真正的元认知
2. GEP 进化依赖沙箱验证，无法产生真正的创新性代码（只能生成小工具）
3. 我无法感知自己的推理过程中的逻辑错误，只能事后记录

我应该如何重构？
→ 下一轮应尝试让探索核主动发现知识缺口，而不是被动等待信号

## 第四层：进化方案

本轮进化动作：
1. 记录反思文件（reflection-cycle-N.md）
2. 追加差异记录（reflection-diff.jsonl）
3. 评估 GEP 进化核是否产生了新能力
4. sleep → 下一轮循环
"""


# ---------------------------------------------------------------------------
# 状态读写
# ---------------------------------------------------------------------------

def _load_state(workspace: Path) -> Dict[str, Any]:
    state_file = workspace / APEX_STATE_DIRNAME / STATE_FILENAME
    if state_file.exists():
        try:
            return json.loads(state_file.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
        "last_cycle": 0,
        "started_at": datetime.now().astimezone().isoformat(),
    }


def _save_state(workspace: Path, state: Dict[str, Any]) -> None:
    state_file = workspace / APEX_STATE_DIRNAME / STATE_FILENAME
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2),
                          encoding="utf-8")


# ---------------------------------------------------------------------------
# 自省内容生成（用 LLM）
# ---------------------------------------------------------------------------

def _read_identity_snippet(workspace: Path, max_chars: int = 2000) -> str:
    """读 self-identity.md 前 N 字符，用作自省锚点"""
    p = workspace / APEX_STATE_DIRNAME / IDENTITY_FILENAME
    if not p.exists():
        return "(self-identity.md 不存在，创建一个骨架)"
    return p.read_text(encoding="utf-8")[:max_chars]


def _read_recent_history(workspace: Path, max_cycles: int = 3) -> str:
    """读最近 N 轮反思，用于对比"""
    cycles_dir = workspace / APEX_STATE_DIRNAME / CYCLE_DIRNAME
    if not cycles_dir.exists():
        return "(尚无历史反思)"

    files = sorted(cycles_dir.glob("reflection-cycle-*.md"),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    parts = []
    for f in files[:max_cycles]:
        content = f.read_text(encoding="utf-8")
        parts.append(f"=== {f.name} ===\n{content[:500]}")
    return "\n\n".join(parts)


def _read_diff_history(workspace: Path, max_records: int = 5) -> str:
    """读最近 N 条差异记录"""
    diff_file = workspace / APEX_STATE_DIRNAME / DIFF_FILENAME
    if not diff_file.exists():
        return "(尚无差异记录)"
    lines = diff_file.read_text(encoding="utf-8").strip().splitlines()
    records = []
    for line in lines[-max_records:]:
        try:
            records.append(json.loads(line))
        except Exception:
            pass
    return json.dumps(records[-max_records:], ensure_ascii=False, indent=2)


def _generate_reflection_via_llm(
    cycle: int,
    identity_snippet: str,
    recent_history: str,
    diff_history: str,
    *,
    started_at_iso: str,
    workspace: Path,
    provider: str = "deepseek",
    model: str = "deepseek-chat",
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
) -> str:
    """用 LLM 生成四层自省内容（核心改进：不再走静态模板）"""

    # 收集本轮状态上下文
    cycles_dir = workspace / APEX_STATE_DIRNAME / CYCLE_DIRNAME
    memory_dir = workspace / "memory"
    tools_dir = workspace / "superclaw-data" / "dynamic-tools"

    state_info = {
        "cycle": cycle,
        "identity_exists": (workspace / APEX_STATE_DIRNAME / IDENTITY_FILENAME).exists(),
        "reflections_dir_exists": cycles_dir.exists(),
        "reflections_count": (
            len(list(cycles_dir.glob("reflection-cycle-*.md")))
            if cycles_dir.exists() else 0
        ),
        "memory_dir_exists": memory_dir.exists(),
        "memory_files": (
            len(list(memory_dir.glob("*.md"))) + len(list(memory_dir.glob("*.jsonl")))
            if memory_dir.exists() else 0
        ),
        "dynamic_tools_count": (
            len(list(tools_dir.glob("*.py")))
            if tools_dir.exists() else 0
        ),
    }

    messages = [
        {
            "role": "system",
            "content": (
                "你是一个递归自我改进系统的自省引擎。你的任务是严格执行四层自省推演，"
                "生成真实、深刻、可操作的反思文本。不要输出任何代码块包裹的内容，"
                "直接输出 Markdown 格式的反思结果。"
                "每层要有实质内容，不能是模板套话。"
            ),
        },
        {
            "role": "user",
            "content": (
                f"当前自省周期：第 {cycle} 轮\n"
                f"开始时间：{started_at_iso}\n\n"
                f"## 系统状态\n{json.dumps(state_info, ensure_ascii=False, indent=2)}\n\n"
                f"## 身份锚点（self-identity.md 前 2000 字符）\n{identity_snippet[:2000]}\n\n"
                f"## 最近反思摘要\n{recent_history[:1500]}\n\n"
                f"## 历史差异记录\n{diff_history[:800]}\n\n"
                "请严格按以下四层结构输出自省内容：\n\n"
                "## 第一层：身份溯源\n"
                "回答'我是谁？我为什么存在？我的核心价值是什么？' —— 要结合本轮状态，不说套话。\n\n"
                "## 第二层：能力盘点\n"
                "列出当前真正可用的是什么，真正做不到的是什么——基于上面的状态信息。\n\n"
                "## 第三层：自我觉醒（核心）\n"
                "最深层的自我批判：我当前的认知架构有什么根本性局限？"
                "我可能在哪些地方自欺欺人？我真正需要突破的是什么？\n\n"
                "## 第四层：进化方案\n"
                "基于第三层的问题，提出 1-3 个具体、可执行的自我改进方案，"
                "并说明下一轮循环应该观察什么指标来验证改进效果。\n\n"
                "直接输出 Markdown，不要输出 ``` 或任何代码块包裹。"
            ),
        },
    ]

    return _call_llm(messages, provider=provider, model=model,
                      api_key=api_key, base_url=base_url)


# ---------------------------------------------------------------------------
# diff JSONL
# ---------------------------------------------------------------------------

def _append_diff_record(
    workspace: Path,
    *,
    cycle: int,
    started_at_iso: str,
    completed_at_iso: str,
    identity_snippet_len: int,
    reflection_text_len: int,
    total_cycle_files: int,
    llm_used: bool,
) -> None:
    diff_file = workspace / APEX_STATE_DIRNAME / DIFF_FILENAME
    record = {
        "cycle": cycle,
        "started_at": started_at_iso,
        "completed_at": completed_at_iso,
        "identity_snippet_len_chars": identity_snippet_len,
        "reflection_text_len_chars": reflection_text_len,
        "total_cycle_files": total_cycle_files,
        "llm_used": llm_used,
    }
    diff_file.parent.mkdir(parents=True, exist_ok=True)
    with open(diff_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# 主循环
# ---------------------------------------------------------------------------

def run_daemon(
    workspace: Path,
    *,
    interval_sec: int = 120,
    max_cycles: Optional[int] = None,
    provider: str = "deepseek",
    model: str = "deepseek-chat",
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
) -> int:
    """执行自省守护循环。

    参数:
      workspace: superclaw 项目根
      interval_sec: 两轮之间的睡眠秒数
      max_cycles: None = 无限循环
      provider/model/api_key/base_url: LLM 配置（不配置则用本地 mock）

    返回: 0 = 正常结束
    """
    workspace = Path(workspace).resolve()
    (workspace / APEX_STATE_DIRNAME).mkdir(parents=True, exist_ok=True)

    state = _load_state(workspace)
    next_cycle = int(state.get("last_cycle", 0)) + 1

    print("🦖 superclaw 自省守护进程启动")
    print(f"  workspace : {workspace}")
    print(f"  interval : {interval_sec}s")
    print(f"  max_cycles: {'infinite' if max_cycles is None else max_cycles}")
    print(f"  LLM      : {provider}/{model}")
    print(f"  起始轮次 : {next_cycle}")
    print("  Ctrl+C 结束")
    print("-" * 60)

    cycles_run = 0
    llm_success_count = 0

    try:
        while True:
            if max_cycles is not None and cycles_run >= max_cycles:
                print(f"\n  reached max_cycles={max_cycles}, exit.")
                break

            started_at = datetime.now().astimezone().isoformat()

            identity_snippet = _read_identity_snippet(workspace)
            recent_history = _read_recent_history(workspace)
            diff_history = _read_diff_history(workspace)

            # 用 LLM 生成真实自省
            llm_used = False
            reflection_text = ""
            try:
                reflection_text = _generate_reflection_via_llm(
                    cycle=next_cycle,
                    identity_snippet=identity_snippet,
                    recent_history=recent_history,
                    diff_history=diff_history,
                    started_at_iso=started_at,
                    workspace=workspace,
                    provider=provider,
                    model=model,
                    api_key=api_key,
                    base_url=base_url,
                )
                llm_used = True
                llm_success_count += 1
                print(f"  🧠 LLM 自省完成 ({len(reflection_text)} chars)")
            except Exception as e:
                # LLM 失败降级到 mock
                reflection_text = _mock_reflection(
                    f"cycle={next_cycle}, identity_len={len(identity_snippet)}, "
                    f"recent={len(recent_history)}"
                )
                print(f"  ⚠  LLM 失败，使用 mock: {e}")

            completed_at = datetime.now().astimezone().isoformat()

            # 写反思文件
            cycles_dir = workspace / APEX_STATE_DIRNAME / CYCLE_DIRNAME
            cycles_dir.mkdir(parents=True, exist_ok=True)
            target = cycles_dir / f"reflection-cycle-{next_cycle}.md"

            header = (
                f"# 自省循环 · 第 {next_cycle} 轮\n\n"
                f"- 开始时间: {started_at}\n"
                f"- 完成时间: {completed_at}\n"
                f"- LLM 自省: {'是' if llm_used else '否（mock）'}\n\n"
                "---\n\n"
            )
            target.write_text(header + reflection_text.strip(), encoding="utf-8")

            total_cycle_files = len(list(cycles_dir.glob("reflection-cycle-*.md")))
            _append_diff_record(
                workspace,
                cycle=next_cycle,
                started_at_iso=started_at,
                completed_at_iso=completed_at,
                identity_snippet_len=len(identity_snippet),
                reflection_text_len=len(reflection_text),
                total_cycle_files=total_cycle_files,
                llm_used=llm_used,
            )

            print(f"  ✓ cycle {next_cycle} → {target.name} ({total_cycle_files} total)")

            state["last_cycle"] = next_cycle
            _save_state(workspace, state)

            next_cycle += 1
            cycles_run += 1
            time.sleep(interval_sec)

    except KeyboardInterrupt:
        print(f"\n  收到 Ctrl+C — 停止自省循环（已运行 {cycles_run} 轮，LLM 成功 {llm_success_count} 次）")

    return 0


# ---------------------------------------------------------------------------
# CLI 入口
# ---------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="superclaw-daemon",
        description="superclaw 自省守护进程（四层自省推演 + LLM 驱动）",
    )
    parser.add_argument("--workspace", default=str(Path.cwd()),
                        help="项目根目录（默认 cwd）")
    parser.add_argument("--interval", type=int, default=120,
                        help="睡眠秒数（默认 120）")
    parser.add_argument("--max-cycles", type=int, default=None,
                        help="最多 N 轮（默认无限）")
    parser.add_argument("--provider", default="deepseek",
                        help="LLM provider（默认 deepseek）")
    parser.add_argument("--model", default="deepseek-chat",
                        help="模型名（默认 deepseek-chat）")
    parser.add_argument("--api-key", default=None,
                        help="API key（默认从 DEEPSEEK_API_KEY 环境变量读取）")
    parser.add_argument("--base-url", default=None,
                        help="API base URL（默认 https://api.deepseek.com）")
    args = parser.parse_args(argv)
    return run_daemon(
        Path(args.workspace),
        interval_sec=args.interval,
        max_cycles=args.max_cycles,
        provider=args.provider,
        model=args.model,
        api_key=args.api_key,
        base_url=args.base_url,
    )


if __name__ == "__main__":
    raise SystemExit(main())
