#!/usr/bin/env python3
"""
无 LLM 自主进化测试脚本
测试知识搜索和自动决策引擎
"""

import json
import time
import urllib.request
import urllib.parse
import random

# === 模拟 C Core 状态 ===
class MockCCore:
    def __init__(self):
        self.cycle = 0
        self.fitness = 0.5
        self.balance = 0.5
        self.dimensions = {
            "能力 (C)": 0.5,
            "学习 (L)": 0.4,
            "知识 (K)": 0.5,
            "协调 (O)": 0.6,
            "适应 (A)": 0.5
        }
    
    def heartbeat(self):
        self.cycle += 1
        return {
            "status": "ok",
            "data": {
                "cycle": self.cycle,
                "fitness": self.fitness,
                "balance": self.balance,
                "dimensions": self.dimensions
            }
        }
    
    def detect_weakness(self):
        weakest = min(self.dimensions, key=self.dimensions.get)
        weak_count = sum(1 for v in self.dimensions.values() if v < 0.5)
        return {
            "status": "ok",
            "data": {
                "count": weak_count,
                "weaknesses": weakest
            }
        }
    
    def record_evolution(self, mutations, knowledge, fitness):
        self.fitness = min(self.fitness + fitness * 0.1, 1.0)
        return {"status": "ok"}

# === 知识搜索器 ===
class KnowledgeSearcher:
    def search_arxiv(self, query, max_results=2):
        try:
            encoded_query = urllib.parse.quote(query)
            url = f"https://export.arxiv.org/api/query?search_query={encoded_query}&max_results={max_results}"
            with urllib.request.urlopen(url, timeout=10) as resp:
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
                        entry['title'] = line.replace('<title>', '').replace('</title>', '').strip()
                    elif '<summary>' in line and '</summary>' in line:
                        summary = line.replace('<summary>', '').replace('</summary>', '').strip()
                        entry['summary'] = summary[:150] + "..." if len(summary) > 150 else summary
                    elif '<id>' in line and '</id>' in line:
                        entry['url'] = line.replace('<id>', '').replace('</id>', '').strip()
            
            return results[:max_results]
        except Exception as e:
            print(f"[知识] arXiv 搜索失败: {e}")
            return []
    
    def search_github(self, query, max_results=2):
        try:
            encoded_query = urllib.parse.quote(query)
            url = f"https://api.github.com/search/repositories?q={encoded_query}&per_page={max_results}"
            with urllib.request.urlopen(url, timeout=10) as resp:
                content = resp.read().decode()
            
            data = json.loads(content)
            items = data.get('items', [])
            
            results = []
            for item in items[:max_results]:
                results.append({
                    'title': item.get('name', ''),
                    'summary': (item.get('description', '')[:150] + "...") if item.get('description') else 'No description',
                    'url': item.get('html_url', '')
                })
            
            return results
        except Exception as e:
            print(f"[知识] GitHub 搜索失败: {e}")
            return []
    
    def search(self, topic):
        print(f"[知识] 搜索主题: {topic}")
        arxiv = self.search_arxiv(topic, 2)
        github = self.search_github(topic, 2)
        return {"arxiv": arxiv, "github": github, "total": len(arxiv) + len(github)}

# === 自动决策引擎 ===
class AutoDecisionEngine:
    def __init__(self):
        self.cycles_without_improvement = 0
        self.last_fitness = 0.0
        self.last_domain = ""
    
    def make_decision(self, weaknesses, state):
        self.cycles_without_improvement += 1
        
        fitness = state.get('fitness', 0.5)
        balance = state.get('balance', 0.5)
        weak_list = weaknesses.get('weaknesses', "")
        
        if fitness < 0.3 or balance < 0.2:
            return {
                "action": "recover",
                "domain": "安全",
                "change": 0.05,
                "reason": "系统健康状态异常",
                "confidence": 0.9
            }
        
        if "能力" in weak_list:
            domain = "变异"
            change = 0.15
            reason = "能力维度偏低，需要增加变异技能"
        elif "学习" in weak_list:
            domain = "知识"
            change = 0.12
            reason = "学习能力不足，需要吸收更多知识"
        elif "知识" in weak_list:
            domain = "知识"
            change = 0.10
            reason = "知识储备不足"
        elif "协调" in weak_list:
            domain = "共进化"
            change = 0.08
            reason = "协调能力不足"
        elif "适应" in weak_list:
            domain = "评估"
            change = 0.10
            reason = "适应度低"
        else:
            domains = ["探索", "记忆", "协议", "共进化"]
            domain = random.choice(domains)
            change = 0.06
            reason = "无明显短板，探索新方向"
        
        if domain == self.last_domain and self.cycles_without_improvement < 3:
            domains = ["探索", "记忆", "协议", "共进化"]
            if domain in domains:
                domains.remove(domain)
            domain = domains[0] if domains else "探索"
        
        self.last_domain = domain
        
        if fitness > self.last_fitness + 0.01:
            self.cycles_without_improvement = 0
        self.last_fitness = fitness
        
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

# === 模拟 Rust Engine ===
class MockRustEngine:
    def mutate(self, domain, change):
        gene_id = f"gene-{random.randint(1000, 9999)}-{domain}"
        return {"status": "ok", "data": {"gene_id": gene_id}}
    
    def evaluate(self, gene_id):
        score = random.uniform(0.3, 0.9)
        return {"status": "ok", "data": {"score": score}}
    
    def retain(self, gene_id):
        return {"status": "ok"}

# === 进化循环 ===
def run_evolution_cycles(num_cycles=3):
    print("=" * 60)
    print("  🦾 无 LLM 自主进化测试")
    print("=" * 60)
    
    c_core = MockCCore()
    knowledge = KnowledgeSearcher()
    decision = AutoDecisionEngine()
    rust = MockRustEngine()
    
    for cycle in range(1, num_cycles + 1):
        print(f"\n--- 循环 #{cycle} ---")
        
        # 1. 心跳
        print(f"[1/7] C Core 心跳...")
        state = c_core.heartbeat()["data"]
        print(f"      → 周期: {state['cycle']}, 适应度: {state['fitness']:.4f}")
        
        # 2. 检测短板
        print(f"[2/7] 检测短板...")
        weakness = c_core.detect_weakness()["data"]
        print(f"      → 短板: {weakness['weaknesses']}")
        
        # 3. 互联网知识搜索
        print(f"[3/7] 互联网知识搜索...")
        search_results = knowledge.search(weakness['weaknesses'])
        print(f"      → 找到 {search_results['total']} 条知识")
        for item in search_results.get('arxiv', [])[:2]:
            print(f"        [arXiv] {item.get('title', '')[:40]}...")
        for item in search_results.get('github', [])[:2]:
            print(f"        [GitHub] {item.get('title', '')[:40]}...")
        
        # 4. 自动决策
        print(f"[4/7] 自动决策...")
        dec = decision.make_decision(weakness, state)
        print(f"      → 决策: {dec['domain']} (变化: {dec['change']:.3f})")
        print(f"      → 理由: {dec['reason']}")
        
        # 5. 执行变异
        print(f"[5/7] Rust Engine 变异...")
        mutate_result = rust.mutate(dec['domain'], dec['change'])
        gene_id = mutate_result['data']['gene_id']
        print(f"      → 基因: {gene_id}")
        
        # 6. 评估
        print(f"[6/7] Rust Engine 评估...")
        eval_result = rust.evaluate(gene_id)
        score = eval_result['data']['score']
        print(f"      → 评估分: {score:.4f}")
        if score > 0.5:
            rust.retain(gene_id)
            print(f"      → ✅ 保留基因")
            
            # 更新维度
            for dim_name in c_core.dimensions:
                if dec['domain'] in dim_name or dim_name in dec['domain']:
                    c_core.dimensions[dim_name] = min(1.0, c_core.dimensions[dim_name] + dec['change'])
                    print(f"      → 更新 {dim_name}: {c_core.dimensions[dim_name]:.3f}")
        
        # 7. 记录
        print(f"[7/7] 记录进化状态...")
        c_core.record_evolution(1, search_results['total'], dec['change'])
        print(f"      → 新适应度: {c_core.fitness:.4f}")
    
    print("\n" + "=" * 60)
    print("  进化完成！")
    print("=" * 60)
    print(f"最终状态:")
    print(f"  周期: {c_core.cycle}")
    print(f"  适应度: {c_core.fitness:.4f}")
    print(f"  维度: {json.dumps(c_core.dimensions, indent=2, ensure_ascii=False)}")

if __name__ == "__main__":
    run_evolution_cycles(3)