#!/usr/bin/env python3
"""
自升级引擎 — C core 驱动 Rust/Python 自动修复和升级
检测问题 → 分析原因 → 生成修复 → 执行修复 → 验证结果
"""

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

CORE_DIR = Path("/home/.openclaw/workspace/core-dna")
MEMORY_DIR = Path("/home/.openclaw/workspace/memory")
UPGRADE_LOG = MEMORY_DIR / "upgrade-log.jsonl"

class SelfUpgrade:
    def __init__(self):
        self.fixes_applied = 0
    
    def log(self, msg):
        entry = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
        print(entry)
        with open(UPGRADE_LOG, "a") as f:
            f.write(json.dumps({"timestamp": datetime.now().isoformat(), "message": msg}) + "\n")
    
    # === 检测 ===
    def detect_all(self):
        """全面检测"""
        issues = []
        
        # 1. 编译状态
        if not (CORE_DIR / "c-core").exists():
            issues.append({"type": "compile", "component": "c-core", "severity": "high"})
        
        if not (CORE_DIR / "rust-engine").exists():
            issues.append({"type": "compile", "component": "rust-engine", "severity": "high"})
        
        # 2. 基因库
        gene_file = MEMORY_DIR / "gene-registry.json"
        if gene_file.exists():
            with open(gene_file) as f:
                registry = json.load(f)
            genes = registry.get("genes", [])
            categories = {}
            for g in genes:
                cat = g.get("category", "unknown")
                categories[cat] = categories.get(cat, 0) + 1
            
            # 检查平衡
            all_cats = ["变异", "安全", "共进化", "自修改", "协议", "探索"]
            for cat in all_cats:
                if categories.get(cat, 0) < 3:
                    issues.append({"type": "gene_gap", "category": cat, "count": categories.get(cat, 0), "severity": "medium"})
        
        # 3. 守护进程
        daemon_state = MEMORY_DIR / "daemon-state.json"
        if daemon_state.exists():
            with open(daemon_state) as f:
                state = json.load(f)
            last_run = state.get("last_run", "")
            if last_run:
                from datetime import datetime as dt
                try:
                    last = dt.fromisoformat(last_run)
                    diff = (dt.now() - last).total_seconds()
                    if diff > 600:  # 超过10分钟没运行
                        issues.append({"type": "daemon_stale", "seconds": diff, "severity": "high"})
                except:
                    pass
        
        # 4. 安全边界
        security = Path("/home/.openclaw/workspace/SECURITY-BOUNDARY.md")
        if security.exists():
            import os
            mode = oct(os.stat(security).st_mode)[-3:]
            if mode != "444":
                issues.append({"type": "security", "issue": "权限过宽", "severity": "high"})
        
        return issues
    
    # === 修复 ===
    def fix_compile(self, component):
        """修复编译"""
        self.log(f"🔧 修复编译: {component}")
        
        if component == "c-core":
            result = subprocess.run(
                ["gcc", "-o", "c-core", "main.c", "-Wall", "-Wextra", "-lm"],
                cwd=str(CORE_DIR), capture_output=True, text=True
            )
        elif component == "rust-engine":
            result = subprocess.run(
                ["rustc", "engine.rs", "-o", "rust-engine", "-A", "unused"],
                cwd=str(CORE_DIR), capture_output=True, text=True
            )
        else:
            return False
        
        if result.returncode == 0:
            self.log(f"✅ {component} 编译成功")
            return True
        else:
            self.log(f"❌ {component} 编译失败: {result.stderr[:100]}")
            return False
    
    def fix_gene_gap(self, category):
        """修复基因缺口"""
        self.log(f"🧬 补充基因: {category}")
        
        # 调用自动获取
        result = subprocess.run(
            [sys.executable, str(CORE_DIR / "auto-acquire.py")],
            capture_output=True, text=True, timeout=60,
            cwd=str(CORE_DIR.parent)
        )
        
        if result.returncode == 0:
            self.log(f"✅ {category} 基因补充完成")
            return True
        else:
            self.log(f"⚠️ {category} 基因补充失败")
            return False
    
    def fix_daemon(self):
        """修复守护进程"""
        self.log("🔄 重启守护进程...")
        
        # 检查是否有旧进程
        pid_file = CORE_DIR / "daemon.pid"
        if pid_file.exists():
            try:
                old_pid = int(pid_file.read_text().strip())
                import os
                os.kill(old_pid, 9)  # 终止旧进程
                self.log(f"  终止旧进程: {old_pid}")
            except:
                pass
        
        # 启动新进程
        subprocess.Popen(
            [sys.executable, str(CORE_DIR / "daemon.py"), "--interval", "300"],
            cwd=str(CORE_DIR.parent),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        
        self.log("✅ 守护进程已重启")
        return True
    
    def fix_security(self):
        """修复安全权限"""
        import os
        security = Path("/home/.openclaw/workspace/SECURITY-BOUNDARY.md")
        os.chmod(security, 0o444)
        self.log("✅ 安全边界权限已恢复 (444)")
        return True
    
    # === 升级 ===
    def upgrade(self):
        """执行升级"""
        self.log("\n" + "=" * 50)
        self.log("🚀 自升级引擎启动")
        self.log("=" * 50)
        
        # 1. 检测
        issues = self.detect_all()
        self.log(f"📊 检测到 {len(issues)} 个问题")
        
        if not issues:
            self.log("✅ 全部正常，无需升级")
            return True
        
        # 2. 修复
        for issue in issues:
            self.log(f"\n--- 修复: {issue['type']} ---")
            
            if issue["type"] == "compile":
                self.fix_compile(issue["component"])
            elif issue["type"] == "gene_gap":
                self.fix_gene_gap(issue["category"])
            elif issue["type"] == "daemon_stale":
                self.fix_daemon()
            elif issue["type"] == "security":
                self.fix_security()
            
            self.fixes_applied += 1
        
        # 3. 验证
        remaining = self.detect_all()
        self.log(f"\n📊 修复后剩余: {len(remaining)} 个问题")
        
        self.log(f"\n✅ 自升级完成: 修复 {self.fixes_applied} 个问题")
        return len(remaining) == 0

if __name__ == "__main__":
    engine = SelfUpgrade()
    engine.upgrade()
