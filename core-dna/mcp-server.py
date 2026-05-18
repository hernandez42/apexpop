#!/usr/bin/env python3
"""
Zircon MCP Server — 标准 MCP 协议
任何支持 MCP 的 LLM 都能调用 Zircon 的能力
"""

import json
import sys
from pathlib import Path

CORE_DIR = Path("/home/.openclaw/workspace/core-dna")
MEMORY_DIR = Path("/root/.openclaw/workspace/memory")

# === MCP 工具定义 ===
TOOLS = [
    {
        "name": "zircon_status",
        "description": "获取 Zircon 系统状态",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "zircon_genes",
        "description": "获取基因库状态",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "zircon_mutate",
        "description": "执行基因变异",
        "inputSchema": {
            "type": "object",
            "properties": {
                "domain": {"type": "string", "description": "变异维度"},
                "change": {"type": "number", "description": "变异幅度"}
            },
            "required": ["domain", "change"]
        }
    },
    {
        "name": "zircon_echo",
        "description": "运行回音壁知识增强",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "zircon_evolve",
        "description": "运行一轮进化",
        "inputSchema": {"type": "object", "properties": {}}
    },
]

# === MCP 响应 ===
def mcp_response(req_id, result):
    return json.dumps({"jsonrpc": "2.0", "id": req_id, "result": result})

def mcp_error(req_id, code, message):
    return json.dumps({"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}})

# === 工具实现 ===
def handle_tool(name, args):
    if name == "zircon_status":
        state_file = MEMORY_DIR / "daemon-state.json"
        if state_file.exists():
            with open(state_file) as f:
                return {"content": [{"type": "text", "text": json.dumps(json.load(f), indent=2)}]}
        return {"content": [{"type": "text", "text": "守护进程未运行"}]}
    
    elif name == "zircon_genes":
        gene_file = MEMORY_DIR / "gene-registry.json"
        if gene_file.exists():
            with open(gene_file) as f:
                r = json.load(f)
            genes = r.get("genes", [])
            cats = {}
            for g in genes:
                c = g.get("category", "?")
                cats[c] = cats.get(c, 0) + 1
            summary = f"总基因: {len(genes)}\n分布: {json.dumps(cats, ensure_ascii=False)}"
            return {"content": [{"type": "text", "text": summary}]}
        return {"content": [{"type": "text", "text": "基因库不存在"}]}
    
    elif name == "zircon_mutate":
        return {"content": [{"type": "text", "text": f"变异: {args.get('domain')} ({args.get('change')})"}]}
    
    elif name == "zircon_echo":
        return {"content": [{"type": "text", "text": "回音壁已触发"}]}
    
    elif name == "zircon_evolve":
        return {"content": [{"type": "text", "text": "进化循环已触发"}]}
    
    return {"content": [{"type": "text", "text": f"未知工具: {name}"}]}

# === MCP 主循环 ===
def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        
        try:
            req = json.loads(line)
        except:
            print(mcp_error(None, -32700, "Parse error"))
            continue
        
        req_id = req.get("id")
        method = req.get("method", "")
        
        if method == "initialize":
            resp = mcp_response(req_id, {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "zircon-evolution", "version": "1.0.0"}
            })
        elif method == "tools/list":
            resp = mcp_response(req_id, {"tools": TOOLS})
        elif method == "tools/call":
            params = req.get("params", {})
            tool_name = params.get("name", "")
            args = params.get("arguments", {})
            result = handle_tool(tool_name, args)
            resp = mcp_response(req_id, result)
        else:
            resp = mcp_error(req_id, -32601, "Method not found")
        
        print(resp)
        sys.stdout.flush()

if __name__ == "__main__":
    main()
