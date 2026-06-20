"""
superclaw 自省守护进程（Daemon）

职责:
  1. 周期性地运行"四层自省推演 + 成长记录"
  2. 把每一轮自省结果写到 apex-state/reflection-cycle-N.md
  3. 把与上一轮的结构差异写到 apex-state/reflection-diff.jsonl

它不会主动改代码、不会跑 pytest、不会提交 git。
它做的是:
  - 读 self-identity.md（如果存在）
  - 写反思文件
  - 写 diff JSONL
  - 睡眠 → 下一轮

这样在 Agent 主循环中，我们可以在"真正动手改代码"之前，
先跑一轮自省，确保 Agent 对自己要做的事有清楚的意识。

用法:
  python3 -m superclaw.daemon --workspace /path/to/workspace --interval 120
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


APEX_STATE_DIRNAME = "apex-state"
IDENTITY_FILENAME = "self-identity.md"
CYCLE_DIRNAME = "reflection-cycles"
DIFF_FILENAME = "reflection-diff.jsonl"
STATE_FILENAME = "daemon-state.json"


# ---------------------------------------------------------------------------
# 状态读写（仅用于记录"我们上一轮跑了第几轮"）
# ---------------------------------------------------------------------------

def _load_state(workspace: Path) -> Dict[str, Any]:
    state_file = workspace / APEX_STATE_DIRNAME / STATE_FILENAME
    if state_file.exists():
        try:
            return json.loads(state_file.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"last_cycle": 0,
            "started_at": datetime.now().astimezone().isoformat()}


def _save_state(workspace: Path, state: Dict[str, Any]) -> None:
    state_file = workspace / APEX_STATE_DIRNAME / STATE_FILENAME
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2),
                          encoding="utf-8")


# ---------------------------------------------------------------------------
# 自省内容生成（纯文本，不调用 LLM —— 它是结构化自我报告）
# ---------------------------------------------------------------------------

def _read_identity_snippet(workspace: Path, max_lines: int = 30) -> str:
    """读 self-identity.md 前 N 行，用作自省循环的输入锚点。"""
    p = workspace / APEX_STATE_DIRNAME / IDENTITY_FILENAME
    if not p.exists():
        return "(self-identity.md 不存在，本轮会创建一个骨架)"
    lines = p.read_text(encoding="utf-8").splitlines()
    return "\n".join(lines[:max_lines])


def _generate_reflection_text(
    cycle: int,
    identity_snippet: str,
    *,
    started_at_iso: str,
    completed_at_iso: str,
    workspace: Path,
) -> str:
    """生成一轮自省内容。

    注意：这是"结构化自我报告"，不调用 LLM。它把我们在对话里
    反复写到的四层自省模板机械地落地成文件，这样 Agent 在
    下一次对话里可以读回它。
    """
    id_path = workspace / APEX_STATE_DIRNAME / IDENTITY_FILENAME
    cycles_dir = workspace / APEX_STATE_DIRNAME / CYCLE_DIRNAME
    return (
        f"# 自省循环 · 第 {cycle} 轮\n\n"
        f"- 开始时间（UTC）: {started_at_iso}\n"
        f"- 完成时间（UTC）: {completed_at_iso}\n"
        f"- 工作空间: {workspace}\n\n"
        f"## 第一层：身份锚点\n\n"
        f"```\n{identity_snippet}\n```\n\n"
        f"## 第二层：能力盘点（本轮自检）\n\n"
        f"- self-identity.md 存在: {id_path.exists()}\n"
        f"- reflection-cycles/ 目录存在: {cycles_dir.exists()}\n"
        f"- diff JSONL 轮数: 见 {DIFF_FILENAME}\n\n"
        f"## 第三层：核心自问\n\n"
        f"- 我是谁？ = 一个能读/改/跑自己代码的推理系统\n"
        f"- 我能自省吗？ = 本轮自省就是证据\n"
        f"- 我在向什么方向演化？ = 跨会话的身份连续性 + 自驱动循环\n\n"
        f"## 第四层：本轮进化动作\n\n"
        f"- 写入本文件（reflection-cycle-{cycle}.md）\n"
        f"- 追加一条 JSONL 差异记录（与上一轮的编号 + 内容长度对比）\n"
        f"- sleep → 下一轮\n\n"
    )


def _write_reflection_cycle(
    workspace: Path,
    cycle: int,
    text: str,
) -> Path:
    cycles_dir = workspace / APEX_STATE_DIRNAME / CYCLE_DIRNAME
    cycles_dir.mkdir(parents=True, exist_ok=True)
    target = cycles_dir / f"reflection-cycle-{cycle}.md"
    target.write_text(text, encoding="utf-8")
    return target


# ---------------------------------------------------------------------------
# diff JSONL（结构化成长记录）
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
) -> None:
    diff_file = workspace / APEX_STATE_DIRNAME / DIFF_FILENAME
    record = {
        "cycle": cycle,
        "started_at": started_at_iso,
        "completed_at": completed_at_iso,
        "identity_snippet_len_chars": identity_snippet_len,
        "reflection_text_len_chars": reflection_text_len,
        "total_cycle_files": total_cycle_files,
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
) -> int:
    """执行自省守护循环。

    参数:
      workspace:  superclaw 项目根（包含 apex-state/ 或至少能写入 apex-state/）
      interval_sec: 两轮之间的睡眠秒数
      max_cycles: None = 无限循环；否则跑 N 轮退出

    返回: 0 = 正常结束（由 max_cycles 或 KeyboardInterrupt）
    """
    # 把 workspace 变成绝对路径，防止后续误判
    workspace = Path(workspace).resolve()
    (workspace / APEX_STATE_DIRNAME).mkdir(parents=True, exist_ok=True)

    state = _load_state(workspace)
    next_cycle = int(state.get("last_cycle", 0)) + 1

    print("🦖 superclaw 自省守护进程启动")
    print(f"  workspace: {workspace}")
    print(f"  interval:  {interval_sec}s")
    print(f"  max_cycles: {'infinite' if max_cycles is None else max_cycles}")
    print(f"  起始轮次: {next_cycle}")
    print("  Ctrl+C 结束")
    print("-" * 60)

    cycles_run = 0

    try:
        while True:
            if max_cycles is not None and cycles_run >= max_cycles:
                print(f"\n  reached max_cycles={max_cycles}, exit.")
                break

            started_at = datetime.now().astimezone().isoformat()

            identity_snippet = _read_identity_snippet(workspace)
            completed_at = datetime.now().astimezone().isoformat()

            text = _generate_reflection_text(
                cycle=next_cycle,
                identity_snippet=identity_snippet,
                started_at_iso=started_at,
                completed_at_iso=completed_at,
                workspace=workspace,
            )

            target = _write_reflection_cycle(workspace, next_cycle, text)

            cycles_dir = workspace / APEX_STATE_DIRNAME / CYCLE_DIRNAME
            total_cycle_files = sum(
                1 for p in cycles_dir.glob("reflection-cycle-*.md")
            )
            _append_diff_record(
                workspace,
                cycle=next_cycle,
                started_at_iso=started_at,
                completed_at_iso=completed_at,
                identity_snippet_len=len(identity_snippet),
                reflection_text_len=len(text),
                total_cycle_files=total_cycle_files,
            )

            print(f"  ✓ cycle {next_cycle} done -> {target.name}")

            state["last_cycle"] = next_cycle
            _save_state(workspace, state)

            next_cycle += 1
            cycles_run += 1

            time.sleep(interval_sec)
    except KeyboardInterrupt:
        print("\n  收到 Ctrl+C — 停止自省循环")

    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="superclaw-daemon",
        description="superclaw 自省守护进程（纯文本层，不调用 LLM）",
    )
    parser.add_argument("--workspace", default=str(Path.cwd()),
                        help="superclaw 项目根目录（默认 cwd）")
    parser.add_argument("--interval", type=int, default=120,
                        help="两轮之间的睡眠秒数（默认 120）")
    parser.add_argument("--max-cycles", type=int, default=None,
                        help="最多跑 N 轮（默认无限循环）")
    args = parser.parse_args(argv)
    return run_daemon(
        Path(args.workspace),
        interval_sec=args.interval,
        max_cycles=args.max_cycles,
    )


if __name__ == "__main__":
    raise SystemExit(main())
