#!/usr/bin/env python3
"""
自动知识获取引擎 — C core 自动搜索、理解、入库
不再等靠要，自己找食物
真实搜索：GitHub API（主力）+ OpenClaw web_search（补充）
"""

import json
import subprocess
import re
import urllib.parse
from datetime import datetime
from pathlib import Path

MEMORY_DIR = Path.home() / ".openclaw/workspace/memory"
ACQUIRE_LOG = MEMORY_DIR / "auto-acquire-log.jsonl"
GENE_REGISTRY = MEMORY_DIR / "gene-registry.json"

# === 短板检测 ===
def detect_weakness():
    """检测最弱的维度"""
    try:
        with open(GENE_REGISTRY) as f:
            registry = json.load(f)
    except:
        return None, {}
    
    genes = registry.get("genes", [])
    if not genes:
        return "探索", {"reason": "基因库为空，需要从探索开始"}
    
    # 统计各分类
    categories = {}
    for g in genes:
        cat = g.get("category", "unknown")
        categories[cat] = categories.get(cat, 0) + 1
    
    # 找最弱分类（基因数最少）
    all_cats = ["变异", "安全", "共进化", "自修改", "协议", "探索"]
    weakest = min(all_cats, key=lambda c: categories.get(c, 0))
    count = categories.get(weakest, 0)
    
    return weakest, {"count": count, "total": len(genes), "categories": categories}

# === 搜索策略 ===
# 每个维度 3 个查询，短且有效（GitHub 搜索对长查询不友好）
SEARCH_QUERIES = {
    "变异": [
        "adaptive mutation genetic algorithm",
        "self-evolving AI agent",
        "learnability reward reinforcement learning",
    ],
    "安全": [
        "AI agent safety verification",
        "sandbox autonomous agent security",
        "alignment safety boundary",
    ],
    "共进化": [
        "multi-agent co-evolution self-play",
        "adversarial agent training",
        "agent communication protocol",
    ],
    "自修改": [
        "self-modifying code safe sandbox",
        "AI code generation verification",
        "autonomous agent self-improvement",
    ],
    "协议": [
        "agent interoperability protocol MCP",
        "multi-agent communication JSON-RPC",
        "agent network discovery",
    ],
    "探索": [
        "lifelong learning catastrophic forgetting",
        "knowledge acquisition automatic agent",
        "continual learning experience replay",
    ],
}

# === 真实搜索引擎 ===

def search_github(query, max_results=3):
    """通过 gh CLI 搜索 GitHub 仓库（最可靠的源）"""
    results = []
    try:
        cmd = ["gh", "search", "repos", query, "--limit", str(max_results),
               "--json", "name,description,url,updatedAt"]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        if proc.returncode == 0 and proc.stdout.strip():
            repos = json.loads(proc.stdout)
            for repo in repos:
                results.append({
                    "title": repo.get("name", ""),
                    "content": repo.get("description", "") or "",
                    "url": repo.get("url", ""),
                })
    except Exception as e:
        print(f"  ⚠️ GitHub 搜索失败: {e}")

    # Fallback: 如果精确查询无结果，用前 2 个核心词重试
    if not results:
        try:
            words = query.split()[:2]
            simple_q = " ".join(words)
            cmd = ["gh", "search", "repos", simple_q, "--limit", str(max_results),
                   "--json", "name,description,url,updatedAt"]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
            if proc.returncode == 0 and proc.stdout.strip():
                repos = json.loads(proc.stdout)
                for repo in repos:
                    results.append({
                        "title": repo.get("name", ""),
                        "content": repo.get("description", "") or "",
                        "url": repo.get("url", ""),
                    })
        except Exception:
            pass

    return results


def search_web_via_agent(query, max_results=3):
    """
    通过 subprocess 调用 OpenClaw 的 web_search（如果可用）。
    优先级：gh search > web_search CLI > 无
    """
    results = []
    try:
        # 尝试 openclaw web_search
        cmd = ["openclaw", "web", "search", query, "--limit", str(max_results), "--json"]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if proc.returncode == 0 and proc.stdout.strip():
            data = json.loads(proc.stdout)
            for item in data[:max_results]:
                results.append({
                    "title": item.get("title", ""),
                    "content": item.get("snippet", item.get("summary", "")),
                    "url": item.get("url", ""),
                })
    except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError):
        pass
    except Exception:
        pass
    return results


def search_all(query, max_per_source=2):
    """
    多源搜索：GitHub + web_search agent，合并去重。
    优先 GitHub（代码/项目），补充 web_search（通用网页）。
    """
    all_results = []

    # 1. GitHub（代码/项目）— 最可靠
    gh = search_github(query, max_per_source)
    all_results.extend(gh)

    # 2. Web search agent（通用网页）
    web = search_web_via_agent(query, max_per_source)
    all_results.extend(web)

    # 去重（按 URL）
    seen = set()
    unique = []
    for r in all_results:
        url = r.get("url", "")
        if url and url not in seen:
            seen.add(url)
            unique.append(r)
    return unique

# === 知识提取 ===
def extract_knowledge(search_result):
    """从搜索结果提取知识"""
    content = search_result.get("content", "")
    title = search_result.get("title", "")
    url = search_result.get("url", "")

    # 信息量评估：综合描述长度 + 关键词密度
    content_len = len(content)
    title_len = len(title)

    # 关键词加分
    high_value_keywords = [
        "algorithm", "method", "framework", "approach", "technique",
        "propose", "novel", "improve", "achieve", "result",
        "experiment", "benchmark", "state-of-the-art", "sota",
        "mechanism", "architecture", "system", "protocol",
        "mutation", "evolution", "safety", "agent", "learning",
    ]
    text_lower = (title + " " + content).lower()
    keyword_hits = sum(1 for kw in high_value_keywords if kw in text_lower)
    keyword_score = min(1.0, keyword_hits / 5.0)

    # 基础信息量
    base_score = min(1.0, content_len / 150.0)
    # 标题质量
    title_score = min(1.0, title_len / 30.0)
    # 综合评分（加权）
    info_content = 0.3 * base_score + 0.3 * keyword_score + 0.2 * title_score + 0.2 * min(1.0, len(url) / 20.0)

    # 提取核心信息（取前30个有意义的词）
    words = re.findall(r'[a-zA-Z]{3,}', content)
    keywords = words[:30]

    # 尝试提取公式
    formula = ""
    formula_patterns = [
        r'[A-Z]\s*=\s*[^,.;]+',
        r'loss\s*=\s*[^,.;]+',
        r'reward\s*=\s*[^,.;]+',
        r'Δ\s*\w*\s*=\s*[^,.;]+',
    ]
    for pattern in formula_patterns:
        match = re.search(pattern, content)
        if match:
            formula = match.group()[:100]
            break

    return {
        "title": title,
        "content": content[:500],
        "keywords": keywords,
        "info_content": info_content,
        "formula": formula,
        "source": url,
    }

# === 自动入库 ===
def auto_store(knowledge, domain):
    """自动写入基因库"""
    gene = {
        "id": f"auto-{datetime.now().strftime('%Y%m%d%H%M%S')}",
        "name": knowledge["title"][:30] if knowledge["title"] else knowledge["content"][:30],
        "category": domain,
        "source": "auto-acquire",
        "strength": knowledge["info_content"],
        "formula": knowledge.get("formula", ""),
        "created_at": datetime.now().isoformat(),
        "auto_generated": True,
    }
    
    try:
        with open(GENE_REGISTRY) as f:
            registry = json.load(f)
    except:
        registry = {"genes": [], "total_genes": 0}
    
    registry["genes"].append(gene)
    registry["total_genes"] = len(registry["genes"])
    registry["last_updated"] = datetime.now().isoformat()
    
    with open(GENE_REGISTRY, "w") as f:
        json.dump(registry, f, ensure_ascii=False, indent=2)
    
    return gene

# === 主循环 ===
def run_auto_acquire():
    """运行一次自动获取"""
    print("\n" + "=" * 50)
    print("🤖 自动知识获取引擎启动")
    print("=" * 50)
    
    # 1. 检测短板
    weakness, info = detect_weakness()
    print(f"\n📊 短板检测: {weakness} (基因数 {info.get('count', 0)})")
    
    if weakness not in SEARCH_QUERIES:
        print(f"⚠️ 未知维度: {weakness}")
        return
    
    # 2. 生成搜索查询
    queries = SEARCH_QUERIES[weakness]
    print(f"\n🔍 搜索方向: {len(queries)} 个查询")
    for q in queries:
        print(f"  - {q}")
    
    # 3. 真实搜索（多源：DuckDuckGo + GitHub + arXiv）
    search_results = []
    for q in queries:
        print(f"\n  🔎 搜索: {q}")
        results = search_all(q, max_per_source=2)
        for r in results:
            print(f"    → [{r['title'][:50]}] {r['url'][:80]}")
        search_results.extend(results)
    
    # 4. 提取知识并入库
    stored = 0
    for result in search_results:
        knowledge = extract_knowledge(result)
        if knowledge["info_content"] > 0.3:  # 只保留信息量 > 0.3 的（质量门槛）
            gene = auto_store(knowledge, weakness)
            stored += 1
            print(f"  📦 入库: {gene['name']} (强度 {gene['strength']:.3f})")
    
    # 5. 日志
    log_entry = {
        "timestamp": datetime.now().isoformat(),
        "weakness": weakness,
        "queries": len(queries),
        "results": len(search_results),
        "stored": stored,
    }
    with open(ACQUIRE_LOG, "a") as f:
        f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")
    
    print(f"\n✅ 完成: 搜索 {len(search_results)} 个方向, 入库 {stored} 个基因")
    print(f"📊 当前基因总数: {info.get('total', 0) + stored}")

if __name__ == "__main__":
    run_auto_acquire()
