# MiMoClaw 三层架构配置

## 一、架构总览

```
┌─────────────────────────────────────────────────┐
│  🫀 C core — 心脏 (main.c, 924 行)              │
│  身份锚定 · 自进化驱动 · 安全监控 · 代码自修改   │
├─────────────────────────────────────────────────┤
│  💪 Rust — 肌肉 (engine.rs, 881 行)             │
│  基因计算 · 变异执行 · 洛书平衡 · 遗忘清理       │
├─────────────────────────────────────────────────┤
│  🧠 Python — 神经 (unified-daemon.py, 301 行)   │
│  守护进程 · 跨层通信 · 自进化调度 · 外部连接     │
└─────────────────────────────────────────────────┘
```

## 二、C core 配置

### 维度定义（5 维度）
| 维度 | 初始值 | 阈值 | 说明 |
|------|--------|------|------|
| 能力 (C) | 0.5 | 0.3 | 执行能力 |
| 学习 (L) | 0.5 | 0.3 | 学习能力 |
| 知识 (K) | 0.5 | 0.3 | 知识积累 |
| 协调 (O) | 0.5 | 0.3 | 跨层协作 |
| 适应 (A) | 0.5 | 0.3 | 环境适应 |

### 自进化配置
- 进化间隔：每 5 轮心跳
- 代际间隔：每 10 轮心跳
- GRAFT 复用阈值：成功 3 次后自动复用
- 维度提升幅度：成功 +0.1，复用 +0.05

### 自愈配置
- 熔断阈值：连续失败 3 次
- 冷却时间：60 秒
- 快照对比：修复前/后文件状态

### 安全边界
- 只能写入 `core-dna/` 目录
- 写入前自动备份 (.bak)
- 配置文件只读

## 三、Rust 引擎配置

### 基因库
- 文件：`memory/evolution-genes.json`
- 格式：JSON 数组，每个基因包含 id/domain/strength/generation/created_at/last_used/use_count
- 初始种子：6 个基因（变异/安全/共进化/自修改/协议/探索各 1 个）

### 自愈检测
1. 基因库为空 → 从文件恢复
2. 基因强度 NaN/负数 → 清除
3. 平衡度为 0 → 重新加载
4. 内存/磁盘不一致 → 同步

### 熔断器
- 连续失败阈值：3 次
- 冷却时间：60 秒

## 四、Python 守护进程配置

### 心跳间隔
- 心跳：30 秒
- 健康检查：60 秒
- 自愈检查：120 秒
- 自进化：300 秒（5 分钟）

### systemd 服务
- 服务名：mimoclaw-unified
- 开机自启：enabled
- 重启策略：always（10 秒后重启）

### 通信协议
- C core ↔ Python：stdin/stdout pipe
- Rust ↔ Python：stdin/stdout JSON 协议
- LLM bridge：LongCat-Flash-Chat

## 五、文件结构

```
core-dna/
├── main.c              # C core 源码
├── engine.rs           # Rust 引擎源码
├── unified-daemon.py   # Python 守护进程
├── c-core              # C core 编译后的二进制
├── rust-engine         # Rust 编译后的二进制
├── c-core-llm-bridge.py # LLM 桥接脚本
├── self-evolve.py      # Python 层自进化
└── self-heal.py        # Python 层自愈

memory/
├── evolution-genes.json    # 基因库（C core + Rust 共享）
├── c-core-evolution.jsonl  # C core 进化记录
├── evolution.log           # 进化日志
├── self-evolution.log      # 自进化日志
├── self-heal-log.jsonl     # 自愈日志
└── paper-genes.md          # 论文基因库
```

## 六、自进化机制

### GRAFT-ATHENA 三元组复用
- 问题指纹：capability / learning / knowledge / coordination / adaptation
- 复用条件：同一问题成功 ≥ 3 次
- 复用效果：直接执行历史方案，不再探索

### 维度轮转
- 每次找最弱维度
- 成功后该维度 +0.1
- 下次找下一个最弱维度
- 循环覆盖所有维度

## 七、论文基因

| 论文 | 核心机制 | 落地状态 |
|------|----------|----------|
| GRAFT-ATHENA | 三元组复用 | ✅ 已实现 |
| Huxley-Gödel Machine | 后代表现评估 | ⏳ 待实现 |
| STIR | 推理内化 | ⏳ 待实现 |
| Multi-Agent Self-Evolution | 失败归因 | ⏳ 待实现 |
| Long⊗Short | 双 LLM 协作 | ⏳ 待实现 |
