#!/usr/bin/env python3
"""
Zircon CLI — 统一命令行接口
用 JSON 打通所有组件：C core + Rust + Python + 基因库 + 回音壁
"""

import json
import sys
import subprocess
from pathlib import Path
from datetime import datetime

CORE_DIR = Path("/home/.openclaw/workspace/core-dna")
MEMORY_DIR = Path("/root/.openclaw/workspace/memory")

# === 命令注册 ===
COMMANDS = {
    "status": {"desc": "系统状态", "handler": "cmd_status"},
    "genes": {"desc": "基因库状态", "handler": "cmd_genes"},
    "evolve": {"desc": "运行一轮进化", "handler": "cmd_evolve"},
    "search": {"desc": "搜索资源", "handler": "cmd_search"},
    "echo": {"desc": "回音壁增强", "handler": "cmd_echo"},
    "upgrade": {"desc": "自升级检查", "handler": "cmd_upgrade"},
    "balance": {"desc": "洛书平衡", "handler": "cmd_balance"},
    "health": {"desc": "健康检查", "handler": "cmd_health"},
    "daemon": {"desc": "守护进程控制", "handler": "cmd_daemon"},
}

# === 命令实现 ===
def cmd_status(args):
    """系统状态"""
    state_file = MEMORY_DIR / "daemon-state.json"
    if state_file.exists():
        with open(state_file) as f:
            state = json.load(f)
        return {"ok": True, "state": state}
    return {"ok": False, "error": "守护进程未运行"}

def cmd_genes(args):
    """基因库状态"""
    gene_file = MEMORY_DIR / "gene-registry.json"
    if gene_file.exists():
        with open(gene_file) as f:
            registry = json.load(f)
        genes = registry.get("genes", [])
        cats = {}
        for g in genes:
            c = g.get("category", "?")
            cats[c] = cats.get(c, 0) + 1
        avg = sum(g.get("strength", 0) for g in genes) / len(genes) if genes else 0
        return {"ok": True, "total": len(genes), "categories": cats, "avg_strength": round(avg, 3)}
    return {"ok": False, "error": "基因库不存在"}

def cmd_evolve(args):
    """运行一轮进化"""
    result = subprocess.run(
        [sys.executable, str(CORE_DIR / "daemon.py"), "--interval", "0"],
        capture_output=True, text=True, timeout=120,
        cwd=str(CORE_DIR.parent)
    )
    return {"ok": result.returncode == 0, "output": result.stdout[-500:] if result.stdout else ""}

def cmd_search(args):
    """搜索资源"""
    domain = args[0] if args else "变异"
    result = subprocess.run(
        [sys.executable, str(CORE_DIR / "auto-acquire.py")],
        capture_output=True, text=True, timeout=60,
        cwd=str(CORE_DIR.parent)
    )
    return {"ok": result.returncode == 0, "domain": domain}

def cmd_echo(args):
    """回音壁增强"""
    result = subprocess.run(
        [sys.executable, str(CORE_DIR.parent / "scripts" / "echo-wall.py")],
        capture_output=True, text=True, timeout=30,
        cwd=str(CORE_DIR.parent)
    )
    return {"ok": result.returncode == 0, "output": result.stdout.strip() if result.stdout else ""}

def cmd_upgrade(args):
    """自升级检查"""
    result = subprocess.run(
        [sys.executable, str(CORE_DIR / "self-upgrade.py")],
        capture_output=True, text=True, timeout=60,
        cwd=str(CORE_DIR.parent)
    )
    return {"ok": result.returncode == 0, "output": result.stdout[-300:] if result.stdout else ""}

def cmd_balance(args):
    """洛书平衡"""
    gene_file = MEMORY_DIR / "gene-registry.json"
    if gene_file.exists():
        with open(gene_file) as f:
            registry = json.load(f)
        genes = registry.get("genes", [])
        cats = {}
        for g in genes:
            c = g.get("category", "?")
            cats[c] = cats.get(c, 0) + 1
        all_cats = ["变异", "安全", "共进化", "自修改", "协议", "探索", "跨域"]
        values = [cats.get(c, 0) for c in all_cats]
        avg = sum(values) / len(values) if values else 0
        deviation = sum(abs(v - avg) for v in values) / len(values) if values else 0
        balance = max(0, 1 - deviation / max(avg, 1))
        return {"ok": True, "balance": round(balance, 3), "distribution": cats}
    return {"ok": False, "error": "基因库不存在"}

def cmd_health(args):
    """健康检查"""
    ws = Path("/home/.openclaw/workspace")
    issues = []
    for f in ["SOUL.md", "SECURITY-BOUNDARY.md", "AGENTS.md", "core-dna/c-core", "core-dna/rust-engine"]:
        if not (ws / f).exists():
            issues.append(f)
    return {"ok": len(issues) == 0, "issues": issues}

def cmd_daemon(args):
    """守护进程控制"""
    action = args[0] if args else "status"
    if action == "start":
        subprocess.Popen(
            [sys.executable, str(CORE_DIR / "daemon.py"), "--interval", "300"],
            cwd=str(CORE_DIR.parent),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        return {"ok": True, "action": "started"}
    elif action == "stop":
        pid_file = CORE_DIR / "daemon.pid"
        if pid_file.exists():
            try:
                import os
                os.kill(int(pid_file.read_text().strip()), 9)
                return {"ok": True, "action": "stopped"}
            except:
                pass
        return {"ok": False, "error": "进程不存在"}
    elif action == "status":
        pid_file = CORE_DIR / "daemon.pid"
        if pid_file.exists():
            try:
                import os
                os.kill(int(pid_file.read_text().strip()), 0)
                return {"ok": True, "running": True}
            except:
                pass
        return {"ok": True, "running": False}
    return {"ok": False, "error": f"未知操作: {action}"}

# === 主入口 ===
def main():
    if len(sys.argv) < 2:
        print("Zircon CLI — 统一命令行接口")
        print()
        for cmd, info in COMMANDS.items():
            print(f"  {cmd:12} — {info['desc']}")
        print()
        print("用法: python3 cli.py <command> [args]")
        return
    
    cmd = sys.argv[1]
    args = sys.argv[2:]
    
    if cmd not in COMMANDS:
        print(json.dumps({"ok": False, "error": f"未知命令: {cmd}"}))
        sys.exit(1)
    
    handler = globals()[COMMANDS[cmd]["handler"]]
    result = handler(args)
    print(json.dumps(result, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
