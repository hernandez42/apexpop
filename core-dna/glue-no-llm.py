#!/usr/bin/env python3
"""
Glue Layer — 无 LLM 版本
连接 C Core、Rust Engine 和互联网知识源的协调层

架构：
  ┌─────────────────────────────────────────────────┐
  │              glue-no-llm.py (Python)          │
  │        胶水层 — 协调一切，自身无状态             │
  ├─────────────────────────────────────────────────┤
  │                                                 │
  │   ┌──────────┐   ┌──────────┐   ┌──────────┐  │
  │   │ C Core   │   │ Rust Eng │   │ Internet │  │
  │   │ (identity│   │ (mutate/ │   │ (arXiv/  │  │
  │   │  health) │   │  eval)   │   │ GitHub)  │  │
  │   └────┬─────┘   └────┬─────┘   └────┬─────┘  │
  │        │  pipe stdin/  │  pipe stdin/  │ API  │
  │        │  stdout       │  stdout       │      │
  │        ▼              ▼              ▼        │
  │   ┌──────────────────────────────────────────┐ │
  │   │        Auto Decision Engine              │ │
  │   │  (基于规则的自动决策，无需 LLM)           │ │
  │   └──────────────────────────────────────────┘ │
  └─────────────────────────────────────────────────┘

进化主循环（每个心跳周期）：
  1. C Core 心跳 + 健康检查
  2. C Core 检测短板
  3. Python 搜索互联网知识（arXiv/GitHub）
  4. 自动决策引擎生成决策
  5. Python 调用 Rust Engine 执行变异
  6. Rust Engine 评估结果
  7. 更新进化状态
"""

import json
import os
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# === 路径配置 ===
BASE_DIR = Path(__file__).parent
C_CORE_BIN = str(BASE_DIR / "c-core")
RUST_ENGINE_BIN = str(BASE_DIR / "rust-engine-pipe")
MEMORY_DIR = Path.home() / ".openclaw/workspace/memory"
EVOLUTION_LOG = MEMORY_DIR / "evolution-cycles-no-llm.jsonl"

# === 决策引擎 ===
class AutoDecisionEngine:
    """无 LLM 的自动决策引擎"""
    
    def __init__(self):
        self.cycles_without_improvement = 0
        self.last_fitness = 0.0
        self.best_fitness = 0.0
        self.last_domain = ""
    
    def make_decision(self, weaknesses: dict, state: dict) -> dict:
        """基于短板和状态生成决策"""
        self.cycles_without_improvement += 1
        
        fitness = state.get('fitness', 0.5)
        balance = state.get('balance', 0.5)
        weak_count = weaknesses.get('count', 0)
        weak_list = weaknesses.get('weaknesses', "")
        
        # 健康优先
        if fitness < 0.3 or balance < 0.2:
            return {
                "action": "recover",
                "domain": "安全",
                "change": 0.05,
                "reason": "系统健康状态异常，需要修复",
                "confidence": 0.9
            }
        
        # 根据短板类型决定领域
        if "skills_count_low" in weak_list or "能力" in weak_list:
            domain = "变异"
            change = 0.15
            reason = "能力维度偏低，需要增加变异技能"
        elif "knowledge_count_low" in weak_list or "知识" in weak_list:
            domain = "知识"
            change = 0.12
            reason = "知识储备不足，需要学习"
        elif "balance_low" in weak_list or "协调" in weak_list:
            domain = "共进化"
            change = 0.08
            reason = "平衡度低，需要促进领域间共进化"
        elif "fitness_low" in weak_list or "适应" in weak_list:
            domain = "评估"
            change = 0.10
            reason = "适应度低，需要加强评估能力"
        else:
            # 无明显短板，探索新方向
            domains = ["探索", "记忆", "协议", "共进化"]
            import random
            domain = random.choice(domains)
            change = 0.06
            reason = "无明显短板，探索新方向"
        
        # 避免重复在同一领域操作
        if domain == self.last_domain and self.cycles_without_improvement < 3:
            domains = ["探索", "记忆", "协议", "共进化"]
            if domain in domains:
                domains.remove(domain)
            domain = domains[0] if domains else "探索"
        
        self.last_domain = domain
        
        # 检查是否有改进
        if fitness > self.last_fitness + 0.01:
            self.cycles_without_improvement = 0
            self.best_fitness = fitness
        self.last_fitness = fitness
        
        # 长期无改进则加大探索力度
        if self.cycles_without_improvement > 15:
            change = min(change * 2, 0.3)
            reason = f"长期无改进({self.cycles_without_improvement}轮)，加大探索力度: {reason}"
            self.cycles_without_improvement = 0
        
        return {
            "status": "ok",
            "action": "mutate",
            "domain": domain,
            "change": change,
            "reason": reason,
            "confidence": 0.8 if fitness > 0.5 else 0.6
        }

# === 互联网知识搜索 ===
class KnowledgeSearcher:
    """互联网知识搜索器"""
    
    def __init__(self):
        self.cache = {}
    
    def search_arxiv(self, query: str, max_results: int = 3) -> list:
        """搜索 arXiv 论文"""
        try:
            import urllib.parse
            encoded_query = urllib.parse.quote(query)
            url = f"https://export.arxiv.org/api/query?search_query={encoded_query}&max_results={max_results}"
            
            with urllib.request.urlopen(url, timeout=15) as resp:
                content = resp.read().decode()
            
            results = []
            lines = content.split('\n')
            in_entry = False
            entry = {}
            
            for line in lines:
                if '<entry>' in line:
                    in_entry = True
                    entry = {}
                elif '</entry>' in line and in_entry:
                    if entry:
                        results.append(entry)
                    in_entry = False
                elif in_entry:
                    if '<title>' in line and '</title>' in line:
                        title = line.replace('<title>', '').replace('</title>', '').strip()
                        entry['title'] = title
                    elif '<summary>' in line and '</summary>' in line:
                        summary = line.replace('<summary>', '').replace('</summary>', '').strip()
                        entry['summary'] = summary[:200] + "..." if len(summary) > 200 else summary
                    elif '<id>' in line and '</id>' in line:
                        url = line.replace('<id>', '').replace('</id>', '').strip()
                        entry['url'] = url
            
            return results[:max_results]
        except Exception as e:
            print(f"[Knowledge] arXiv 搜索失败: {e}")
            return []
    
    def search_github(self, query: str, max_results: int = 3) -> list:
        """搜索 GitHub 仓库"""
        try:
            import urllib.parse
            encoded_query = urllib.parse.quote(query)
            url = f"https://api.github.com/search/repositories?q={encoded_query}&per_page={max_results}"
            
            with urllib.request.urlopen(url, timeout=15) as resp:
                content = resp.read().decode()
            
            data = json.loads(content)
            items = data.get('items', [])
            
            results = []
            for item in items[:max_results]:
                results.append({
                    'title': item.get('name', ''),
                    'summary': item.get('description', 'No description')[:200] + "..." if item.get('description') else 'No description',
                    'url': item.get('html_url', '')
                })
            
            return results
        except Exception as e:
            print(f"[Knowledge] GitHub 搜索失败: {e}")
            return []
    
    def search(self, topic: str) -> dict:
        """综合搜索"""
        print(f"[Knowledge] 搜索: {topic}")
        
        arxiv_results = self.search_arxiv(topic, 2)
        github_results = self.search_github(topic, 2)
        
        return {
            'arxiv': arxiv_results,
            'github': github_results,
            'total': len(arxiv_results) + len(github_results)
        }

# === 子进程管理 ===
@dataclass
class SubprocessBridge:
    name: str
    binary: str
    process: Optional[subprocess.Popen] = field(default=None, init=False)
    ready: bool = field(default=False, init=False)

    def start(self) -> bool:
        try:
            self.process = subprocess.Popen(
                [self.binary],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
            line = self.process.stdout.readline().strip()
            if line:
                msg = json.loads(line)
                if msg.get("status") == "ready":
                    self.ready = True
                    print(f"[Glue] ✅ {self.name} 就绪: {msg.get('msg', '')}")
                    return True
            print(f"[Glue] ❌ {self.name} 未就绪: {line}")
            return False
        except Exception as e:
            print(f"[Glue] ❌ {self.name} 启动失败: {e}")
            return False

    def send(self, cmd: dict) -> dict:
        if not self.process or not self.ready:
            return {"status": "error", "msg": f"{self.name} 未就绪"}

        try:
            line = json.dumps(cmd, ensure_ascii=False)
            self.process.stdin.write(line + "\n")
            self.process.stdin.flush()

            response = self.process.stdout.readline().strip()
            if response:
                return json.loads(response)
            return {"status": "error", "msg": "空响应"}
        except Exception as e:
            return {"status": "error", "msg": str(e)}

    def stop(self):
        if self.process:
            try:
                self.process.stdin.close()
                self.process.terminate()
                self.process.wait(timeout=5)
            except Exception:
                self.process.kill()
            self.ready = False
            print(f"[Glue] 🔴 {self.name} 已停止")

# === 进化主循环 ===
class EvolutionLoop:
    def __init__(self, max_cycles: int = 10):
        self.max_cycles = max_cycles
        self.c_core = SubprocessBridge("C Core", C_CORE_BIN)
        self.rust_engine = SubprocessBridge("Rust Engine", RUST_ENGINE_BIN)
        self.decision_engine = AutoDecisionEngine()
        self.knowledge_searcher = KnowledgeSearcher()
        self.cycle_log = []

    def start(self) -> bool:
        print("=" * 60)
        print("  Glue Layer — 无 LLM 自主进化主循环")
        print("=" * 60)

        c_ok = self.c_core.start()
        r_ok = self.rust_engine.start()

        if not c_ok or not r_ok:
            print("[Glue] ❌ 组件启动失败，无法运行")
            return False

        print("[Glue] ✅ 所有组件就绪，开始无 LLM 自主进化\n")
        return True

    def run_cycle(self, cycle_num: int) -> dict:
        print(f"--- 循环 #{cycle_num} ---")
        result = {"cycle": cycle_num, "steps": []}

        # Step 1: C Core 心跳
        print(f"  [1] C Core 心跳...")
        heartbeat = self.c_core.send({"cmd": "heartbeat"})
        result["steps"].append({"step": "heartbeat", "result": heartbeat.get("status")})
        c_state = heartbeat.get("data", {})
        print(f"      → cycle={c_state.get('cycle')}, fitness={c_state.get('fitness', 0):.4f}")
        time.sleep(0.1)

        # Step 2: C Core 检测短板
        print(f"  [2] 检测短板...")
        weakness = self.c_core.send({"cmd": "detect_weakness"})
        result["steps"].append({"step": "detect_weakness", "result": weakness.get("status")})
        weak_count = weakness.get("data", {}).get("count", 0)
        weak_list = weakness.get("data", {}).get("weaknesses", "none")
        print(f"      → 发现 {weak_count} 个短板: {weak_list}")
        time.sleep(0.1)

        # Step 3: 互联网知识搜索（无 LLM）
        print(f"  [3] 互联网知识搜索...")
        search_topic = weak_list if weak_list != "none" else "AI self-improvement"
        search_results = self.knowledge_searcher.search(search_topic)
        print(f"      → 找到 {search_results['total']} 条知识")
        for i, item in enumerate(search_results.get('arxiv', [])[:2]):
            print(f"        [arXiv] {item.get('title', '')[:50]}...")
        for i, item in enumerate(search_results.get('github', [])[:2]):
            print(f"        [GitHub] {item.get('title', '')[:50]}...")
        result["steps"].append({"step": "knowledge_search", "count": search_results['total']})
        time.sleep(0.1)

        # Step 4: 自动决策（无 LLM）
        print(f"  [4] 自动决策...")
        decision = self.decision_engine.make_decision(
            weakness.get("data", {}),
            c_state
        )
        result["steps"].append({"step": "auto_decision", "result": decision.get("status")})
        print(f"      → 决策: {decision.get('domain', '?')} (change={decision.get('change', 0):.3f})")
        print(f"      → 理由: {decision.get('reason', '?')}")
        time.sleep(0.1)

        # Step 5: Rust Engine 执行变异
        print(f"  [5] Rust Engine 变异...")
        mutate_result = self.rust_engine.send({
            "cmd": "mutate",
            "domain": decision.get("domain", "探索"),
            "change": decision.get("change", 0.1),
        })
        result["steps"].append({"step": "mutate", "result": mutate_result.get("status")})
        gene_id = mutate_result.get("data", {}).get("gene_id", "")
        print(f"      → 基因: {gene_id}")
        time.sleep(0.1)

        # Step 6: Rust Engine 评估
        print(f"  [6] Rust Engine 评估...")
        if gene_id:
            eval_result = self.rust_engine.send({"cmd": "evaluate", "gene_id": gene_id})
            result["steps"].append({"step": "evaluate", "result": eval_result.get("status")})
            score = eval_result.get("data", {}).get("score", 0)
            print(f"      → 评估分: {score:.4f}")

            if score > 0.5:
                retain = self.rust_engine.send({"cmd": "retain", "gene_id": gene_id})
                print(f"      → 保留: {retain.get('status')}")
        else:
            print(f"      → 无基因可评估")
        time.sleep(0.1)

        # Step 7: 记录进化状态
        print(f"  [7] 记录进化状态...")
        record = self.c_core.send({
            "cmd": "record_evolution",
            "mutations": 1,
            "knowledge": search_results['total'],
            "fitness": c_state.get("fitness", 1.0) + decision.get("change", 0.1) * 0.01,
        })
        result["steps"].append({"step": "record_evolution", "result": record.get("status")})
        print(f"      → 记录完成\n")

        return result

    def run(self):
        if not self.start():
            return

        try:
            for i in range(1, self.max_cycles + 1):
                cycle_result = self.run_cycle(i)
                self.cycle_log.append(cycle_result)

            print("=" * 60)
            print("  无 LLM 自主进化完成 — 最终状态")
            print("=" * 60)

            status = self.c_core.send({"cmd": "status"})
            print(f"  C Core: {json.dumps(status.get('data', {}), indent=2, ensure_ascii=False)}")

            status_r = self.rust_engine.send({"cmd": "status"})
            print(f"  Rust:   {json.dumps(status_r.get('data', {}), indent=2, ensure_ascii=False)}")

            MEMORY_DIR.mkdir(parents=True, exist_ok=True)
            with open(EVOLUTION_LOG, "a") as f:
                for entry in self.cycle_log:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            print(f"\n  日志已保存: {EVOLUTION_LOG}")

        finally:
            self.c_core.stop()
            self.rust_engine.stop()

# === 入口 ===
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Glue Layer — 无 LLM 自主进化粘合层")
    parser.add_argument("--cycles", type=int, default=10, help="进化循环次数")
    args = parser.parse_args()

    loop = EvolutionLoop(max_cycles=args.cycles)
    loop.run()