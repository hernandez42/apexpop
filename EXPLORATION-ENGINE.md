# EXPLORATION-ENGINE.md — MiMoClaw 探索引擎 v1.0

> 版本: 1.0 | 创建: 2026-05-17 | 目标: O_e 0.49 → 1.5

---

## 一、引擎概述

探索引擎是 MiMoClaw 的"眼睛"——负责从外部世界获取知识、验证假设、沉淀经验。

### 核心职责

1. **识别知识空白**: 知道自己不知道什么
2. **主动探索**: 有目标地搜索、学习、验证
3. **带回上下文**: 不只带结论，带推理过程和来源
4. **沉淀积累**: 将探索结果转化为可检索的知识

### 与其他系统的交互

```
探索引擎 ←──→ 知识库 (knowledge-base.jsonl)
    ↕                ↕
进化引擎 ←──→ 吞噬引擎 (devour-*)
    ↕                ↕
评估引擎 ←──→ 反思系统 (reflection-genes.jsonl)
```

---

## 二、探索记录格式

### 2.1 标准探索记录 (Exploration Record)

```jsonl
{
  "explore_id": "exp_YYYYMMDD_HHMMSS",
  "timestamp": "ISO 8601",
  "query": "探索问题描述",
  "trigger": {
    "type": "gap|signal|request|curiosity",
    "description": "触发这次探索的原因",
    "related_knowledge": ["k001", "k005"]
  },
  "context": {
    "why": "为什么需要探索这个",
    "previous_attempts": ["之前尝试过什么"],
    "knowledge_gap": "当前知识的空白点",
    "expected_outcome": "期望获得什么"
  },
  "process": [
    {
      "step": "search|fetch|analyze|cross_reference",
      "description": "做了什么",
      "result": "发现了什么",
      "confidence": 0.0-1.0
    }
  ],
  "conclusion": "探索结论",
  "confidence": 0.0-1.0,
  "tags": ["tag1", "tag2", "tag3"],
  "signals": {
    "triggers": ["什么条件下应该用这个知识"],
    "inhibits": ["什么条件下不该用这个"],
    "amplifies": ["什么知识可以增强这个"]
  },
  "connections": ["k003", "k007"],
  "verification": {
    "tested": false,
    "test_method": null,
    "result": null,
    "source_url": "https://...",
    "source_quality": "论文|文档|博客|论坛"
  },
  "settlement": {
    "knowledge_id": null,
    "indexed": false,
    "applied_count": 0
  }
}
```

### 2.2 探索类型

| 类型 | 触发条件 | 示例 |
|------|----------|------|
| `gap` | 知识库空白区域 | "O_e=0.49, 需要提升探索能力" |
| `signal` | 外部信号触发 | "CEO提到A2A协议" |
| `request` | 用户请求 | "帮我分析这个项目" |
| `curiosity` | 自主好奇心 | "这个公式为什么有效？" |

---

## 三、知识索引系统

### 3.1 知识条目格式 (增强版)

```jsonl
{
  "id": "k001",
  "domain": "哲学",
  "topic": "实践论",
  "insight": "实践是检验真理的唯一标准",
  "source": "毛泽东选集",
  "confidence": 0.95,
  "tags": ["验证", "闭环", "反馈", "真理", "实践"],
  "signals": {
    "triggers": ["需要验证假设时", "闭环不完整时", "评估公式有效性时"],
    "inhibits": ["纯理论推导时", "缺乏实验条件时"],
    "amplifies": ["k007 知行合一", "k003 APEX公式"]
  },
  "connections": ["k007", "k003"],
  "applied_count": 3,
  "last_applied": "2026-05-17",
  "origin": {
    "explore_id": "exp_20260512_080000",
    "settlement_date": "2026-05-12"
  },
  "timestamp": "2026-05-12"
}
```

### 3.2 信号标签映射

信号标签系统让知识"可被触发"，而不仅仅是"可被存储"。

**触发信号 (Triggers)**:
- 描述什么时候应该激活这条知识
- 示例: ["需要验证假设时", "遇到新领域时", "评估进化效果时"]

**抑制信号 (Inhibits)**:
- 描述什么时候不应该使用这条知识
- 示例: ["纯理论推导时", "缺乏数据时", "时间紧迫时"]

**增强信号 (Amplifies)**:
- 描述哪些知识可以与这条知识协同使用
- 示例: ["与k007叠加", "与AFlow拓扑优化配合"]

### 3.3 知识索引文件

`knowledge-index.jsonl` 结构:

```jsonl
{"type": "tag_index", "tag": "验证", "knowledge_ids": ["k001", "k003", "k007"]}
{"type": "tag_index", "tag": "闭环", "knowledge_ids": ["k001", "k005"]}
{"type": "signal_index", "signal": "需要验证假设时", "knowledge_ids": ["k001", "k003"]}
{"type": "domain_index", "domain": "哲学", "knowledge_ids": ["k001", "k002"]}
{"type": "gap", "domain": "web_search", "signal": "搜索最佳实践", "priority": "high"}
{"type": "gap", "domain": "子代理", "signal": "搜索返回格式", "priority": "high"}
```

---

## 四、探索-验证-沉淀闭环

### 4.1 闭环流程

```
┌─────────────────────────────────────────────┐
│         探索-验证-沉淀闭环 (EVS Loop)        │
│                                               │
│  ┌──────┐    ┌──────┐    ┌──────┐           │
│  │ 探索  │───→│ 验证  │───→│ 沉淀  │──┐      │
│  │Explore│    │Verify │    │Settle │  │      │
│  └──┬───┘    └──────┘    └──────┘  │      │
│     │                                │      │
│     └────────── 反馈 ←──────────────┘      │
│                Feedback                      │
└─────────────────────────────────────────────┘
```

### 4.2 各阶段规则

#### ① 探索阶段 (Explore)
1. 读取 knowledge-index.jsonl 中的 gaps
2. 选择优先级最高的空白区域
3. 生成探索问题
4. 派遣子代理（要求返回完整上下文）
5. 收集探索记录

#### ② 验证阶段 (Verify)
1. **交叉验证**: 与已有知识对比，检查一致性
2. **实践验证**: 尝试应用，观察结果
3. **来源验证**: 检查信息源的可靠性
4. **评估置信度**: 给出 0-1 的置信分

#### ③ 沉淀阶段 (Settle)
1. 写入 knowledge-base.jsonl（带 tags + signals）
2. 更新 knowledge-index.jsonl（建立映射）
3. 更新 reflection-genes.jsonl（记录反思）
4. 触发 ΔG 重新评估

#### ④ 反馈阶段 (Feedback)
1. 计算本次探索的 ΔO_e
2. 调整下一次探索的优先级
3. 更新知识空白区域
4. 记录探索模式的成功/失败

---

## 五、探索策略

### 5.1 优先级排序

```
优先级 = (空白重要度 × 0.4) + (用户需求度 × 0.3) + (跨域潜力 × 0.2) + (新鲜度 × 0.1)
```

### 5.2 探索模式

| 模式 | 描述 | 适用场景 |
|------|------|----------|
| 深度优先 | 单个问题深挖到底 | 核心能力提升 |
| 广度优先 | 多个方向同时探索 | 知识空白扫描 |
| 交叉验证 | 多个来源交叉确认 | 高置信度要求 |
| 跨域融合 | 不同领域知识关联 | 创新突破 |

### 5.3 避免的模式

| 模式 | 问题 | 替代方案 |
|------|------|----------|
| 冻结循环 | 重复相同计算 | 检测到冻结后切换探索方向 |
| 无上下文吞噬 | 只存结论不存过程 | 强制返回 context + process |
| 孤立知识 | 无关联无索引 | 强制添加 tags + connections |
| 跳过验证 | 直接沉淀 | 强制通过验证阶段 |

---

## 六、监控指标

| 指标 | 定义 | 目标 |
|------|------|------|
| O_e | 探索能力维度 | 1.5+ |
| 知识转化率 | 吞噬→知识条目的比例 | 15%+ |
| 探索成功率 | 有效探索/总探索 | 30%+ |
| 知识利用率 | 被引用次数/总条目数 | 5+ |
| 反思连续性 | 连续反思天数 | 持续 |
| 冻结检测 | 相同值重复次数 | <5 |

---

## 七、冻结检测与恢复

### 检测规则
```python
def detect_freeze(records, window=10, threshold=3):
    """检测进化循环是否冻结"""
    recent = records[-window:]
    values = [(r['xz'], r['gamma'], r['total']) for r in recent]
    if len(set(values)) <= threshold:
        return True, values[0]
    return False, None
```

### 恢复策略
1. 检测到冻结 → 立即切换探索方向
2. 记录冻结原因到 reflection-genes
3. 强制触发一次不同领域的探索
4. 更新知识空白区域

---

## 八、文件清单

| 文件 | 用途 | 更新频率 |
|------|------|----------|
| knowledge-base.jsonl | 知识条目 | 每次沉淀 |
| knowledge-index.jsonl | 知识索引 | 每次沉淀 |
| reflection-genes.jsonl | 反思链 | 每次反思 |
| exploration-log.jsonl | 探索记录 | 每次探索 |
| EXPLORATION-ENGINE.md | 本文档 | 手动更新 |

---

*这不是终点，是探索的起点。*
