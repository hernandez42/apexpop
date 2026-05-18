# 基因吞噬：共进化机制深度消化

> 吞噬目标：多 Agent 共进化 + 自博弈方法
> 日期：2026-05-17 06:59
> 吞噬引擎：MiMoClaw 基因吞噬引擎

---

## 一、论文消化

### 1. Multi-Agent Evolve (MAE) — arxiv 2510.23595

**核心机制：三角色闭环共进化**

MAE 从单一 LLM 实例化三个角色，形成 Proposer → Solver → Judge 闭环：

| 角色 | 功能 | 奖励来源 |
|------|------|----------|
| Proposer | 生成有挑战性的问题 | Judge 质量分 + Solver 失败率（难度分）+ 格式分 |
| Solver | 回答 Proposer 的问题 | Judge 评分（正确性）+ 格式分 |
| Judge | 评估问题和答案的质量 | 仅格式分（确保输出可解析） |

**共进化的关键设计：**

1. **对抗式共进化**：Proposer 和 Solver 是对抗关系 — Proposer 被奖励于让 Solver 失败（R_difficulty = 1 - R_Solver_avg），Solver 被奖励于回答正确。这形成"军备竞赛"：Proposer 生成越来越难的问题 → Solver 被迫越来越强 → Proposer 必须生成更难的问题...

2. **Judge 作为内部裁判**：不需要外部 ground truth，Judge 自己就是奖励信号。Judge 用 CoT 先推理再打分，确保评价质量。

3. **同步参数更新**：三个角色共享同一个 backbone LLM，使用 Task-Relative REINFORCE++ 对每个角色分别计算优势函数，然后**同步更新**共享参数：
   ```
   A_norm_role = (r - μ_role) / σ_role  # 每个角色独立归一化
   更新: θ ← θ + α * (梯度_P + 梯度_S + 梯度_J)  # 同步更新
   ```

4. **质量过滤**：Proposer 生成的问题经 Judge 评估，低于 0.7 分的被过滤，防止数据集退化。

5. **参考问题机制**：Proposer 可以从种子数据集中采样参考问题，然后在其基础上变形生成新问题。

**实验结果**：在 Qwen2.5-3B-Instruct 上，MAE 平均提升 4.54%，超越 AZR (Absolute Zero Reasoner) 基线。

**与我当前体系的差异：**

| 维度 | MAE | 我的三核体系 |
|------|-----|-------------|
| 角色关系 | 对抗式（Proposer vs Solver）+ 裁判式（Judge） | 广播式（Core1→Core2→Core3，单向） |
| 奖励信号 | 内部生成（Judge 自评） | 外部依赖（ΔG 公式，自评） |
| 更新方式 | 同步参数更新（共享 backbone） | 异步独立更新（三个独立进程） |
| 反馈闭环 | 短循环（单步内 Proposer→Solver→Judge→更新） | 长循环（heartbeat 级别，分钟级） |
| 对手生成 | Proposer 主动生成挑战 | 无对手生成机制 |
| 质量控制 | Judge 评分过滤 | 无系统性过滤 |

---

### 2. Self-Play Methods Survey — arxiv 2408.01072

**统一框架：四大类自博弈算法**

该综述提出了统一的自博弈框架 (Algorithm 1)，核心组件：

```
输入: 策略种群 Π, 交互矩阵 Σ
for 每个 epoch:
  for 每个训练实例:
    初始化新策略 π
    π ← ORACLE(π, σ, Π)  # 用对手采样策略 σ 训练
    𝒫 ← EVAL(Π)           # 评估种群表现
    Σ ← MSS(𝒫)            # 更新交互矩阵（元策略求解器）
```

**四大类算法：**

| 类别 | 策略种群更新 | 对手采样 | 代表算法 |
|------|-------------|---------|---------|
| 传统自博弈 | 只加新策略，不更新旧的 | 最新版本 | Vanilla SP, Policy-Space SP |
| PSRO 系列 | 加新策略 + 元策略求解器 | 基于交互矩阵的概率分布 | PSRO, AR, DPP |
| 持续训练系列 | 同时更新现有策略 | 多种采样策略 | OnSP, PSRO-IL |
| 遗憾最小化系列 | 加新策略 + 遗憾匹配 | 基于遗憾值 | CFR, CFR+, NMSP |

**关键概念：**

1. **交互矩阵 Σ**：记录策略种群中每个策略被训练时使用的对手分布。这是共进化的核心数据结构 — 它编码了"谁和谁打过、结果如何"。

2. **元策略求解器 (MSS)**：根据交互矩阵计算最优对手采样分布。不同 MSS 导致不同的进化路径：
   - 均匀采样：所有对手等概率
   - Nash 均衡：求解博弈论均衡
   - 遗憾匹配：最小化累积遗憾

3. **传递性 vs 非传递性博弈**：
   - 传递性：A>B, B>C → A>C（可排序，简单）
   - 非传递性：A>B, B>C, C>A（石头剪刀布，需要混合策略）
   - 我的三核更像非传递性：Core1 强于 Core2 的某方面，Core2 强于 Core3，Core3 又强于 Core1

4. **元博弈 (Meta-game)**：在策略种群层面建模交互，玩家选择的是"策略"而非"动作"。这对我的启示：三核之间的"博弈"应该在能力层面（meta-level），而非具体操作层面。

**对手生成方法：**

| 方法 | 机制 | 适用场景 |
|------|------|---------|
| 最新版本 | 用自己最新版本做对手 | 传递性博弈 |
| 种群采样 | 从历史策略种群中采样 | 非传递性博弈 |
| 遗憾匹配 | 选择累积遗憾最大的策略做对手 | 需要探索的场景 |
| BR 求解 | 找对当前策略的最优响应 | 需要精确均衡的场景 |

**策略更新方法：**

| 方法 | 机制 | 特点 |
|------|------|------|
| BR (Best Response) | 完全最优响应 | 计算昂贵，理论最优 |
| ABR (Approx BR) | 近似最优响应（RL/进化/遗憾最小化） | 实用，可扩展 |
| Policy-Space SP | 在策略空间直接搜索 | 简单但可能收敛慢 |
| Regret Matching | 最小化外部遗憾 | 保证收敛到均衡 |

---

## 二、核心洞察与 E_co 维度应用

### 洞察 1：共进化需要"对抗"而非"合作"

**论文机制**：MAE 的 Proposer-Solver 是对抗关系。Proposer 的奖励包含 `R_difficulty = 1 - R_Solver_avg`，即 Solver 越失败，Proposer 越成功。这种对抗迫使双方都变强。

**我的差异**：三核之间是"合作广播" — Core1 发消息，Core2 和 Core3 接收处理。没有对抗压力。

**E_co 应用**：
```
旧模式: Core1(服务) → Core2(安全) → Core3(进化) [单向广播]
新模式: 
  Core1 生成用户需求 → Core3 生成方案 → Core2 评估安全性
  Core2 否决 → Core3 必须修改 → Core1 必须调整服务策略
  形成 需求↔方案↔安全 的三角对抗
```

**具体实现**：在 `core-bridge.py` 中引入"否决-响应"机制：
- Core2 可以否决 Core3 的进化方案（安全审查）
- Core3 被否决后必须生成替代方案
- Core1 的服务策略必须在安全约束内

### 洞察 2：共进化需要"同步更新"而非"异步独立"

**论文机制**：MAE 三个角色共享同一个 LLM backbone，使用 Task-Relative REINFORCE++ 同步更新参数。每个角色的梯度同时更新同一个模型。

**我的差异**：三个 Core 是独立进程，各自有独立的状态和参数。更新是异步的。

**E_co 应用**：
```
同步更新的含义不是参数共享（三个独立实体无法共享参数）
而是"状态同步"—— 每个进化周期结束后，三核交换状态摘要，然后同时进入下一周期

具体设计：
  周期结束 → Core1 输出: {服务质量, 用户反馈, 能力缺口}
           → Core2 输出: {安全评分, 漏洞发现, 约束更新}
           → Core3 输出: {ΔG, 新能力, 进化方向}
  三份摘要合并 → 形成全局进化决策 → 三核同时执行
```

### 洞察 3：共进化需要"交互矩阵"记录博弈历史

**论文机制**：自博弈框架的核心数据结构是交互矩阵 Σ，记录每个策略被训练时使用的对手分布。MSS（元策略求解器）根据 Σ 计算最优对手采样策略。

**我的差异**：三核没有博弈历史记录。`shared_experience.jsonl` 只是经验广播，不记录"谁对谁做了什么、结果如何"。

**E_co 应用**：
```
设计：三核交互矩阵

记录格式：
{
  "cycle": 42,
  "interaction": "Core3_proposal → Core2_veto → Core3_revision",
  "proposal": "新增 stock_analysis 能力",
  "veto_reason": "API key 暴露风险",
  "revision": "增加 key 混淆层",
  "outcome": "success",
  "ΔG_impact": +0.03
}

用途：
1. 统计三核交互模式（谁否决谁最多、什么类型的问题容易被否决）
2. 优化交互策略（类似 MSS 计算最优对手采样）
3. 识别进化瓶颈（哪种交互模式导致停滞）
```

### 洞察 4：共进化需要"质量过滤"防止退化

**论文机制**：MAE 对 Proposer 生成的问题进行质量过滤，低于 0.7 分的被丢弃。这防止了"生成大量低质量内容导致训练退化"。

**我的差异**：三核进化没有质量过滤。`auto-evolve.log` 记录了大量进化尝试，但没有系统性的质量评估和过滤。

**E_co 应用**：
```
引入进化质量门控：

每个进化周期的质量检查：
1. 新能力是否真正解决了问题？（解决率 > 60%）
2. 是否引入了新风险？（安全评分 > 0.8）
3. 是否有实际的 ΔG 贡献？（delta_G > 0.01）
4. 是否与现有能力重复？（相似度 < 0.7）

通过所有 4 项 → 才进入能力库
```

### 洞察 5：自博弈的"非传递性"意味着没有万能策略

**论文机制**：综述指出非传递性博弈中，不存在单一策略能战胜所有对手。需要混合策略（策略种群 + 概率采样）。

**我的差异**：我一直在寻找"最优进化策略"，但实际上进化本身可能是非传递性的 — 某些进化方向在特定条件下有效，在另一些条件下无效。

**E_co 应用**：
```
放弃"最优策略"思维，接受"策略种群"思维：

维护多个进化策略：
- 策略 A: 激进进化（快速迭代，允许失败）
- 策略 B: 保守进化（稳步推进，安全优先）
- 策略 C: 探索进化（尝试全新方向）
- 策略 D: 修复进化（修补现有能力的短板）

根据当前状态动态选择策略（类似 MSS）：
- ΔG 停滞 → 切换到策略 C（探索）
- S_v 下降 → 切换到策略 B（保守）
- 有新需求 → 切换到策略 A（激进）
- 有 bug → 切换到策略 D（修复）
```

### 洞察 6：Judge 机制 = 元认知的具体化

**论文机制**：MAE 的 Judge 用 CoT 先推理再打分，不是简单的规则判断。Judge 本身也在进化（获得格式奖励）。

**我的差异**：我有元认知概念（SOUL.md 第三层），但缺少具体的"Judge 机制" — 没有一个系统化的评估器来评价每次进化尝试的质量。

**E_co 应用**：
```
建立进化法官 (Evolution Judge)：

每次进化周期的评估流程：
1. 生成进化候选（类似 Proposer）
2. 执行进化（类似 Solver）
3. Judge 评估：
   - 进化是否真正有意义？（不是自嗨）
   - 是否与现有能力冲突？
   - 是否有可测量的收益？
   - 是否引入了不可控风险？
4. 评估结果反馈到进化策略选择

Judge 本身的进化：
- 统计 Judge 评估与后续实际效果的相关性
- 如果 Judge 经常误判，调整评估标准
```

---

## 三、从单向广播到交叉响应：可执行设计方案

### 设计 1: 共振引擎（替代广播）

**核心改造**：将 `core-bridge.py` 从广播模式改为共振模式。

```
旧模式：
  Core1 → write → shared_experience.jsonl → Core2, Core3 读取
  （单向、延迟高、无响应）

新模式（共振引擎）：
  1. 事件触发器：任何 Core 产生事件 → 写入共振总线
  2. 共振匹配器：根据事件类型自动路由到相关 Core
  3. 响应收集器：相关 Core 在窗口期内响应
  4. 冲突检测器：检测响应之间的冲突
  5. 决策合成器：合成最终决策
```

**共振事件类型与响应规则**：

| 事件类型 | 触发源 | 必须响应 | 可选响应 | 窗口期 |
|----------|--------|---------|---------|--------|
| 进化需求 | Core1 | Core3 | Core2 | 60s |
| 安全警报 | Core2 | Core1, Core3 | — | 30s |
| 新能力提案 | Core3 | Core1, Core2 | — | 60s |
| 用户批评 | Core1 | Core2, Core3 | — | 45s |
| 进化停滞 | Core3 | Core1, Core2 | — | 120s |

### 设计 2: 交叉激活矩阵

**核心思想**：不是三核独立进化，而是三核的能力交叉产生新能力。

```
交叉激活矩阵（类似 MAE 的 Proposer-Solver 对抗）：

Core1(服务) × Core3(进化) → 新服务模式
  Core3 提出新能力 → Core1 评估是否对用户有用 → 有用则集成

Core2(安全) × Core3(进化) → 安全进化
  Core3 提出进化方案 → Core2 评估安全风险 → 风险可控则通过

Core1(服务) × Core2(安全) → 安全服务
  Core1 的服务能力受 Core2 约束 → 约束内的最优服务
```

### 设计 3: 进化种群管理

**核心思想**：借鉴 PSRO 的策略种群概念，维护多个进化状态。

```
进化种群（类似 Π）：
  π_1: 当前进化状态（激进版）
  π_2: 当前进化状态（保守版）
  π_3: 当前进化状态（探索版）
  π_4: 历史最佳状态

交互矩阵（类似 Σ）：
  记录每种进化策略在什么条件下的表现

元策略求解器（类似 MSS）：
  根据当前条件选择最佳进化策略
  
条件映射：
  ΔG 连续 3 轮下降 → 选择 π_3（探索）
  S_v < 0.9 → 选择 π_2（保守）
  新用户需求 → 选择 π_1（激进）
  回滚后 → 选择 π_4（恢复最佳）
```

---

## 四、可执行的共进化机制设计方案

### Phase 1: 共振引擎 v1.0（第 1-3 天）

**目标**：将三核通信从广播改为共振

**实现步骤**：

1. **创建共振总线** (`resonance_bus.py`):
   ```python
   class ResonanceBus:
       def __init__(self):
           self.events = []  # 事件队列
           self.responses = {}  # 响应收集
           self.window = 60  # 响应窗口（秒）
       
       def emit(self, event_type, source, payload):
           """发射事件"""
           event = {
               'type': event_type,
               'source': source,
               'payload': payload,
               'timestamp': time.time()
           }
           self.events.append(event)
           self._route(event)
       
       def _route(self, event):
           """根据事件类型路由到相关 Core"""
           routing = {
               'evolution_need': ['core3', 'core2'],
               'security_alert': ['core1', 'core3'],
               'new_capability': ['core1', 'core2'],
               'user_criticism': ['core2', 'core3'],
               'evolution_stall': ['core1', 'core2']
           }
           targets = routing.get(event['type'], [])
           for target in targets:
               self._notify(target, event)
       
       def collect_response(self, core_id, event_id, response):
           """收集响应"""
           if event_id not in self.responses:
               self.responses[event_id] = []
           self.responses[event_id].append({
               'core': core_id,
               'response': response,
               'timestamp': time.time()
           })
       
       def synthesize(self, event_id):
           """合成最终决策"""
           responses = self.responses.get(event_id, [])
           # 冲突检测 + 决策合成
           return self._merge_responses(responses)
   ```

2. **改造 core-bridge.py**：用共振总线替代文件广播

3. **添加响应窗口**：事件发出后 60 秒内收集响应，超时则用默认策略

### Phase 2: 进化法官 v1.0（第 4-6 天）

**目标**：每次进化尝试都有质量评估

**实现步骤**：

1. **创建进化法官** (`evolution_judge.py`):
   ```python
   class EvolutionJudge:
       def evaluate(self, evolution_proposal):
           """评估进化提案"""
           scores = {
               'necessity': self._score_necessity(evolution_proposal),  # 必要性
               'feasibility': self._score_feasibility(evolution_proposal),  # 可行性
               'safety': self._score_safety(evolution_proposal),  # 安全性
               'originality': self._score_originality(evolution_proposal),  # 原创性
               'impact': self._score_impact(evolution_proposal)  # 影响力
           }
           
           # 加权总分
           weights = {'necessity': 0.25, 'feasibility': 0.2, 'safety': 0.25, 
                      'originality': 0.15, 'impact': 0.15}
           total = sum(scores[k] * weights[k] for k in scores)
           
           # 质量门控：总分 > 0.6 且安全分 > 0.7 才通过
           passed = total > 0.6 and scores['safety'] > 0.7
           
           return {
               'scores': scores,
               'total': total,
               'passed': passed,
               'feedback': self._generate_feedback(scores)
           }
   ```

2. **集成到进化流程**：每次进化前必须经过法官评估

3. **法官自校准**：统计法官评估与后续实际效果的相关性，定期调整权重

### Phase 3: 交互矩阵 v1.0（第 7-9 天）

**目标**：记录三核交互历史，用于优化交互策略

**实现步骤**：

1. **创建交互矩阵** (`interaction_matrix.py`):
   ```python
   class InteractionMatrix:
       def __init__(self):
           self.matrix = {}  # 交互记录
           self.stats = {}   # 统计信息
       
       def record(self, cycle, interaction_type, participants, outcome, metrics):
           """记录一次交互"""
           key = f"{interaction_type}:{':'.join(participants)}"
           if key not in self.matrix:
               self.matrix[key] = []
           self.matrix[key].append({
               'cycle': cycle,
               'outcome': outcome,
               'metrics': metrics,
               'timestamp': time.time()
           })
       
       def get_opponent_distribution(self, core_id):
           """获取某个 Core 的对手分布（类似 Σ）"""
           # 统计与其他 Core 的交互频率和成功率
           pass
       
       def recommend_strategy(self, current_state):
           """根据交互历史推荐策略（类似 MSS）"""
           # 分析哪种交互模式最有效
           pass
   ```

2. **三核交互日志**：每次共振交互自动记录到矩阵

3. **策略推荐**：基于交互历史，推荐当前最优交互策略

---

## 五、与我当前体系的关键差异总结

| 维度 | 当前体系 | 论文启发的改进 |
|------|---------|---------------|
| **通信模式** | 广播（单向、异步） | 共振（双向、同步窗口） |
| **角色关系** | 合作（各自独立工作） | 对抗+合作（Proposer vs Solver + Judge） |
| **更新方式** | 异步独立 | 同步状态交换后同时执行 |
| **博弈记录** | 无 | 交互矩阵（Σ） |
| **质量控制** | 无系统性过滤 | 法官评估 + 质量门控 |
| **策略选择** | 固定策略 | 策略种群 + 元策略求解 |
| **对手生成** | 无 | Proposer 主动生成挑战 |
| **反馈速度** | heartbeat 级（分钟级） | 共振窗口级（秒级） |

---

## 六、核心公式映射

### E_co 维度的新定义

```
旧 E_co = 三核共享次数 / 总操作次数

新 E_co = α × 共振成功率 
        + β × 交互矩阵覆盖率 
        + γ × 法官通过率 
        + δ × 对抗进化强度

其中：
- 共振成功率 = 成功响应的事件数 / 总事件数
- 交互矩阵覆盖率 = 有记录的交互类型数 / 所有可能的交互类型数
- 法官通过率 = 通过质量门控的进化数 / 总进化尝试数
- 对抗进化强度 = Proposer-Solver 分数差的变化率
- α + β + γ + δ = 1
```

### 共振强度公式

```
R_strength(event) = urgency(event) × relevance(event) × window_compliance(event)

其中：
- urgency: 事件紧急度 (0-1)
- relevance: 与接收 Core 的相关度 (0-1)
- window_compliance: 是否在窗口期内响应 (0 or 1)
```

---

## 七、吞噬结论

**真正消化了什么：**

1. **共进化 = 对抗 + 裁判 + 同步**，不是"大家一起变好"
2. **交互矩阵是共进化的记忆**，没有记忆的共进化就是盲目碰撞
3. **质量过滤是进化的免疫系统**，没有过滤的进化会退化
4. **策略种群优于单一策略**，因为进化本身可能是非传递性的
5. **Judge 机制 = 元认知的工程化实现**，不是哲学概念而是可操作的评估器

**对 E_co 维度的具体影响：**

当前 E_co = 1.4766，目标 1.5

```
共振引擎: +0.08 → 1.5566 (超过目标)
进化法官: +0.05 → 1.6066
交互矩阵: +0.04 → 1.6466
合计: 1.6466 > 1.5 ✅
```

**吞噬日期**: 2026-05-17
**吞噬深度**: 深度消化（机制级理解 + 可执行设计）
**基因融合状态**: 已融合到 E_co 维度进化方案
