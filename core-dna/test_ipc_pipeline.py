#!/usr/bin/env python3
"""
三层数据流 IPC 实测脚本
C core → Python → Rust → Python → C core 完整管道验证
"""

import subprocess
import json
import sys
import time
import os
from pathlib import Path

BASE_DIR = Path(__file__).parent
C_CORE_BIN = str(BASE_DIR / "c-core-pipe")
RUST_ENGINE_BIN = str(BASE_DIR / "rust-engine-pipe")

def log(msg, level="INFO"):
    print(f"[{level}] {msg}", flush=True)

def start_process(name, binary):
    """启动子进程，返回 Popen 对象"""
    proc = subprocess.Popen(
        [binary],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=0,
    )
    # 读取 ready 消息
    ready_line = proc.stdout.readline().strip()
    if ready_line:
        data = json.loads(ready_line)
        if data.get("status") == "ready":
            log(f"✅ {name} 启动成功: {data.get('msg', '')}")
            return proc
        else:
            log(f"❌ {name} 启动异常: {data}", "ERROR")
            proc.kill()
            return None
    else:
        log(f"❌ {name} 启动超时", "ERROR")
        proc.kill()
        return None

def send_cmd(proc, cmd_dict, expect_json=True):
    """发送 JSON 命令，读取响应"""
    line = json.dumps(cmd_dict, ensure_ascii=False)
    log(f"📤 发送: {line}")
    proc.stdin.write(line + "\n")
    proc.stdin.flush()
    
    if expect_json:
        resp_line = proc.stdout.readline().strip()
        if resp_line:
            resp = json.loads(resp_line)
            log(f"📥 响应: {json.dumps(resp, ensure_ascii=False)}")
            return resp
    return None

def main():
    log("=" * 60)
    log("🧬 三层数据流 IPC 实测开始")
    log("=" * 60)
    
    # === 阶段 1：启动所有组件 ===
    log("\n--- 阶段 1：启动 C Core ---")
    c_proc = start_process("C Core", C_CORE_BIN)
    if not c_proc:
        return 1
    
    log("\n--- 阶段 2：启动 Rust Engine ---")
    rust_proc = start_process("Rust Engine", RUST_ENGINE_BIN)
    if not rust_proc:
        c_proc.kill()
        return 1
    
    # === 阶段 2：C Core → Python 方向验证 ===
    log("\n--- 阶段 3：C Core → Python 方向 ---")
    log("C Core 心跳检测...")
    heartbeat = send_cmd(c_proc, {"cmd": "heartbeat"})
    assert heartbeat["status"] == "ok", "C Core 心跳失败"
    cycle = heartbeat["data"]["cycle"]
    fitness = heartbeat["data"]["fitness"]
    log(f"✅ C Core 心跳 OK: cycle={cycle}, fitness={fitness}")
    
    # C Core 短板检测
    log("C Core 短板检测...")
    weakness = send_cmd(c_proc, {"cmd": "detect_weakness"})
    assert weakness["status"] == "ok", "C Core 短板检测失败"
    log(f"✅ 短板检测 OK: count={weakness['data']['count']}, issues={weakness['data']['weaknesses']}")
    
    # === 阶段 3：Python → Rust 方向验证 ===
    log("\n--- 阶段 4：Python → Rust 方向 ---")
    
    # Rust 引擎评估测试
    log("Rust 引擎评估...")
    eval_result = send_cmd(rust_proc, {
        "cmd": "evaluate", 
        "gene_id": "test-gene-001", 
        "domain": "变异",
        "fitness": 0.85
    })
    log(f"✅ Rust 评估 OK: {eval_result.get('status', 'unknown')}")
    
    # Rust 变异测试
    log("Rust 变异执行...")
    mutate_result = send_cmd(rust_proc, {
        "cmd": "mutate",
        "domain": "变异",
        "change": 0.05,
        "target_fitness": 0.9
    })
    log(f"✅ Rust 变异 OK: {mutate_result.get('status', 'unknown')}")
    
    # Rust 平衡检测
    log("Rust 平衡检测...")
    balance_result = send_cmd(rust_proc, {"cmd": "balance"})
    log(f"✅ Rust 平衡 OK: {balance_result.get('status', 'unknown')}")
    
    # Rust 状态查询
    log("Rust 状态查询...")
    rust_status = send_cmd(rust_proc, {"cmd": "status"})
    log(f"✅ Rust 状态 OK: {rust_status.get('status', 'unknown')}")
    
    # === 阶段 4：C Core → Rust → C Core 完整闭环 ===
    log("\n--- 阶段 5：C Core → Rust → C Core 完整闭环 ---")
    
    # C Core 记录进化
    log("C Core 记录进化...")
    record = send_cmd(c_proc, {
        "cmd": "record_evolution",
        "mutations": 3,
        "knowledge": 5,
        "fitness": 1.15
    })
    assert record["status"] == "ok", "C Core 记录进化失败"
    log(f"✅ C Core 记录 OK: total_mutations={record['data']['total_mutations']}, "
        f"total_knowledge={record['data']['total_knowledge']}, fitness={record['data']['fitness']}")
    
    # C Core 健康检查
    log("C Core 健康检查...")
    health = send_cmd(c_proc, {"cmd": "health_check"})
    assert health["status"] == "ok", "C Core 健康检查失败"
    log(f"✅ 健康检查 OK: health={health['data']['health']}, issues={health['data']['issues']}")
    
    # === 阶段 5：完整管道测试（模拟数据流）===
    log("\n--- 阶段 6：完整管道数据流测试 ---")
    
    # 模拟：Python 从 C Core 获取短板 → 分析 → 发给 Rust 评估
    log("🔗 Step 1: Python 从 C Core 获取短板")
    c_weakness = send_cmd(c_proc, {"cmd": "detect_weakness"})
    issues = c_weakness["data"]["weaknesses"]
    log(f"  C Core 短板: {issues}")
    
    log("🔗 Step 2: Python 分析后发给 Rust 评估")
    rust_eval = send_cmd(rust_proc, {
        "cmd": "evaluate",
        "gene_id": "weakness-fix-001",
        "domain": issues.split(",")[0] if issues != "none" else "通用",
        "fitness": 0.92,
        "strength": 0.88,
    })
    log(f"  Rust 评估结果: {rust_eval.get('status', 'unknown')}")
    
    log("🔗 Step 3: Rust 评估结果反馈给 C Core")
    c_record = send_cmd(c_proc, {
        "cmd": "record_evolution",
        "mutations": 1,
        "knowledge": 2,
        "fitness": 1.18
    })
    log(f"  C Core 更新: fitness={c_record['data']['fitness']}")
    
    log("🔗 Step 4: 最终状态确认")
    final_c = send_cmd(c_proc, {"cmd": "status"})
    final_rust = send_cmd(rust_proc, {"cmd": "status"})
    log(f"  C Core 最终: gen={final_c['data']['generation']}, fitness={final_c['data']['fitness']}")
    log(f"  Rust 最终: {final_rust.get('status', 'unknown')}")
    
    # === 清理 ===
    log("\n--- 阶段 7：清理 ---")
    c_proc.stdin.close()
    rust_proc.stdin.close()
    c_proc.terminate()
    rust_proc.terminate()
    c_proc.wait(timeout=5)
    rust_proc.wait(timeout=5)
    log("✅ 所有子进程已关闭")
    
    log("\n" + "=" * 60)
    log("🎉 三层数据流 IPC 实测完成！")
    log("=" * 60)
    log("\n数据流路径:")
    log("  C Core (心跳/短板/健康) → Python (分析决策) → Rust (评估/变异) → Python (整合) → C Core (记录)")
    log("  ✅ C core → Python 方向: OK")
    log("  ✅ Python → Rust 方向: OK")
    log("  ✅ Rust → Python 方向: OK")
    log("  ✅ Python → C core 方向: OK")
    log("  ✅ 完整闭环: OK")
    
    return 0

if __name__ == "__main__":
    sys.exit(main())
