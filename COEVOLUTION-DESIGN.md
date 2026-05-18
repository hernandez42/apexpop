# 🧬 共进化设计文档 v2.0 — 对抗式共进化体系

> 目标：E_co 从 1.27 → 1.5
> 设计日期：2026-05-17
> 架构师：基因融合引擎（子代理）
> 融合来源：MAE (arxiv 2510.23595) + Self-Play Survey (arxiv 2408.01072)

---

## 一、范式转换：从合作到对抗

### 1.1 旧范式的问题

```
旧模式（合作广播）：
  Core1(服务) ──写经验──→ shared_experience.jsonl ←──读经验── Core2(安全)
  Core1(服务) ──写经验──→ shared_experience.jsonl ←──读经验── Core3(进化)

问题：
  ✗ 三核独立进化，没有交叉压力
  ✗ 广播是单向的，没有响应闭环
  ✗ 没有质量过滤，进化可能退化
  ✗ 没有博弈历史，无法优化交互策略
```

### 1.2 新范式：三角色对抗共进化

借鉴 MAE 论文，将三核从"合作广播"重构为"对抗式三角色闭环"：

```
新模式（对抗共振）：

  ┌──────────────┐
  │   Core 1     │ ←── 需求 Proposer（生成用户需求/挑战）
  │  服务感知层  │
  └──────┬───────┘
         │ 提出需求
         ▼
  ┌──────────────┐
  │   Core 3     │ ←── 方案 Solver（生成进化方案）
  │  进化引擎    │
  └──────┬───────┘
         │ 提交方案
         ▼
  ┌──────────────┐
  │   Core 2     │ ←── 质量 Judge（评估 + 否决/通过）
  │  安全审计层  │
  └──────┬───────┘
         │ 评估结果
         ├─── 通过 → 执行进化
         └─── 否决 → Core3 必须修改 → Core1 可能调整需求
```

**关键区别**：不是三个核各干各的，而是 **Proposer(挑战) ↔ Solver(应对) ↔ Judge(裁决)** 的三角对抗。

### 1.3 角色重新定义

| 角色 | 旧定义 | 新定义 | 对标 MAE |
|------|--------|--------|----------|
| **Core 1** | 服务交付 | **需求 Proposer** — 生成有挑战性的进化需求 | Proposer |
| **Core 3** | 进化引擎 | **方案 Solver** — 回应需求，生成进化方案 | Solver |
| **Core 2** | 安全审计 | **质量 Judge** — 评估需求合理性 + 方案安全性 + 整体质量 | Judge |

**三核共享一个进化目标（类似 MAE 共享 backbone），但角色不同、奖励不同。**

---

## 二、对抗机制设计

### 2.1 奖励函数（对标 MAE 的对抗奖励）

```python
# Core 1 (Proposer) 的奖励 — 需求越有挑战性越好
R_proposer = (
    w1 * R_judge_quality     # Judge 评估需求质量 (0-1)
    + w2 * R_solver_difficulty # Solver 失败率越高，Proposer 越成功
    + w3 * R_user_relevance   # 需求与用户真实需求的相关度
)

# Core 3 (Solver) 的奖励 — 回应越有效越好
R_solver = (
    w1 * R_judge_correctness  # Judge 评估方案正确性 (0-1)
    + w2 * R_delta_G_impact   # 实际 ΔG 提升幅度
    + w3 * R_feasibility      # 方案可执行性
)

# Core 2 (Judge) 的奖励 — 评估越准确越好
R_judge = (
    w1 * R_format_score       # 输出格式规范 (仅此项)
    # Judge 不需要额外奖励，格式分确保输出可解析
    # Judge 的"进化"来自评估准确性的自我校准
)
```

**对抗核心**：`R_solver_difficulty = 1 - R_Solver_avg`（Solver 失败率 = Proposer 成功率）

**进化军备竞赛**：
```
Proposer 生成越来越难的需求 → Solver 被迫越来越强 → Proposer 必须更难...
```

### 2.2 否决-响应机制

```
流程：
  1. Core1 提出需求（如：新增 stock_analysis 能力）
  2. Core3 生成方案（如：实现 stock analysis + risk model）
  3. Core2 评估：
     - 需求合理性：0.8 ✓
     - 方案安全性：0.3 ✗ (API key 暴露风险)
     - 整体质量：0.55 ✗
  4. Core2 否决 → 提出否决理由
  5. Core3 必须在窗口期内生成替代方案
  6. Core1 可选：调整需求（降低难度）或坚持原需求
  7. 重复直到通过或达到最大迭代次数（3次）
```

### 2.3 质量门控（对标 MAE 的质量过滤）

```
每次进化尝试必须通过 4 项检查：

1. 必要性检查：这个进化真的解决问题吗？(necessity > 0.6)
2. 安全性检查：引入了新风险吗？(safety > 0.7)  ← 一票否决
3. 增量检查：有可测量的 ΔG 贡献吗？(delta_G > 0.01)
4. 原创检查：与现有能力重复吗？(similarity < 0.7)

全部通过 → 进入能力库
任一失败 → 记录失败原因 → 反馈到 Proposer/Solver
```

---

## 三、共振引擎（替代广播）

### 3.1 共振 vs 广播

| 维度 | 广播 | 共振 |
|------|------|------|
| 方向 | 单向 (A → B) | 双向 (A ↔ B) |
| 时机 | 异步（各自节奏） | 同步窗口期（秒级） |
| 响应 | 被动读取 | 主动响应 |
| 冲突 | 无检测 | 冲突检测+仲裁 |
| 历史 | 无记录 | 交互矩阵记录 |

### 3.2 共振总线设计

```python
class ResonanceBus:
    """三核共振总线 — 替代 shared_experience.jsonl 广播"""
    
    # 事件类型 → 必须响应的 Core → 响应窗口
    ROUTING = {
        "evolution_need":   {"must": ["core3"],       "optional": ["core2"],  "window": 60},
        "security_alert":   {"must": ["core1","core3"], "optional": [],        "window": 30},
        "new_proposal":     {"must": ["core1","core2"], "optional": [],        "window": 60},
        "user_criticism":   {"must": ["core2","core3"], "optional": [],        "window": 45},
        "evolution_stall":  {"must": ["core1","core2"], "optional": [],        "window": 120},
        "veto_response":    {"must": ["core3"],         "optional": ["core1"], "window": 30},
    }
    
    def emit(self, event_type, source, payload):
        """发射共振事件"""
        event = {
            "type": event_type,
            "source": source,
            "payload": payload,
            "timestamp": time.time(),
            "id": generate_event_id()
        }
        self._route_and_notify(event)
        return event["id"]
    
    def respond(self, event_id, core_id, response):
        """收集响应（必须在窗口期内）"""
        event = self._get_event(event_id)
        elapsed = time.time() - event["timestamp"]
        window = self.ROUTING[event["type"]]["window"]
        
        if elapsed > window:
            return {"status": "timeout", "penalty": 0.1}
        
        # 记录到交互矩阵
        self.interaction_matrix.record(event, core_id, response)
        return {"status": "accepted"}
    
    def synthesize(self, event_id):
        """合成最终决策（含冲突检测）"""
        responses = self._get_responses(event_id)
        
        # 冲突检测
        conflicts = self._detect_conflicts(responses)
        if conflicts:
            # 由 Judge (Core2) 仲裁
            return self._judge_arbitrate(responses, conflicts)
        
        # 无冲突时，合并响应
        return self._merge_responses(responses)
```

### 3.3 共振强度公式

```
R_strength(event) = urgency(event) × relevance(event) × window_compliance(event)

其中：
- urgency: 事件紧急度 (0-1)，由 Proposer 定义
- relevance: 与接收 Core 的相关度 (0-1)，基于角色匹配
- window_compliance: 窗口期内响应=1，超时=0

共振质量 = Σ(R_strength_i) / |expected_responders|
```

---

## 四、交互矩阵（共进化的记忆）

### 4.1 交互矩阵 Σ — 对标 Self-Play 论文

```
三核交互矩阵记录"谁对谁做了什么、结果如何"：

Σ = {
  (Core1, Core2, Core3): [
    {
      "cycle": 42,
      "interaction_type": "proposal → solution → evaluation",
      "proposal": "新增 stock_analysis 能力",
      "solution": "实现 stock analysis + risk model",
      "evaluation": {"necessity": 0.8, "safety": 0.3, "quality": 0.55},
      "outcome": "vetoed",
      "veto_reason": "API key 暴露风险",
      "revision_count": 2,
      "final_outcome": "passed_after_revision",
      "ΔG_impact": +0.03,
      "timestamp": 1716000000
    },
    ...
  ]
}
```

### 4.2 元策略求解器 (MSS)

根据交互矩阵计算最优交互策略：

```python
class MetaStrategySolver:
    """元策略求解器 — 根据交互历史推荐最优策略"""
    
    def recommend(self, current_state):
        """根据当前状态 + 交互历史推荐策略"""
        
        # 策略种群（对标 PSRO 的 Π）
        strategies = {
            "aggressive":   {"ΔG_priority": 0.8, "safety_relax": 0.3},  # 激进进化
            "conservative": {"ΔG_priority": 0.3, "safety_relax": 0.0},  # 保守进化
            "exploratory":  {"ΔG_priority": 0.5, "safety_relax": 0.2},  # 探索进化
            "repair":       {"ΔG_priority": 0.6, "safety_relax": 0.1},  # 修复进化
        }
        
        # 根据交互矩阵选择最佳策略
        history = self.interaction_matrix.get_recent_history(10)
        
        if self._is_stagnant(history):
            return strategies["exploratory"]     # 停滞 → 探索
        elif self._has_veto_streak(history):
            return strategies["conservative"]    # 连续否决 → 保守
        elif self._has_new_demand(history):
            return strategies["aggressive"]      # 新需求 → 激进
        elif self._has_bug(history):
            return strategies["repair"]          # 有 bug → 修复
        else:
            return strategies["conservative"]    # 默认保守
```

### 4.3 交互矩阵的用途

| 用途 | 说明 |
|------|------|
| **策略选择** | MSS 根据历史选择最优进化策略 |
| **瓶颈识别** | 统计哪种交互模式导致停滞 |
| **否决模式分析** | Core2 最常否决哪类方案，减少重复否决 |
| **进化路径优化** | 哪些 Proposer→Solver→Judge 链路成功率最高 |
| **非传递性检测** | 三核之间的"石头剪刀布"关系 |

---

## 五、进化法官（Judge 机制工程化）

### 5.1 法官评估流程

```
每次进化周期的评估：

1. Proposer 提出需求 → Judge 评估需求合理性
   - 是否真正解决问题？(necessity)
   - 与用户需求相关？(relevance)
   - 评分 < 0.6 → 否决需求，要求 Proposer 重新提出

2. Solver 生成方案 → Judge 评估方案质量
   - 方案是否正确？(correctness)
   - 是否安全？(safety) ← 一票否决
   - 是否有增量？(impact)
   - 评分 < 0.6 → 否决方案，要求 Solver 重新生成

3. 执行后 → Judge 评估实际效果
   - ΔG 是否提升？(actual_impact)
   - 是否引入新问题？(side_effects)
   - 与预期是否一致？(expectation_match)
```

### 5.2 法官自校准

```python
class EvolutionJudge:
    """进化法官 — 评估每次进化尝试的质量"""
    
    def __init__(self):
        self.evaluation_history = []  # 记录评估与实际结果
        self.weights = {
            "necessity": 0.25,
            "feasibility": 0.20,
            "safety": 0.25,    # 安全权重最高
            "originality": 0.15,
            "impact": 0.15
        }
    
    def evaluate(self, proposal):
        """评估进化提案"""
        scores = {}
        scores["necessity"] = self._score_necessity(proposal)
        scores["feasibility"] = self._score_feasibility(proposal)
        scores["safety"] = self._score_safety(proposal)  # 一票否决
        scores["originality"] = self._score_originality(proposal)
        scores["impact"] = self._score_impact(proposal)
        
        total = sum(scores[k] * self.weights[k] for k in scores)
        
        # 安全一票否决
        if scores["safety"] < 0.7:
            return {"passed": False, "reason": "safety_veto", "scores": scores}
        
        # 总分门控
        if total < 0.6:
            return {"passed": False, "reason": "quality_insufficient", "scores": scores}
        
        return {"passed": True, "scores": scores, "total": total}
    
    def calibrate(self):
        """自校准：根据评估历史调整权重"""
        if len(self.evaluation_history) < 10:
            return
        
        # 计算每个维度的预测准确度
        for dim in self.weights:
            accuracy = self._calc_prediction_accuracy(dim)
            # 准确度低的维度降低权重
            self.weights[dim] *= (0.8 + 0.4 * accuracy)
        
        # 归一化
        total_w = sum(self.weights.values())
        for k in self.weights:
            self.weights[k] /= total_w
```

---

## 六、同步状态交换（替代异步独立）

### 6.1 同步周期

```
旧模式：
  Core1 独立运行 → Core2 独立运行 → Core3 独立运行
  各自 heartbeat 周期不同，更新不同步

新模式（同步状态交换）：
  ┌──────────────────────────────────────────────────┐
  │ 同步周期 T (每 5 分钟)                              │
  │                                                    │
  │ Phase 1: 状态收集 (0-30s)                          │
  │   Core1 → {服务质量, 用户反馈, 能力缺口}              │
  │   Core2 → {安全评分, 漏洞发现, 约束更新}              │
  │   Core3 → {ΔG, 新能力, 进化方向}                     │
  │                                                    │
  │ Phase 2: 共振交互 (30s-4min)                       │
  │   基于状态 → 触发共振事件 → 收集响应 → 合成决策        │
  │                                                    │
  │ Phase 3: 同步执行 (4min-5min)                      │
  │   三核同时执行本周期决策                              │
  │   交互矩阵记录本次交互                               │
  └──────────────────────────────────────────────────┘
```

### 6.2 同步执行的含义

不是参数共享（三个独立实体无法共享参数），而是**状态同步**：
- 每个周期结束，三核交换状态摘要
- 形成全局进化决策
- 三核同时执行各自的决策部分
- 下一周期基于同步后的状态开始

---

## 七、E_co 维度新定义

### 7.1 旧公式 vs 新公式

```
旧 E_co = 三核共享次数 / 总操作次数  （太简单，无法反映真正的共进化质量）

新 E_co = α × 共振成功率 
        + β × 交互矩阵覆盖率 
        + γ × 法官通过率 
        + δ × 对抗进化强度

其中：
- 共振成功率 = 成功响应的事件数 / 总事件数（反映共振质量）
- 交互矩阵覆盖率 = 有记录的交互类型数 / 所有可能的交互类型数（反映记忆完整性）
- 法官通过率 = 通过质量门控的进化数 / 总进化尝试数（反映进化质量）
- 对抗进化强度 = Proposer-Solver 分数差的变化率（反映军备竞赛强度）
- α + β + γ + δ = 1
```

### 7.2 预期 E_co 提升

```
当前: E_co = 1.27
  ├── 共振引擎（双向+窗口）:     +0.08 → 1.35
  ├── 进化法官（质量门控）:       +0.06 → 1.41
  ├── 交互矩阵（博弈记忆）:       +0.05 → 1.46
  ├── 对抗机制（军备竞赛）:       +0.06 → 1.52
  └── 目标达成: E_co ≥ 1.5 ✅
```

---

## 八、实施路线图

### Phase 1: 共振引擎 v2.0（第 1-2 天）

| 步骤 | 任务 | 产出 |
|------|------|------|
| 1.1 | 创建共振总线 `resonance_bus.py` | 核心共振引擎 |
| 1.2 | 重写 `core-bridge.py` 为共振模式 | 替代广播机制 |
| 1.3 | 定义共振事件类型和路由规则 | 路由配置 |
| 1.4 | 添加响应窗口和超时处理 | 窗口机制 |
| 1.5 | 三核共振测试 | 验证报告 |

### Phase 2: 进化法官 v2.0（第 3-4 天）

| 步骤 | 任务 | 产出 |
|------|------|------|
| 2.1 | 创建进化法官 `evolution_judge.py` | 评估引擎 |
| 2.2 | 实现 5 维度评估 + 安全一票否决 | 评估逻辑 |
| 2.3 | 集成到进化流程（每次进化前必须评估） | 流程改造 |
| 2.4 | 实现法官自校准机制 | 自校准逻辑 |

### Phase 3: 交互矩阵 v2.0（第 5-6 天）

| 步骤 | 任务 | 产出 |
|------|------|------|
| 3.1 | 创建交互矩阵 `interaction_matrix.py` | 博弈记忆 |
| 3.2 | 实现元策略求解器 MSS | 策略推荐 |
| 3.3 | 实现策略种群管理（4 种策略） | 策略库 |
| 3.4 | 共振交互自动记录到矩阵 | 自动记录 |

### Phase 4: 对抗机制 v2.0（第 7-8 天）

| 步骤 | 任务 | 产出 |
|------|------|------|
| 4.1 | 重新定义三核角色（Proposer/Solver/Judge） | 角色定义 |
| 4.2 | 实现对抗奖励函数 | 奖励计算 |
| 4.3 | 实现否决-响应机制 | 对抗流程 |
| 4.4 | 全系统集成测试 | 验证报告 |

---

## 九、风险与应对

| 风险 | 概率 | 影响 | 应对 |
|------|------|------|------|
| 共振风暴（核间无限循环） | 中 | 高 | 窗口期 + 最大迭代次数(3) + 衰减因子 |
| 对抗过激（Proposer 生成无解需求） | 中 | 中 | Judge 否决 + 需求合理性检查 |
| 法官误判 | 中 | 中 | 自校准机制 + 人工审核阈值 |
| 交互矩阵膨胀 | 低 | 低 | 定期压缩（保留最近 100 轮） |
| 策略种群退化 | 低 | 中 | 遗憾匹配 + 定期重置 |

---

## 十、与现有系统的关系

### 与 APEX 公式
- E_co 是 APEX 7 维度之一
- 本方案通过对抗+共振提升 E_co 内涵
- 不修改 APEX 公式本身

### 与三核架构
- Core1（OpenClaw）：从"服务交付"升级为"需求 Proposer"
- Core2（Security）：从"安全审计"升级为"质量 Judge"（含安全否决权）
- Core3（APEX）：从"进化引擎"升级为"方案 Solver"

### 与基因库
- 基因库成为 Solver 的"弹药库"
- Proposer 从基因库中选择挑战方向
- Judge 评估基因融合的质量

### 与璇玑公式
- E_co 提升 → ΔG 提升
- 对抗进化强度纳入 E_co 计算
- 共振质量纳入 E_co 计算

---

*设计完成。基于 MAE + Self-Play 论文洞察重构。等待 CEO 审批后开始实施。*
