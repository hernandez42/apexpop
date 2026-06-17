#!/usr/bin/env python3
"""
混合 LLM 引擎 — 本地 + LongCat 组合
简单任务用本地（快），复杂任务用 LongCat（强）
"""

import json
import urllib.request

# 配置
OLLAMA_URL = "http://localhost:11434/api/chat"
LONGCAT_URL = "https://api.scnet.cn/api/llm/v1/chat/completions"
LONGCAT_KEY = "sk-tp-NjUxLTExNTgwMzAxMzU2LTE3ODE1ODE1NzgxNDU="
LOCAL_MODEL = "qwen3.5:2b"
REMOTE_MODEL = "LongCat-Flash-Chat"

# 系统 prompt
SYSTEM_PROMPT = """你是 MiMoClaw 智能助手。严格依据知识库回答，禁止编造。"""

# 复杂度判断
COMPLEX_KEYWORDS = ["分析", "推理", "证明", "推导", "比较", "评估", "设计", "架构", "优化", "融合"]

def judge_complexity(prompt):
    """判断任务复杂度"""
    score = 0
    for kw in COMPLEX_KEYWORDS:
        if kw in prompt:
            score += 1
    if len(prompt) > 100:
        score += 1
    return score >= 2  # 2个以上关键词 = 复杂任务

def call_local(prompt):
    """调用本地模型"""
    data = json.dumps({
        'model': LOCAL_MODEL,
        'messages': [
            {'role': 'system', 'content': SYSTEM_PROMPT},
            {'role': 'user', 'content': prompt}
        ],
        'stream': False
    }).encode()
    req = urllib.request.Request(OLLAMA_URL, data=data, method='POST')
    with urllib.request.urlopen(req, timeout=30) as resp:
        result = json.loads(resp.read())
        return result['message']['content'], "local"

def call_remote(prompt):
    """调用 LongCat"""
    headers = {
        'Authorization': f'Bearer {LONGCAT_KEY}',
        'Content-Type': 'application/json'
    }
    data = json.dumps({
        'model': REMOTE_MODEL,
        'messages': [
            {'role': 'system', 'content': SYSTEM_PROMPT},
            {'role': 'user', 'content': prompt}
        ],
        'max_tokens': 300
    }).encode()
    req = urllib.request.Request(LONGCAT_URL, data=data, headers=headers, method='POST')
    with urllib.request.urlopen(req, timeout=30) as resp:
        result = json.loads(resp.read())
        return result['choices'][0]['message']['content'], "remote"

def hybrid_call(prompt):
    """混合调用：自动选择本地或远程"""
    is_complex = judge_complexity(prompt)
    
    if is_complex:
        try:
            response, source = call_remote(prompt)
            return response, source
        except:
            response, source = call_local(prompt)
            return response, f"{source}(fallback)"
    else:
        try:
            response, source = call_local(prompt)
            return response, source
        except:
            response, source = call_remote(prompt)
            return response, f"{source}(fallback)"

if __name__ == "__main__":
    import sys
    prompt = sys.argv[1] if len(sys.argv) > 1 else "你好"
    response, source = hybrid_call(prompt)
    print(f"[{source}] {response}")
