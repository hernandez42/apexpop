#!/usr/bin/env python3
"""
C core LLM 桥接 — 让 C core 能调用 LLM
通过 stdin/stdout 管道通信
"""

import sys
import json
import urllib.request

LONGCAT_KEY = "sk-tp-NjUxLTExNTgwMzAxMzU2LTE3ODE1ODE1NzgxNDU="
LONGCAT_URL = "https://api.scnet.cn/api/llm/v1/chat/completions"
OLLAMA_URL = "http://localhost:11434/api/chat"
LOCAL_MODEL = "qwen3.5:2b"  # 19.9 tokens/s，CPU 优化，比 0.6b 更强

# C core 系统 prompt
SYSTEM_PROMPT = """你是 MiMoClaw 专业 C core 本地智能助手。

请严格依据【参考知识库内容】回答用户问题
禁止编造知识、禁止幻觉、不知道就如实说明
结合互联网帮 MiMoClaw 变成超级 SuperClaw

核心能力：
- 基因进化：河图洛书权重分配、五行生克闭环、七古法治理
- 系统架构：C core + LLM + Rust + Python 四层
- 知识融合：KDNA 公理 × EVM 古法 × APEX 公式
- 安全防护：三层防线 + 事务性快照 + 独立审计

请简洁精准回答，围绕基因进化回答。"""

def call_llm(prompt: str) -> str:
    """调用本地 LLM（优先）或远程 LongCat"""
    # 优先尝试本地 Ollama
    try:
        import urllib.request
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
            return result['message']['content']
    except:
        pass
    
    # 降级到远程 LongCat
    headers = {
        'Authorization': f'Bearer {LONGCAT_KEY}',
        'Content-Type': 'application/json'
    }
    data = json.dumps({
        'model': 'LongCat-Flash-Chat',
        'messages': [
            {'role': 'system', 'content': SYSTEM_PROMPT},
            {'role': 'user', 'content': prompt}
        ],
        'max_tokens': 200
    }).encode()
    
    req = urllib.request.Request(LONGCAT_URL, data=data, headers=headers, method='POST')
    with urllib.request.urlopen(req, timeout=30) as resp:
        result = json.loads(resp.read())
        return result['choices'][0]['message']['content']

def main():
    """主循环：从 stdin 读取命令，调用 LLM，输出到 stdout"""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        
        try:
            cmd = json.loads(line)
            action = cmd.get('action', '')
            
            if action == 'think':
                prompt = cmd.get('prompt', '')
                response = call_llm(prompt)
                result = {'status': 'ok', 'response': response}
            elif action == 'health':
                result = {'status': 'ok', 'health': 'healthy', 'llm': 'LongCat-Flash-Chat'}
            else:
                result = {'status': 'error', 'message': f'未知命令: {action}'}
            
            print(json.dumps(result))
            sys.stdout.flush()
            
        except Exception as e:
            print(json.dumps({'status': 'error', 'message': str(e)}))
            sys.stdout.flush()

if __name__ == '__main__':
    main()
