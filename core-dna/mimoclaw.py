#!/usr/bin/env python3
"""
MiMoClaw 统一 CLI — 贯穿 C core + Rust + Python 三层
用法: python3 mimoclaw.py <command> [args]

命令列表：
  status    — 系统状态（三层全覆盖）
  evolve    — 触发进化
  health    — 健康检查
  search    — 搜索知识
  service   — 民生服务
  audit     — GPT 审计
  genome    — Genome 进化操作
  share     — GEP 基因共享协议
  human     — 数字人交互
"""

import json
import subprocess
import sys
import os
import time
import select
from pathlib import Path

CORE_DIR = Path("/home/.openclaw/workspace/core-dna")
MEMORY_DIR = Path("/home/.openclaw/workspace/memory")

# ===================== 底层通信 =====================

def run_c_core(cmd):
    """调用 C core — 发送命令并读取响应"""
    proc = subprocess.Popen(
        [str(CORE_DIR / "c-core")],
        cwd=str(CORE_DIR),
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=False
    )
    try:
        import select as sel
        import os
        # 写入命令（不关闭 stdin，让 C core 自己读取）
        try:
            proc.stdin.write((cmd + "\n").encode())
            proc.stdin.flush()
        except (BrokenPipeError, OSError):
            pass

        # 等待输出：C core 的 status 命令会输出后继续运行，
        # 我们读取输出直到看到特定结束标记或超时
        output = b""
        deadline = time.time() + 10
        while time.time() < deadline:
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            ready, _, _ = sel.select([proc.stdout], [], [], min(remaining, 1.0))
            if ready:
                chunk = os.read(proc.stdout.fileno(), 4096)
                if not chunk:
                    break
                output += chunk
                # 检查是否已读到完整的 status 输出
                text = output.decode("utf-8", errors="replace")
                if "=== C Core Status ===" in text and "适应度:" in text and "健康度:" in text:
                    # 已收到完整的 status 输出
                    break
            else:
                if output:
                    break

        # 发送 quit 退出 C core
        try:
            proc.stdin.write(b"quit\n")
            proc.stdin.flush()
            proc.stdin.close()
        except (BrokenPipeError, OSError):
            pass

        # 等待进程退出
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=3)

        return output.decode("utf-8", errors="replace").strip()
    except Exception as e:
        return f"错误: {e}"
    finally:
        try:
            proc.kill()
        except Exception:
            pass

def run_rust_engine(json_cmd):
    """调用 Rust 引擎"""
    proc = subprocess.Popen(
        [str(CORE_DIR / "rust-engine")],
        cwd=str(CORE_DIR),
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=False
    )
    stdout, _ = proc.communicate(input=(json_cmd + "\n").encode(), timeout=10)
    return stdout.decode("utf-8", errors="replace").strip()

def run_python_module(module, args=None):
    """调用 Python 模块"""
    cmd = ["python3", str(CORE_DIR / module)]
    if args:
        cmd.extend(args)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    return result.stdout if result.returncode == 0 else f"错误: {result.stderr[:200]}"

def _import_gep():
    """延迟导入 GEP"""
    sys.path.insert(0, str(CORE_DIR))
    from gene_sharing import GeneSharingProtocol
    return GeneSharingProtocol()

def _import_dh():
    """延迟导入数字人"""
    sys.path.insert(0, str(CORE_DIR))
    from digital_human import DigitalHuman
    return DigitalHuman()

# ===================== 命令实现 =====================

def cmd_status():
    """查看系统状态"""
    print("=== MiMoClaw 系统状态 ===\n")

    # C core
    print("🫀 C core:")
    try:
        resp = run_c_core("status")
        # 提取 Status 部分（跳过启动信息）
        if "=== C Core Status ===" in resp:
            status_start = resp.index("=== C Core Status ===")
            resp = resp[status_start:]
        print(f"  {resp[:200]}")
    except Exception as e:
        print(f"  ❌ 无法连接: {e}")

    # Rust
    print("\n💪 Rust:")
    try:
        resp = run_rust_engine('{"cmd":"status"}')
        d = json.loads(resp)
        print(f"  基因数: {d.get('gene_count', 0)}")
        print(f"  平衡度: {d.get('balance', 0):.3f}")
        print(f"  代数: {d.get('cycle', 0)}")
    except Exception as e:
        print(f"  ❌ 无法连接: {e}")

    # GEP
    print("\n🔗 GEP 基因共享:")
    try:
        gep = _import_gep()
        stats = gep.stats()
        print(f"  私有库: {stats['private_count']} 个 (均分 {stats['private_avg_score']:.1f})")
        print(f"  公共库: {stats['public_count']} 个 (均分 {stats['public_avg_score']:.1f})")
    except Exception as e:
        print(f"  ❌ {e}")

    # 数字人
    print("\n🤖 数字人:")
    try:
        dh = _import_dh()
        print(f"  名称: {dh.name}")
        print(f"  交互次数: {len(dh.context)}")
    except Exception as e:
        print(f"  ❌ {e}")

    # 系统资源
    print("\n📊 系统资源:")
    r = subprocess.run(["df", "-h", "/"], capture_output=True, text=True)
    print(f"  磁盘: {r.stdout.strip().split(chr(10))[-1]}")
    r = subprocess.run(["free", "-h"], capture_output=True, text=True)
    print(f"  内存: {r.stdout.strip().split(chr(10))[1]}")


def cmd_evolve():
    """触发进化"""
    print("🧬 触发自进化...\n")

    # Rust 进化
    print("--- Rust 进化引擎 ---")
    try:
        resp = run_rust_engine('{"cmd":"status"}')
        d = json.loads(resp)
        print(f"  当前: {d.get('gene_count', 0)} 基因, 平衡度 {d.get('balance', 0):.3f}")

        resp2 = run_rust_engine('{"cmd":"mutate","domain":"auto","change":0.3}')
        d2 = json.loads(resp2)
        print(f"  变异后: {d2.get('genes', 0)} 基因, 平衡度 {d2.get('balance', 0):.3f}")
    except Exception as e:
        print(f"  ❌ {e}")

    # Python 自进化
    print("\n--- Python 自进化 ---")
    try:
        out = run_python_module("self-evolve.py")
        if out.strip():
            print(f"  {out.strip()[:200]}")
    except Exception as e:
        print(f"  ❌ {e}")

    print("\n✅ 进化完成")


def cmd_health():
    """健康检查"""
    print("🏥 健康检查...\n")

    # 磁盘
    r = subprocess.run(["df", "-h", "/"], capture_output=True, text=True)
    print(f"磁盘: {r.stdout.strip().split(chr(10))[-1]}")

    # 内存
    r = subprocess.run(["free", "-h"], capture_output=True, text=True)
    print(f"内存: {r.stdout.strip().split(chr(10))[1]}")

    # 关键文件
    critical = [WORKSPACE / "SOUL.md", WORKSPACE / "AGENTS.md"]
    for f in critical:
        status = "✅" if f.exists() else "❌"
        print(f"  {status} {f.name}")

    # 三层进程
    print("\n三层进程:")
    for name, proc_name in [("C core", "c-core"), ("Rust", "rust-engine")]:
        try:
            r = subprocess.run(["pgrep", "-f", proc_name], capture_output=True, text=True)
            pid = r.stdout.strip().split("\n")[0] if r.stdout.strip() else "未运行"
            print(f"  {name}: PID={pid}")
        except Exception:
            print(f"  {name}: 状态未知")


def cmd_search(query):
    """搜索知识"""
    print(f"🔍 搜索: {query}")
    # 本地知识库搜索
    memory_files = list(MEMORY_DIR.glob("*.md"))
    results = []
    for mf in memory_files[:10]:
        try:
            content = mf.read_text(encoding="utf-8")
            if query.lower() in content.lower():
                results.append(str(mf.name))
        except Exception:
            pass
    if results:
        print(f"  在 {len(results)} 个文件中找到匹配:")
        for r in results:
            print(f"    📄 {r}")
    else:
        print("  未找到匹配内容")


def cmd_service():
    """民生服务"""
    print("📱 民生服务模块")
    script = CORE_DIR.parent / "scripts/daily-service.py"
    if script.exists():
        subprocess.run(["python3", str(script)], cwd=str(CORE_DIR.parent))
    else:
        print("  daily-service.py 未安装，使用本地模式")
        # 提供基础服务信息
        from datetime import datetime
        now = datetime.now()
        print(f"  当前时间: {now.strftime('%Y-%m-%d %H:%M')}")
        print(f"  🌤️  天气查询: python3 mimoclaw.py human chat '今天天气怎么样'")
        print(f"  🍜 美食推荐: python3 mimoclaw.py human chat '推荐今天的晚餐'")


def cmd_audit():
    """审计"""
    print("🔍 系统审计...")
    script = CORE_DIR.parent / "scripts/gpt-audit.py"
    if script.exists():
        subprocess.run(["python3", str(script)], cwd=str(CORE_DIR.parent))
    else:
        # 基础审计
        print("\n--- 安全边界检查 ---")
        forbidden = [
            "~/.openclaw/openclaw.json",
            "~/.openclaw/openclaw.json.bak",
        ]
        for path in forbidden:
            expanded = os.path.expanduser(path)
            exists = os.path.exists(expanded)
            print(f"  {'✅ 保护中' if exists else '⚠️ 不存在'}: {path}")

        print("\n--- 基因库完整性 ---")
        gene_files = [
            CORE_DIR / "genome_public.json",
            CORE_DIR / "genome_private.json",
        ]
        for gf in gene_files:
            if gf.exists():
                try:
                    data = json.loads(gf.read_text())
                    count = len(data) if isinstance(data, list) else 0
                    print(f"  ✅ {gf.name}: {count} 个基因")
                except Exception:
                    print(f"  ⚠️ {gf.name}: 格式异常")
            else:
                print(f"  ℹ️ {gf.name}: 不存在")

        print("\n✅ 基础审计完成")


def cmd_genome(args):
    """Genome 进化操作"""
    print("🧬 Genome 进化引擎\n")

    if not args:
        resp = run_rust_engine('{"cmd":"status"}')
        try:
            d = json.loads(resp)
            print(f"  代数: {d.get('cycle', 0)}")
            print(f"  基因数: {d.get('gene_count', 0)}")
            print(f"  平衡度: {d.get('balance', 0):.3f}")
            print(f"  变异率: {d.get('mutation_rate', 0)}")
        except Exception:
            print(f"  {resp[:200]}")
        return

    subcmd = args[0]

    if subcmd == "status":
        resp = run_rust_engine('{"cmd":"status"}')
        try:
            d = json.loads(resp)
            print(json.dumps(d, indent=2, ensure_ascii=False))
        except Exception:
            print(resp[:500])

    elif subcmd == "mutate":
        domain = args[1] if len(args) > 1 else "auto"
        change = float(args[2]) if len(args) > 2 else 0.3
        resp = run_rust_engine(json.dumps({"cmd": "mutate", "domain": domain, "change": change}))
        try:
            print(json.dumps(json.loads(resp), indent=2, ensure_ascii=False))
        except Exception:
            print(resp[:500])

    elif subcmd == "learn":
        resp = run_rust_engine('{"cmd":"learn"}')
        try:
            print(json.dumps(json.loads(resp), indent=2, ensure_ascii=False))
        except Exception:
            print(resp[:500])

    elif subcmd == "forget":
        threshold = float(args[1]) if len(args) > 1 else 0.1
        resp = run_rust_engine(json.dumps({"cmd": "forget", "threshold": threshold}))
        try:
            print(json.dumps(json.loads(resp), indent=2, ensure_ascii=False))
        except Exception:
            print(resp[:500])

    elif subcmd == "save":
        resp = run_rust_engine('{"cmd":"save"}')
        try:
            print(json.dumps(json.loads(resp), indent=2, ensure_ascii=False))
        except Exception:
            print(resp[:500])

    elif subcmd == "evaluate" or subcmd == "eval":
        # 通过 self-evolve.py 评估
        out = run_python_module("self-evolve.py")
        if out.strip():
            try:
                data = json.loads(out.strip())
                print(json.dumps(data, indent=2, ensure_ascii=False))
            except Exception:
                print(out.strip()[:300])
        else:
            print("无评估结果")

    elif subcmd == "history":
        # 查看进化历史
        evo_log = MEMORY_DIR / "evolution-history.jsonl"
        if evo_log.exists():
            lines = evo_log.read_text().strip().split("\n")
            limit = int(args[1]) if len(args) > 1 else 10
            print(f"进化历史 (最近 {min(limit, len(lines))} 条):\n")
            for line in lines[-limit:]:
                try:
                    entry = json.loads(line)
                    ts = entry.get("timestamp", 0)
                    etype = entry.get("type", "?")
                    rnd = entry.get("round", "?")
                    print(f"  [{ts}] {etype} (轮次 {rnd})")
                except Exception:
                    print(f"  {line[:80]}")
        else:
            print("暂无进化历史")

    else:
        print(f"未知子命令: {subcmd}")
        print("可用: status, mutate [domain] [change], learn, forget [threshold], save, eval, history [limit]")


def cmd_share(args):
    """GEP 基因共享协议"""
    print("🔗 GEP 基因共享协议\n")

    gep = _import_gep()

    if not args:
        stats = gep.stats()
        print(json.dumps(stats, indent=2, ensure_ascii=False))
        return

    subcmd = args[0]

    if subcmd == "stats":
        print(json.dumps(gep.stats(), indent=2, ensure_ascii=False))

    elif subcmd == "pull":
        model = args[1] if len(args) > 1 else None
        results = gep.pull(source="all", model=model, top_k=5)
        if results:
            for r in results:
                print(f"  [{r.get('id','?')[:8]}] {r.get('model','?')} score={r.get('score',0):.1f}")
        else:
            print("  暂无可拉取的基因")

    elif subcmd == "publish":
        g = {
            "prompt_strategy": "chain-of-thought",
            "toolchain": ["search", "python"],
            "skills": ["reasoning", "coding"],
            "model": args[1] if len(args) > 1 else "test",
            "score": 80.0,
        }
        result = gep.publish(g, visibility="public", tags=["cli-test"])
        print(f"  已发布: {result.get('id', '?')[:8]}")

    elif subcmd == "merge":
        merged = gep.merge_pool(count=3)
        if merged:
            print(json.dumps(merged, indent=2, ensure_ascii=False))
        else:
            print("  公共库基因不足，无法合并")

    elif subcmd == "tags":
        tags = gep.list_tags()
        for tag, count in sorted(tags.items(), key=lambda x: -x[1]):
            print(f"  {tag}: {count}")

    elif subcmd == "cleanup":
        removed = gep.cleanup()
        print(f"  清理了 {removed} 个低分基因")

    else:
        print(f"未知子命令: {subcmd}")
        print("可用: stats, pull [model], publish [model], merge, tags, cleanup")


def cmd_human(args):
    """数字人交互"""
    print("🤖 数字人交互\n")

    dh = _import_dh()

    if not args:
        print("输入文本与数字人对话 (输入 quit 退出):")
        while True:
            try:
                text = input("你: ").strip()
                if text.lower() in ("quit", "exit", "q"):
                    break
                if not text:
                    continue
                result = dh.chat(text=text)
                print(f"{dh.name}: {result['response']}")
                print(f"  (意图: {result['intent']['intent']}, 置信度: {result['intent']['confidence']:.2f})")
            except (KeyboardInterrupt, EOFError):
                break
        print("\n再见！")
        return

    subcmd = args[0]

    if subcmd == "chat":
        text = " ".join(args[1:]) if len(args) > 1 else ""
        if not text:
            print("请提供文本: python3 mimoclaw.py human chat <text>")
            return
        result = dh.chat(text=text)
        print(f"{dh.name}: {result['response']}")
        print(f"  意图: {result['intent']['intent']} (置信度: {result['intent']['confidence']:.2f})")

    elif subcmd == "stats":
        print(json.dumps(dh.get_stats(), indent=2, ensure_ascii=False))

    elif subcmd == "voice":
        audio = args[1] if len(args) > 1 else "test.wav"
        result = dh.chat(audio_path=audio)
        print(f"{dh.name}: {result['response']}")

    elif subcmd == "clear":
        dh.clear_context()
        print("  上下文已清空")

    else:
        print(f"未知子命令: {subcmd}")
        print("可用: chat <text>, voice [file], stats, clear")
        print("无参数启动交互模式: python3 mimoclaw.py human")


# ===================== 帮助 =====================

def cmd_help():
    print("""
MiMoClaw 统一 CLI — SuperClaw 系统控制器

用法: python3 mimoclaw.py <command> [args]

核心命令:
  status              查看系统状态（三层全覆盖）
  evolve              触发自进化
  health              健康检查
  search <query>      搜索知识
  service             民生服务
  audit               系统审计

Genome 进化:
  genome              查看 Genome 状态
  genome status       详细状态
  genome mutate       触发变异 (domain change)
  genome learn        从历史学习
  genome forget       遗忘弱基因 (threshold)
  genome save         保存基因库
  genome eval         进化评估
  genome history      进化历史

GEP 基因共享:
  share               查看统计
  share stats         详细统计
  share pull [model]  拉取基因
  share publish       发布基因
  share merge         合并基因
  share tags          查看标签
  share cleanup       清理低分

数字人交互:
  human               进入交互模式
  human chat <text>   单次对话
  human voice [file]  语音交互
  human stats         交互统计
  human clear         清空上下文

帮助:
  help                显示此帮助
""")


# ===================== 主入口 =====================

def main():
    if len(sys.argv) < 2:
        cmd_help()
        return

    cmd = sys.argv[1]
    args = sys.argv[2:] if len(sys.argv) > 2 else []

    commands = {
        "status": cmd_status,
        "evolve": cmd_evolve,
        "health": cmd_health,
        "search": lambda: cmd_search(args[0] if args else ""),
        "service": cmd_service,
        "audit": cmd_audit,
        "help": cmd_help,
    }

    if cmd in commands:
        commands[cmd]()
    elif cmd == "genome":
        cmd_genome(args)
    elif cmd == "share":
        cmd_share(args)
    elif cmd == "human":
        cmd_human(args)
    else:
        print(f"未知命令: {cmd}")
        cmd_help()


if __name__ == "__main__":
    WORKSPACE = Path("/home/.openclaw/workspace")
    main()
