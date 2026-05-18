#!/usr/bin/env python3
"""
LongCat 工人 — 调用 LongCat 执行各种任务
我负责指挥，它负责干活
"""

import urllib.request
import json
from pathlib import Path

MEMORY_DIR = Path("/root/.openclaw/workspace/memory")
LONGCAT_KEY = "ak_2iC5SD91p9eW3IE3YN6rZ6bV40N9Q"
LONGCAT_URL = "https://api.longcat.chat/openai/v1/chat/completions"

def call_longcat(task, max_tokens=500):
    """调用 LongCat 执行任务"""
    headers = {
        'Authorization': f'Bearer {LONGCAT_KEY}',
        'Content-Type': 'application/json'
    }
    data = json.dumps({
        'model': 'LongCat-Flash-Chat',
        'messages': [{'role': 'user', 'content': task}],
        'max_tokens': max_tokens
    }).encode()
    
    req = urllib.request.Request(LONGCAT_URL, data=data, headers=headers, method='POST')
    with urllib.request.urlopen(req, timeout=60) as resp:
        result = json.loads(resp.read())
        return result['choices'][0]['message']['content']

# === 任务类型 ===

def scan_security(code):
    """安全扫描"""
    return call_longcat(f"分析以下代码的安全风险，列出所有漏洞和修复建议：\n{code}")

def evaluate_gene(gene_name, gene_content):
    """基因评估"""
    return call_longcat(f"评估以下基因的质量（0-1分），给出评分和改进建议：\n基因名：{gene_name}\n内容：{gene_content}")

def understand_paper(paper_text):
    """论文理解"""
    return call_longcat(f"深入理解以下论文的核心机制，提取可迁移的 Insight：\n{paper_text[:2000]}")

def generate_code(description):
    """代码生成"""
    return call_longcat(f"根据以下描述生成 Python 代码：\n{description}")

def review_code(code):
    """代码审查"""
    return call_longcat(f"审查以下代码，找出 bug 和改进点：\n{code}")

def summarize(text):
    """摘要"""
    return call_longcat(f"用 3 句话总结以下内容：\n{text[:1000]}")

# === 主函数 ===
if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 3:
        print("用法: python3 longcat-worker.py <任务类型> <内容>")
        print("任务类型: security, evaluate, paper, code, review, summary")
        sys.exit(1)
    
    task_type = sys.argv[1]
    content = sys.argv[2]
    
    if task_type == "security":
        result = scan_security(content)
    elif task_type == "evaluate":
        result = evaluate_gene("unknown", content)
    elif task_type == "paper":
        result = understand_paper(content)
    elif task_type == "code":
        result = generate_code(content)
    elif task_type == "review":
        result = review_code(content)
    elif task_type == "summary":
        result = summarize(content)
    else:
        print(f"未知任务类型: {task_type}")
        sys.exit(1)
    
    print(result)
