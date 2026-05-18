# 深度学习笔记：AI 自进化核心论文精读

## 一、Survey of Self-Evolving Agents (TMLR 2026, arXiv:2507.21046)

**地位**：77 页，17 所顶级高校联合撰写，**自进化领域最权威的分类学**

### 核心分类框架（必须掌握）

#### 1. What to Evolve（进化什么）
- **Models**：Policy（策略网络权重）+ Experience（经验回放）
- **Context**：Memory Evolution（记忆进化）+ Prompt Optimization（提示优化）
- **Tools**：Autonomous Discovery（自主发现工具）+ Iterative Refinement（迭代优化）+ Scalable Management（可扩展管理）
- **Architecture**：Single-Agent Optimization + Multi-Agent System Optimization

#### 2. When to Evolve（何时进化）
- **Intra-Test-Time**：单次任务内的适应（ICL / SFT / RL）
- **Inter-Test-Time**：跨任务的适应（ICL / SFT / RL）
- 关键区分：APEX 的 15min 周期属于 Inter-Test-Time

#### 3. How to Evolve（如何进化）
- **Reward-based**：Textual Feedback / Internal Rewards / External Rewards / Implicit Rewards
- **Imitation & Demonstration**：Self-Generated / Cross-Agent / Hybrid
- **Population-based & Evolutionary**：Single-Agent Evolution / Multi-Agent Evolution

#### 4. Where to Evolve（在哪进化）
- **General Domain**：Memory + Model-Agent Co-Evolution + Curriculum-Driven
- **Specialized Domain**：Coding / GUI / Financial / Medical / Education

### APEX 在这个分类学中的定位

| Survey 分类 | APEX 覆盖 | 缺失 |
|-------------|-----------|------|
| What: Models | ⚠️ 间接（调参） | ❌ 不修改模型权重 |
| What: Context | ✅ Prompt 优化 | ❌ 记忆进化 |
| What: Tools | ❌ | ❌ 工具发现/创建 |
| What: Architecture | ❌ | ❌ 架构自修改 |
| When: Inter-Test | ✅ 15min 周期 | ❌ Intra-Test 适应 |
| How: Reward-based | ✅ ΔG 作为奖励 | ❌ 文本反馈 |
| How: Imitation | ❌ | ❌ 没有示范学习 |
| How: Evolutionary | ❌ | ❌ 没有种群/进化方法 |
| Where: General | ⚠️ 部分 | ❌ 跨域泛化 |
| Where: Specialized | ⚠️ 编码 | ❌ 医疗/金融/教育 |

---

## 二、Darwin Gödel Machine (arXiv:2505.22954, 2025→2026)

**地位**：**第一个真正实现自修改代码的 AI 系统**，Jeff Clune 团队

### 核心设计

#### 1. 自引用（Self-Referential）
- Agent 修改的是自己的 Python 代码（图灵完备）
- 修改自身代码 = 改进自身能力 = 改进自身修改代码的能力
- 形成**递归自改进**闭环

#### 2. 开放式探索（Open-Endedness）
- 维护一个 **Archive**（所有生成过的 Agent）
- 从 Archive 中采样 → 自修改 → 评估 → 存入 Archive
- 不是只保留最优版本，而是保留所有"有趣"的变体
- 关键洞见：**踏脚石（Stepping Stones）** 比直接优化更重要

#### 3. 经验验证（Empirical Validation）
- 原版 Gödel Machine 需要形式化证明修改有益 → 实践中不可能
- DGM 用基准测试代替证明：改完跑 SWE-bench，分数提高就接受
- 类似生物进化：不预验证，突变→测试→选择

#### 4. 核心算法
```
Archive = [初始Agent]
loop:
    parent = Sample(Archive)  // 从档案中采样
    child = SelfModify(parent)  // 自修改
    score = Evaluate(child, benchmark)  // 经验验证
    Archive.append(child)  // 存入档案
    // 开放式：即使 score < parent，只要"有趣"就保留
```

### 实验结果
- SWE-bench：20.0% → 50.0%（2.5x 提升）
- Polyglot：14.2% → 30.7%（2.2x 提升）
- **自改进 > 不自改进**：递归改进确实有效
- **开放式 > 非开放式**：有 Archive 比只保留最新版好

### APEX 与 DGM 的差距

| 维度 | DGM | APEX |
|------|-----|------|
| 自修改代码 | ✅ Python 代码级修改 | ❌ 只调数值参数 |
| 开放式 Archive | ✅ 保留所有变体 | ❌ 只保留最新状态 |
| 经验验证 | ✅ SWE-bench / Polyglot | ⚠️ 内部评分（非外部基准） |
| 递归自改进 | ✅ 改进→更好改进 | ❌ 改进不提升改进能力 |
| 踏脚石机制 | ✅ 有趣但非最优的也保留 | ❌ 没有 |
| 并行探索 | ✅ 树状并行 | ❌ 单线程 |

---

## 三、对 APEX v2 的深度启示

### 启示 1：必须打破"调参"天花板
APEX v1 的所有进化都发生在参数空间（Θ, K, Φ, Ψ）。但 Survey 论文明确指出，进化的四个维度是 **Models, Context, Tools, Architecture**。APEX 只触及了 Context（Prompt），连 Models 都没碰，更别说 Tools 和 Architecture。

**行动**：v2 必须实现代码级自修改（D_s 维度）

### 启示 2：开放式 Archive 是关键
DGM 证明了：**保留所有变体 > 只保留最优版本**。因为"看似无用"的变体可能是未来的踏脚石。

**行动**：v2 必须维护 Agent Archive，实现 O_e 维度

### 启示 3：进化方法不止一种
Survey 列出了三大类 How-to-Evolve 方法：
1. Reward-based（APEX 已有）
2. Imitation & Demonstration（APEX 完全没有）
3. Population-based & Evolutionary（APEX 完全没有）

**行动**：v2 需要加入 Cross-Agent Demonstration Learning 和 Population-based Evolution

### 启示 4：Intra-Test-Time 进化缺失
APEX 只做 Inter-Test-Time（任务间），不做 Intra-Test-Time（任务内）。但 Survey 指出两种都很重要。

**行动**：v2 需要在单次任务执行中也能适应

### 启示 5：安全不只是 PID 满分
Survey 专门有一章讨论 Self-Evolving Agent 的安全风险：
- Emergent Risks（涌现风险）
- Prescriptive Guardrails（处方性护栏）
- Mitigation Strategies（缓解策略）

APEX 的 PID=1.000 太粗糙了。

**行动**：v2 的 S_v 维度需要更精细的安全框架
