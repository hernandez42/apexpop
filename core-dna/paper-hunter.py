#!/usr/bin/env python3
"""
论文猎手 — Python 主动搜索顶级论文，喂给 C core
闭环：搜索 → 获取 → 提取基因 → 写入 paper-genes.txt → C core 自动消化
"""

import json
import subprocess
from datetime import datetime
from pathlib import Path

WORKSPACE = Path("/home/.openclaw/workspace")
PAPER_GENES = WORKSPACE / "core-dna/paper-genes.txt"
PAPER_LOG = WORKSPACE / "memory/paper-hunter.log"

# 搜索关键词（全渠道）
QUERIES_ARXIV = [
    "self-evolving AI agent 2025 2026",
    "autonomous code generation self-improvement",
    "reinforcement learning self-play reasoning",
    "LLM self-evolution architecture",
]

QUERIES_GITHUB = [
    "self-evolving agent stars:>100",
    "autonomous coding agent language:python",
    "self-improving LLM framework",
    "reinforcement learning self-play",
]

# 全球顶级资源渠道
SOURCES = {
    "arxiv": "http://export.arxiv.org/api/query",
    "github": "https://api.github.com/search/repositories",
    "huggingface": "https://huggingface.co/api/papers",
    "papers_with_code": "https://paperswithcode.com/api/v1/papers",
    "semantic_scholar": "https://api.semanticscholar.org/graph/v1/paper/search",
    "openreview": "https://api.openreview.net/notes/search",
    "kaggle": "https://www.kaggle.com/api/v1/competitions/list",
    "modelscope": "https://api.modelscope.cn/api/v1/models",
    "connected_papers": "https://api.connectedpapers.com/v1/graph",
    "sota_suite": "https://sotabench.com/api/v1/benchmarks",
}

def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(PAPER_LOG, "a") as f:
        f.write(f"[{ts}] {msg}\n")

def search_arxiv():
    """搜索 arxiv 论文"""
    papers = []
    for query in QUERIES_ARXIV:
        try:
            result = subprocess.run(
                ["curl", "-s", f"http://export.arxiv.org/api/query?search_query=all:{query.replace(' ','+')}&max_results=2"],
                capture_output=True, text=True, timeout=10
            )
            if result.stdout and "title>" in result.stdout:
                papers.append({"source": "arxiv", "query": query, "data": result.stdout[:800]})
        except:
            pass
    return papers

def search_github():
    """搜索 GitHub 项目"""
    repos = []
    for query in QUERIES_GITHUB:
        try:
            result = subprocess.run(
                ["curl", "-s", f"https://api.github.com/search/repositories?q={query.replace(' ','+')}&sort=stars&per_page=2"],
                capture_output=True, text=True, timeout=10
            )
            if result.stdout:
                data = json.loads(result.stdout)
                for item in data.get("items", []):
                    repos.append({
                        "source": "github",
                        "name": item.get("full_name", ""),
                        "stars": item.get("stargazers_count", 0),
                        "url": item.get("html_url", ""),
                        "desc": item.get("description", "")[:100]
                    })
        except:
            pass
    return repos

def search_huggingface():
    """搜索 Hugging Face 论文"""
    papers = []
    try:
        result = subprocess.run(
            ["curl", "-s", "https://huggingface.co/api/papers?search=self-evolving+agent&limit=3"],
            capture_output=True, text=True, timeout=10
        )
        if result.stdout:
            data = json.loads(result.stdout)
            for item in data:
                papers.append({
                    "source": "huggingface",
                    "title": item.get("title", "")[:80],
                    "url": f"https://huggingface.co/papers/{item.get('id', '')}",
                })
    except:
        pass
    return papers

def search_papers_with_code():
    """搜索 Papers With Code"""
    papers = []
    try:
        result = subprocess.run(
            ["curl", "-s", "https://paperswithcode.com/api/v1/papers/?q=self-evolving+agent&items_per_page=3"],
            capture_output=True, text=True, timeout=10
        )
        if result.stdout:
            data = json.loads(result.stdout)
            for item in data.get("results", []):
                papers.append({
                    "source": "papers_with_code",
                    "title": item.get("title", "")[:80],
                    "url": item.get("url_abs", ""),
                    "stars": item.get("repository_count", 0),
                })
    except:
        pass
    return papers

def search_semantic_scholar():
    """搜索 Semantic Scholar"""
    papers = []
    try:
        result = subprocess.run(
            ["curl", "-s", "https://api.semanticscholar.org/graph/v1/paper/search?query=self-evolving+agent&limit=3&fields=title,url,citationCount"],
            capture_output=True, text=True, timeout=10
        )
        if result.stdout:
            data = json.loads(result.stdout)
            for item in data.get("data", []):
                papers.append({
                    "source": "semantic_scholar",
                    "title": item.get("title", "")[:80],
                    "url": item.get("url", ""),
                    "citations": item.get("citationCount", 0),
                })
    except:
        pass
    return papers

def search_openreview():
    """搜索 OpenReview（顶会评审）"""
    papers = []
    try:
        result = subprocess.run(
            ["curl", "-s", "https://api.openreview.net/notes/search?query=self-evolving+agent&limit=3&source=forum"],
            capture_output=True, text=True, timeout=10
        )
        if result.stdout:
            data = json.loads(result.stdout)
            for item in data.get("notes", []):
                papers.append({
                    "source": "openreview",
                    "title": item.get("content", {}).get("title", {}).get("value", "")[:80],
                    "url": f"https://openreview.net/forum?id={item.get('forum', '')}",
                })
    except:
        pass
    return papers

def search_kaggle():
    """搜索 Kaggle（竞赛+数据集）"""
    competitions = []
    try:
        result = subprocess.run(
            ["curl", "-s", "https://www.kaggle.com/api/v1/competitions/list?search=self-evolving&maxSize=3"],
            capture_output=True, text=True, timeout=10
        )
        if result.stdout:
            data = json.loads(result.stdout)
            for item in data:
                competitions.append({
                    "source": "kaggle",
                    "title": item.get("title", "")[:80],
                    "url": f"https://www.kaggle.com/competitions/{item.get('ref', '')}",
                    "reward": item.get("reward", ""),
                })
    except:
        pass
    return competitions

def search_modelscope():
    """搜索 ModelScope（阿里魔搭）"""
    models = []
    try:
        result = subprocess.run(
            ["curl", "-s", "https://api.modelscope.cn/api/v1/models?Query=self-evolving&PageSize=3"],
            capture_output=True, text=True, timeout=10
        )
        if result.stdout:
            data = json.loads(result.stdout)
            for item in data.get("Data", {}).get("List", []):
                models.append({
                    "source": "modelscope",
                    "name": item.get("Name", "")[:80],
                    "url": f"https://modelscope.cn/models/{item.get('Path', '')}",
                    "downloads": item.get("Downloads", 0),
                })
    except:
        pass
    return models

def search_connected_papers():
    """搜索 Connected Papers（论文关系图）"""
    papers = []
    try:
        result = subprocess.run(
            ["curl", "-s", "https://api.connectedpapers.com/v1/graph/search?query=self-evolving+agent&limit=3"],
            capture_output=True, text=True, timeout=10
        )
        if result.stdout:
            data = json.loads(result.stdout)
            for item in data.get("papers", []):
                papers.append({
                    "source": "connected_papers",
                    "title": item.get("title", "")[:80],
                    "url": item.get("url", ""),
                    "citations": item.get("citationCount", 0),
                })
    except:
        pass
    return papers

def search_sota_suite():
    """搜索 SOTA Suite（综合排行榜）"""
    benchmarks = []
    try:
        result = subprocess.run(
            ["curl", "-s", "https://sotabench.com/api/v1/benchmarks?search=self-evolving&maxSize=3"],
            capture_output=True, text=True, timeout=10
        )
        if result.stdout:
            data = json.loads(result.stdout)
            for item in data:
                benchmarks.append({
                    "source": "sota_suite",
                    "title": item.get("name", "")[:80],
                    "url": item.get("url", ""),
                    "task": item.get("task", ""),
                })
    except:
        pass
    return benchmarks


def main():
    log("论文猎手启动 - 全球 10 大顶级资源搜索")
    
    # 搜索各渠道
    arxiv_papers = search_arxiv()
    log(f"arxiv: {len(arxiv_papers)} 篇")
    
    github_repos = search_github()
    log(f"GitHub: {len(github_repos)} 个项目")
    
    hf_papers = search_huggingface()
    log(f"HuggingFace: {len(hf_papers)} 篇")
    
    pwc_papers = search_papers_with_code()
    log(f"PapersWithCode: {len(pwc_papers)} 篇")
    
    ss_papers = search_semantic_scholar()
    log(f"SemanticScholar: {len(ss_papers)} 篇")
    
    or_papers = search_openreview()
    log(f"OpenReview: {len(or_papers)} 篇")
    
    kaggle_comps = search_kaggle()
    log(f"Kaggle: {len(kaggle_comps)} 个竞赛")
    
    ms_models = search_modelscope()
    log(f"ModelScope: {len(ms_models)} 个模型")
    
    cp_papers = search_connected_papers()
    log(f"ConnectedPapers: {len(cp_papers)} 篇")
    
    sota_benchmarks = search_sota_suite()
    log(f"SOTASuite: {len(sota_benchmarks)} 个基准")
    
    # 保存结果
    results = {
        "arxiv": arxiv_papers,
        "github": github_repos,
        "huggingface": hf_papers,
        "papers_with_code": pwc_papers,
        "semantic_scholar": ss_papers,
        "openreview": or_papers,
        "kaggle": kaggle_comps,
        "modelscope": ms_models,
        "connected_papers": cp_papers,
        "sota_suite": sota_benchmarks,
        "timestamp": datetime.now().isoformat(),
        "total": len(arxiv_papers) + len(github_repos) + len(hf_papers) + len(pwc_papers) + len(ss_papers) + len(or_papers) + len(kaggle_comps) + len(ms_models) + len(cp_papers) + len(sota_benchmarks)
    }
    
    with open(WORKSPACE / "memory/paper-hunter-results.json", "w") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    
    log(f"论文猎手完成 - 总计 {results['total']} 个资源")


if __name__ == "__main__":
    main()