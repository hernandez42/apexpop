#!/usr/bin/env python3
"""
自愈引擎 — 防止修复死循环
核心原则：修复必须验证效果，无效修复必须升级策略或停止

三个机制：
1. 状态快照对比：修复前后对比，确认状态真的变了
2. 连续失败熔断：同一问题连续失败 N 次 → 停止循环，上报
3. 策略升级：修复失败 → 换策略 → 再失败 → 升级为人工介入
"""

import json
import hashlib
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Optional

MEMORY_DIR = Path("/home/.openclaw/workspace/memory")
STATE_SNAPSHOT = MEMORY_DIR / "self-heal-snapshot.json"
HEAL_LOG = MEMORY_DIR / "self-heal-log.jsonl"

# === 配置 ===
MAX_SAME_FIX_ATTEMPTS = 3    # 同一修复最多尝试 3 次
MAX_TOTAL_ROUNDS = 10        # 单次治疗最多 10 轮
COOLDOWN_SECONDS = 60        # 熔断后冷却时间


def log_heal(msg: str, level: str = "INFO"):
    """记录自愈日志"""
    entry = {
        "timestamp": datetime.now().isoformat(),
        "level": level,
        "message": msg
    }
    with open(HEAL_LOG, "a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def snapshot_state() -> dict:
    """快照当前关键状态"""
    state = {}
    
    # 基因库状态
    gene_file = MEMORY_DIR / "gene-registry.json"
    if gene_file.exists():
        with open(gene_file) as f:
            registry = json.load(f)
        genes = registry.get("genes", [])
        state["gene_count"] = len(genes)
        cats = {}
        for g in genes:
            cat = g.get("category", "unknown")
            cats[cat] = cats.get(cat, 0) + 1
        state["gene_categories"] = cats
        # 基因库内容哈希（检测是否真的变了）
        state["gene_hash"] = hashlib.md5(
            json.dumps(sorted([(g.get("name",""), g.get("category","")) for g in genes])).encode()
        ).hexdigest()
    
    # 关键文件状态
    core_files = ["c-core", "engine.rs", "glue.py", "daemon.py"]
    for f in core_files:
        path = MEMORY_DIR.parent / "core-dna" / f
        if path.exists():
            state[f"file_{f}"] = path.stat().st_mtime
        else:
            state[f"file_{f}"] = 0
    
    return state


def state_changed(before: dict, after: dict) -> bool:
    """检查状态是否真的发生了变化"""
    for key in before:
        if key in after and before[key] != after[key]:
            return True
    return False


class SelfHealer:
    """自愈引擎 — 防止修复死循环"""
    
    def __init__(self):
        self.attempt_history: Dict[str, int] = {}  # fix_name → consecutive_failures
        self.total_rounds = 0
        self.circuit_open = False
        self.circuit_open_until = 0
    
    def _fix_key(self, fix_type: str, context: dict) -> str:
        """生成修复唯一标识"""
        return f"{fix_type}:{json.dumps(context, sort_keys=True)}"
    
    def check_circuit_breaker(self) -> bool:
        """检查熔断器"""
        if self.circuit_open:
            import time
            if time.time() < self.circuit_open_until:
                remaining = int(self.circuit_open_until - time.time())
                log_heal(f"🔴 熔断中，剩余 {remaining}s", "WARN")
                return True
            else:
                self.circuit_open = False
                log_heal("🟢 熔断冷却结束，恢复尝试", "INFO")
        return False
    
    def record_failure(self, fix_key: str):
        """记录修复失败"""
        self.attempt_history[fix_key] = self.attempt_history.get(fix_key, 0) + 1
        count = self.attempt_history[fix_key]
        
        if count >= MAX_SAME_FIX_ATTEMPTS:
            log_heal(
                f"🔴 同一修复 [{fix_key}] 连续失败 {count} 次 → 触发熔断",
                "ERROR"
            )
            import time
            self.circuit_open = True
            self.circuit_open_until = time.time() + COOLDOWN_SECONDS
            return True  # 触发了熔断
        return False
    
    def record_success(self, fix_key: str):
        """记录修复成功，重置计数"""
        if fix_key in self.attempt_history:
            del self.attempt_history[fix_key]
    
    def run_fix_with_validation(
        self,
        fix_name: str,
        context: dict,
        fix_fn: Callable[[], bool],
        validate_fn: Optional[Callable[[], bool]] = None
    ) -> dict:
        """
        执行修复 + 验证效果
        
        fix_fn: 执行修复，返回是否"执行了"
        validate_fn: 验证修复是否"生效了"（可选，默认检查状态变化）
        
        返回:
        {
            "success": bool,
            "executed": bool,      # 是否执行了修复操作
            "validated": bool,     # 是否验证通过
            "reason": str,
            "action": "fixed" | "skipped" | "circuit_break" | "escalate"
        }
        """
        fix_key = self._fix_key(fix_name, context)
        result = {"success": False, "executed": False, "validated": False, "reason": "", "action": ""}
        
        self.total_rounds += 1
        
        # 0. 总轮次检查
        if self.total_rounds > MAX_TOTAL_ROUNDS:
            result["reason"] = f"总轮次 {self.total_rounds} 超过上限 {MAX_TOTAL_ROUNDS}"
            result["action"] = "escalate"
            log_heal(f"🔴 {result['reason']}", "ERROR")
            return result
        
        # 1. 熔断器检查
        if self.check_circuit_breaker():
            result["reason"] = "熔断器开启，拒绝执行"
            result["action"] = "circuit_break"
            return result
        
        # 2. 快照修复前状态
        before = snapshot_state()
        
        # 3. 执行修复
        try:
            executed = fix_fn()
            result["executed"] = executed
        except Exception as e:
            result["reason"] = f"修复执行异常: {e}"
            result["action"] = "escalate"
            log_heal(f"🔴 {result['reason']}", "ERROR")
            self.record_failure(fix_key)
            return result
        
        if not executed:
            result["reason"] = "修复函数返回 False（无需修复）"
            result["action"] = "skipped"
            result["success"] = True
            return result
        
        # 4. 验证效果
        after = snapshot_state()
        
        if validate_fn:
            try:
                validated = validate_fn()
            except Exception as e:
                validated = False
                log_heal(f"⚠️ 验证函数异常: {e}", "WARN")
        else:
            validated = state_changed(before, after)
        
        result["validated"] = validated
        
        if validated:
            result["success"] = True
            result["reason"] = "修复已执行且验证通过"
            result["action"] = "fixed"
            self.record_success(fix_key)
            log_heal(f"✅ [{fix_name}] 修复成功，状态已变化", "INFO")
        else:
            result["reason"] = "修复已执行但状态未变化"
            result["action"] = "skipped"
            failed = self.record_failure(fix_key)
            if failed:
                result["action"] = "circuit_break"
            log_heal(
                f"⚠️ [{fix_name}] 执行但未生效 (连续第{self.attempt_history.get(fix_key,0)}次)",
                "WARN"
            )
        
        return result
    
    def reset(self):
        """重置所有状态"""
        self.attempt_history.clear()
        self.total_rounds = 0
        self.circuit_open = False
        log_heal("🔄 自愈引擎已重置", "INFO")


def demo():
    """演示自愈机制"""
    healer = SelfHealer()
    
    # 模拟：修复基因缺口，但补充操作不真正生效
    def fake_gene_fix():
        """假装补充基因（不实际写入）"""
        log_heal("🧬 补充基因: 安全")
        return True  # 执行了
    
    def gene_still_gap():
        """验证：基因缺口仍然存在"""
        gene_file = MEMORY_DIR / "gene-registry.json"
        if gene_file.exists():
            with open(gene_file) as f:
                registry = json.load(f)
            genes = registry.get("genes", [])
            cats = {}
            for g in genes:
                cat = g.get("category", "unknown")
                cats[cat] = cats.get(cat, 0) + 1
            # 安全类 < 3 就算未修复
            return cats.get("安全", 0) >= 3
        return False
    
    # 运行 5 次，观察熔断
    for i in range(5):
        print(f"\n--- 第 {i+1} 轮 ---")
        result = healer.run_fix_with_validation(
            fix_name="gene_gap_fix",
            context={"category": "安全"},
            fix_fn=fake_gene_fix,
            validate_fn=gene_still_gap
        )
        print(f"  执行: {result['executed']}, 验证: {result['validated']}, "
              f"动作: {result['action']}, 原因: {result['reason']}")
        if result["action"] == "circuit_break":
            print("  🔴 熔断触发！循环停止。")
            break


if __name__ == "__main__":
    demo()
