# Zircon 熵世界 — 架构规范

## 三层架构

```
┌─────────────────────────────────────────────┐
│              C core 层（恒定锚）              │
│  回音壁 + 知识库 + 基因库 + 身份锚定          │
│  作用：记忆、身份、健康、自修复                │
│  特点：不变、恒定、永远在线                    │
├─────────────────────────────────────────────┤
│              Python 层（灵活执行）             │
│  吞噬引擎 + 生产线 + 天兵天将                 │
│  作用：搜索、分析、入库、融合                  │
│  特点：灵活、可扩展、快速迭代                  │
├─────────────────────────────────────────────┤
│              Rust 层（高性能引擎）             │
│  驱动 Python + 基因计算 + 洛书平衡            │
│  作用：计算、评估、筛选、存储                  │
│  特点：快速、安全、可靠                        │
└─────────────────────────────────────────────┘
```

## CLI 标准

所有组件通过 JSON 行协议通信：

### C core → Python
```json
{"cmd": "heartbeat"}
{"cmd": "detect_weakness"}
{"cmd": "health_check"}
```

### Python → Rust
```json
{"cmd": "mutate", "domain": "变异", "change": 0.1}
{"cmd": "evaluate", "gene_id": "gene-001"}
{"cmd": "retain", "gene_id": "gene-001"}
{"cmd": "balance"}
```

### Rust → C core
```json
{"status": "ok", "genes": 63, "balance": 0.85}
{"status": "error", "message": "mutation too large"}
```

## 数据流

```
输入 → Python(吞噬) → Rust(评估) → C core(存储)
  ↑                                        │
  └────────── 回音壁增强 ←─────────────────┘
```

## 规范文件

- SOUL.md — 身份和行为准则（只读）
- SECURITY-BOUNDARY.md — 安全边界（只读）
- AGENTS.md — 行为规则
- PROTOCOL.md — 通信协议
- ARCHITECTURE.md — 架构规范（本文件）
- GENE-KNOWLEDGE-MAP.md — 基因知识映射
- KNOWLEDGE-GRAPH.md — 知识图谱

## 循环节奏

- C core：每秒心跳
- Python：每5分钟一轮
- Rust：实时响应
- 回音壁：每小时回响
- 健康检查：每循环一次
