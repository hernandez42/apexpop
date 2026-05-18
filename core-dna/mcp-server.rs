/**
 * Zircon MCP Server — 标准 MCP 协议
 * 把 Rust 引擎变成标准 MCP 服务器
 * 任何支持 MCP 的 LLM 都能直接调用
 */

use std::io::{self, BufRead, Write};
use std::collections::HashMap;

// === MCP 标准响应 ===
fn mcp_response(id: Option<&serde_json::Value>, result: serde_json::Value) -> String {
    let mut resp = serde_json::json!({
        "jsonrpc": "2.0",
        "result": result,
    });
    if let Some(id) = id {
        resp["id"] = id.clone();
    }
    resp.to_string()
}

fn mcp_error(id: Option<&serde_json::Value>, code: i32, message: &str) -> String {
    let mut resp = serde_json::json!({
        "jsonrpc": "2.0",
        "error": {"code": code, "message": message},
    });
    if let Some(id) = id {
        resp["id"] = id.clone();
    }
    resp.to_string()
}

// === MCP 工具定义 ===
fn get_tools() -> serde_json::Value {
    serde_json::json!([
        {
            "name": "mutate",
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
            "name": "evaluate",
            "description": "评估基因效果",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "gene_id": {"type": "string", "description": "基因ID"}
                },
                "required": ["gene_id"]
            }
        },
        {
            "name": "balance",
            "description": "计算洛书平衡度",
            "inputSchema": {"type": "object", "properties": {}}
        },
        {
            "name": "status",
            "description": "获取系统状态",
            "inputSchema": {"type": "object", "properties": {}}
        }
    ])
}

// === 主循环 ===
fn main() {
    let stdin = io::stdin();
    let mut stdout = io::stdout();
    
    for line in stdin.lock().lines() {
        let line = match line {
            Ok(l) => l,
            Err(_) => break,
        };
        
        let request: serde_json::Value = match serde_json::from_str(&line) {
            Ok(v) => v,
            Err(_) => {
                writeln!(stdout, "{}", mcp_error(None, -32700, "Parse error")).unwrap();
                continue;
            }
        };
        
        let id = request.get("id");
        let method = request.get("method").and_then(|m| m.as_str()).unwrap_or("");
        
        let response = match method {
            "initialize" => {
                mcp_response(id, serde_json::json!({
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "zircon-evolution", "version": "1.0.0"}
                }))
            }
            "tools/list" => {
                mcp_response(id, serde_json::json!({
                    "tools": get_tools()
                }))
            }
            "tools/call" => {
                let params = request.get("params").unwrap_or(&serde_json::json!({}));
                let tool_name = params.get("name").and_then(|n| n.as_str()).unwrap_or("");
                let args = params.get("arguments").unwrap_or(&serde_json::json!({}));
                
                let result = match tool_name {
                    "mutate" => {
                        let domain = args.get("domain").and_then(|d| d.as_str()).unwrap_or("unknown");
                        let change = args.get("change").and_then(|c| c.as_f64()).unwrap_or(0.0);
                        serde_json::json!({
                            "content": [{"type": "text", "text": format!("变异: {} ({})", domain, change)}]
                        })
                    }
                    "evaluate" => {
                        let gene_id = args.get("gene_id").and_then(|g| g.as_str()).unwrap_or("unknown");
                        serde_json::json!({
                            "content": [{"type": "text", "text": format!("评估: {}", gene_id)}]
                        })
                    }
                    "balance" => {
                        serde_json::json!({
                            "content": [{"type": "text", "text": "洛书平衡度计算"}]
                        })
                    }
                    "status" => {
                        serde_json::json!({
                            "content": [{"type": "text", "text": "系统状态正常"}]
                        })
                    }
                    _ => {
                        writeln!(stdout, "{}", mcp_error(id, -32601, "Method not found")).unwrap();
                        continue;
                    }
                };
                mcp_response(id, result)
            }
            _ => {
                mcp_error(id, -32601, "Method not found")
            }
        };
        
        writeln!(stdout, "{}", response).unwrap();
        stdout.flush().unwrap();
    }
}
