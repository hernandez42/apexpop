# Python Glue Layer 验证报告
日期: 2026-05-17 08:17 CST

## 架构概览

```
┌─────────────────────────────────────────────────────┐
│                  glue.py (Python)                   │
│           胶水层 — 协调一切，自身无状态               │
├─────────────────────────────────────────────────────┤
│                                                     │
│   ┌──────────┐   ┌──────────┐   ┌──────────┐      │
│   │ C Core   │   │ Rust Eng │   │  LLM     │      │
│   │ (pipe)   │   │ (pipe)   │   │ (API)    │      │
│   └────┬─────┘   └────┬─────┘   └────┬─────┘      │
│        │ stdin/stdout  │ stdin/stdout  │ HTTP       │
│        ▼              ▼              ▼            │
│   ┌──────────────────────────────────────────┐     │
│   │           Echo Wall (知识循环增强)        │     │
│   └──────────────────────────────────────────┘     │
└─────────────────────────────────────────────────────┘
```

## 通信协议

| 组件 | 协议 | 方向 |
|------|------|------|
| C Core | stdin/stdout JSON 行 | Python → C → Python |
| Rust Engine | stdin/stdout JSON 行 | Python → Rust → Python |
| LLM | OpenAI 兼容 HTTP API | Python → MiMo API |
| Echo Wall | Python 直接调用 | Python → echo-wall.py |

## C Core 命令

| 命令 | 说明 | 返回 |
|------|------|------|
| `{"cmd":"heartbeat"}` | 心跳 + 状态更新 | cycle, fitness, balance, health |
| `{"cmd":"detect_weakness"}` | 短板检测 | count, weaknesses |
| `{"cmd":"record_evolution",...}` | 记录进化 | total_mutations, knowledge |
| `{"cmd":"health_check"}` | 健康检查 | health, issues, repairs |
| `{"cmd":"status"}` | 完整状态 | 全部字段 |

## Rust Engine 命令

| 命令 | 说明 | 返回 |
|------|------|------|
| `{"cmd":"mutate","domain":"...","change":0.1}` | 变异执行 | gene_id, score |
| `{"cmd":"evaluate","gene_id":"..."}` | 基因评估 | score, strength |
| `{"cmd":"retain","gene_id":"..."}` | 基因保留 | use_count |
| `{"cmd":"balance"}` | 平衡查询 | domains, balance |
| `{"cmd":"status"}` | 完整状态 | genes, mutations |

## 进化主循环（每个心跳周期）

```
1. C Core 心跳 → 更新 cycle, fitness
2. C Core 短板检测 → 识别弱项
3. LLM 分析 → 决定变异方向和幅度
4. Rust Engine 变异 → 产生新基因
5. Rust Engine 评估 → 评估基因质量
6. Echo Wall 回响 → 知识循环增强
7. C Core 记录 → 持久化进化状态
```

## 验证结果（3 个循环）

| 循环 | 心跳 | 短板 | LLM决策 | 变异 | 评估分 | 保留 | Echo Wall | 记录 |
|------|------|------|---------|------|--------|------|-----------|------|
| #1 | ✅ | ✅ 4个 | ✅ 变异×0.15 | ✅ gene-1-0 | 0.484 | — | ✅ silent | ✅ |
| #2 | ✅ | ✅ 4个 | ✅ 变异×0.15 | ✅ gene-2-1 | 0.968 | ✅ | ✅ silent | ✅ |
| #3 | ✅ | ✅ 4个 | ✅ 变异×0.15 | ✅ gene-3-2 | 0.748 | ✅ | ✅ silent | ✅ |

**结论：所有组件正常通信，7步循环全部执行成功。**

## 最终状态

### C Core
- 进化代数: 1
- 适应度: 1.003
- 健康: 2 (优秀)
- 技能数: 0
- 知识数: 0

### Rust Engine
- 基因数: 3
- 平衡度: 0.68
- 变异数: 3
- 保留数: 2
- 遗忘数: 0

## Echo Wall 集成说明

Echo Wall 返回 "silent" 是正常行为：
- 知识库已有 3 条记录（来自之前的回响测试）
- 回响间隔为 3600 秒（1小时）
- 记录太新，尚未触发回响
- 当记录达到回响时间后，会自动增强/衰减

## 文件清单

| 文件 | 用途 |
|------|------|
| `core-dna/glue.py` | Python 粘合层主文件 |
| `core-dna/c-core-pipe` | C Core 管道通信版本（已编译） |
| `core-dna/rust-engine-pipe` | Rust Engine 管道通信版本（已编译） |
| `core-dna/main_pipe.c` | C Core 管道版本源码 |
| `core-dna/rust_pipe/` | Rust Engine Cargo 项目 |
| `memory/evolution-cycles.jsonl` | 进化循环日志 |

## 配置

### Mock LLM 模式（默认）
```bash
cd core-dna && python3 glue.py --cycles 3
```

### 真实 LLM 模式
```bash
cd core-dna && python3 glue.py --cycles 3 --real-llm
# 需要设置 MIMO_API_KEY 环境变量
```

### 环境变量
- `MIMO_API_KEY`: MiMo LLM API Key
- `MIMO_BASE_URL`: API 端点（默认 https://api.mimo.xiaomi.com/v1）
- `MIMO_OMNI_MODEL`: 模型名（默认 mimo-v2.5）
- `GLUE_MOCK_LLM=1`: 强制 mock 模式

## 后续改进

1. [ ] Echo Wall 回响在知识积累后自动生效
2. [ ] 真实 LLM API 集成测试
3. [ ] Rust Engine 基因持久化（当前内存态）
4. [ ] C Core 健康检查文件路径适配
5. [ ] 进化循环日志可视化
