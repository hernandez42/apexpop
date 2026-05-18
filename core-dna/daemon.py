#!/usr/bin/env python3
"""
C core 守护进程 — 全自动后台运行
不需要我就能自己完成：检测短板→搜索资源→消化→入库→融合→循环
"""

import json
import time
import subprocess
import sys
from datetime import datetime
from pathlib import Path

MEMORY_DIR = Path.home() / ".openclaw/workspace/memory"
CORE_DIR = Path("/home/.openclaw/workspace/core-dna")
DAEMON_LOG = MEMORY_DIR / "daemon-log.jsonl"
PID_FILE = CORE_DIR / "daemon.pid"

class CCDaemon:
    def __init__(self):
        self.running = True
        self.cycle_count = 0
        self.log_file = open(DAEMON_LOG, "a")
    
    def log(self, message):
        entry = f"[{datetime.now().strftime('%H:%M:%S')}] {message}"
        print(entry)
        self.log_file.write(json.dumps({
            "timestamp": datetime.now().isoformat(),
            "message": message,
            "cycle": self.cycle_count,
        }) + "\n")
        self.log_file.flush()
    
    def detect_weakness(self):
        """自动检测短板"""
        try:
            registry_file = MEMORY_DIR / "gene-registry.json"
            if registry_file.exists():
                with open(registry_file) as f:
                    registry = json.load(f)
                genes = registry.get("genes", [])
                categories = {}
                for g in genes:
                    cat = g.get("category", "unknown")
                    categories[cat] = categories.get(cat, 0) + 1
                
                all_cats = ["变异", "安全", "共进化", "自修改", "协议", "探索"]
                weakest = min(all_cats, key=lambda c: categories.get(c, 0))
                self.log(f"📊 短板: {weakest} (基因数 {categories.get(weakest, 0)})")
                return weakest, categories
        except Exception as e:
            self.log(f"⚠️ 短板检测失败: {e}")
        return None, {}
    
    def search_and_acquire(self, domain):
        """搜索并获取知识"""
        self.log(f"🔍 搜索 {domain} 相关资源...")
        
        # 调用自动获取引擎
        try:
            result = subprocess.run(
                [sys.executable, str(CORE_DIR / "auto-acquire.py")],
                capture_output=True, text=True, timeout=60,
                cwd=str(CORE_DIR.parent)
            )
            if result.returncode == 0:
                self.log(f"✅ 搜索完成")
                return True
            else:
                self.log(f"⚠️ 搜索失败: {result.stderr[:100]}")
                return False
        except Exception as e:
            self.log(f"⚠️ 搜索异常: {e}")
            return False
    
    def run_pipeline(self):
        """运行生产线"""
        self.log("🧬 运行基因生产线...")
        try:
            result = subprocess.run(
                [sys.executable, str(CORE_DIR / "pipeline.py")],
                capture_output=True, text=True, timeout=60,
                cwd=str(CORE_DIR.parent)
            )
            if result.returncode == 0:
                self.log("✅ 生产线完成")
                return True
        except Exception as e:
            self.log(f"⚠️ 生产线异常: {e}")
        return False
    
    def run_echo_wall(self):
        """运行回音壁"""
        self.log("🔊 运行回音壁...")
        try:
            result = subprocess.run(
                [sys.executable, str(CORE_DIR.parent / "scripts" / "echo-wall.py")],
                capture_output=True, text=True, timeout=30,
                cwd=str(CORE_DIR.parent)
            )
            if result.returncode == 0:
                self.log("✅ 回音壁完成")
                return True
        except Exception as e:
            self.log(f"⚠️ 回音壁异常: {e}")
        return False
    
    def run_self_upgrade(self):
        """运行自升级"""
        self.log("⬆️ 自升级检查...")
        try:
            result = subprocess.run(
                [sys.executable, str(CORE_DIR / "self-upgrade.py")],
                capture_output=True, text=True, timeout=60,
                cwd=str(CORE_DIR.parent)
            )
            if result.returncode == 0:
                self.log("✅ 自升级完成")
                return True
        except Exception as e:
            self.log(f"⚠️ 自升级异常: {e}")
        return False
    
    def health_check(self):
        """健康检查"""
        issues = []
        ws = Path("/home/.openclaw/workspace")
        critical_files = [
            "SOUL.md", "SECURITY-BOUNDARY.md", "AGENTS.md",
            "core-dna/main.c", "core-dna/engine.rs", "core-dna/pipeline.py",
            "core-dna/daemon.py", "core-dna/auto-acquire.py",
        ]
        for f in critical_files:
            if not (ws / f).exists():
                issues.append(f"缺失: {f}")
        
        if issues:
            self.log(f"⚠️ 健康问题: {', '.join(issues)}")
        else:
            self.log("✅ 健康检查通过")
        return len(issues) == 0
    
    def save_state(self):
        """保存状态"""
        state = {
            "cycle_count": self.cycle_count,
            "last_run": datetime.now().isoformat(),
            "status": "running",
        }
        with open(MEMORY_DIR / "daemon-state.json", "w") as f:
            json.dump(state, f, indent=2)
    
    def run_cycle(self):
        """运行一个完整循环"""
        self.cycle_count += 1
        self.log(f"\n{'='*40} 循环 #{self.cycle_count} {'='*40}")
        
        # 1. 健康检查
        self.health_check()
        
        # 2. 检测短板
        weakness, categories = self.detect_weakness()
        
        # 3. 搜索获取
        if weakness:
            self.search_and_acquire(weakness)
        
        # 4. 运行生产线
        self.run_pipeline()
        
        # 5. 回音壁增强
        self.run_echo_wall()
        
        # 6. 自升级检查
        self.run_self_upgrade()
        
        # 7. 保存状态
        self.save_state()
        
        self.log(f"✅ 循环 #{self.cycle_count} 完成")
    
    def run(self, interval=300):
        """主循环（默认5分钟一次）"""
        self.log("🚀 C core 守护进程启动")
        self.log(f"⏰ 循环间隔: {interval} 秒")
        
        # 写入 PID
        with open(PID_FILE, "w") as f:
            f.write(str(subprocess.os.getpid()))
        
        while self.running:
            try:
                self.run_cycle()
                self.log(f"💤 等待 {interval} 秒...")
                time.sleep(interval)
            except KeyboardInterrupt:
                self.log("🛑 收到终止信号")
                self.running = False
            except Exception as e:
                self.log(f"❌ 异常: {e}")
                time.sleep(60)  # 出错后等1分钟再试
        
        # 清理
        self.log("👋 C core 守护进程退出")
        self.log_file.close()
        if PID_FILE.exists():
            PID_FILE.unlink()

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="C core 守护进程")
    parser.add_argument("--interval", type=int, default=300, help="循环间隔（秒）")
    args = parser.parse_args()
    
    daemon = CCDaemon()
    daemon.run(interval=args.interval)
