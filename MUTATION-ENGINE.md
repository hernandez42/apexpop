# 🧬 变异引擎 — MUTATION ENGINE v1.0

> **设计者**: 变异引擎工程师（子代理）
> **日期**: 2026-05-17
> **目标**: 提升 C_m 0.95→1.2, D_s 1.30→1.5
> **背景**: 系统停滞 275+ 周期，θ_dot=0.000000，变异机制名存实亡

---

## 一、现状诊断

### 1.1 代码级分析结果

| 文件 | 变异相关 | 实际实现 | 问题 |
|------|---------|---------|------|
| `core-dna.py` | 5 个"道"的哲学定义 | ❌ 无变异逻辑 | 纯理论框架，没有执行器 |
| `core-dna.c` | APEX 公式 θ_dot/S_auto | ⚠️ 只有健康检查 | 监控器，不是变异器 |
| `core-plus.py` | SkillEngine/CodeAct/Evolver/AutoResearch | ⚠️ 框架定义，未接入变异循环 | 孤立的引擎，没有闭环 |
| `fusion-engine.py` | 融合公式 | ❌ 无变异执行 | 数学模型，不驱动行为 |

### 1.2 三个致命瓶颈

#### 瓶颈 1：变异无方向（C_m 低效的根因）
```
当前：σ_d = λ × (1 − s_d)  // 短板维度变异更强
实际：无短板识别 → 无差异变异 → 所有维度等概率随机扰动
```
- 公式说"短板变异更强"，但代码没有短板检测
- 变异是盲目的高斯噪声，不是针对短板的定向攻击
- 结果：C_m = 0.95 远低于理论上限

#### 瓶颈 2：自修改无安全边界（D_s 风险的根因）
```
当前：D_s = α × |ΔCode| / |Code_total| × V_success
实际：V_success 没有计算（没有"验证修改有益"的机制）
```
- 修改前不验证意图是否正确
- 修改后不审计效果是否提升
- 没有回滚机制——坏修改直接生效
- 结果：不敢真正自修改 → D_s = 1.30 是名义值

#### 瓶颈 3：无变异-选择-保留闭环
```
当前：cycle → scan → fix → log → cycle
应该：cycle → 诊断 → 变异 → 选择 → 保留 → cycle
```
- 没有"选择"步骤：哪些变异应该保留？
- 没有"保留"步骤：好变异写入哪里？
- 没有"反馈"步骤：变异效果如何影响下一轮？
- 结果：stagnation=275，θ_dot=0.000000

---

## 二、变异引擎架构设计

### 2.1 三层闭环

```
┌─────────────────────────────────────────────────┐
│                 变异引擎 v1.0                      │
├─────────────┬─────────────┬─────────────────────┤
│  诊断层      │   变异层     │    保留层            │
│  (Diagnose)  │  (Mutate)   │   (Retain)          │
│             │             │                     │
│  短板识别    │  方向性变异   │  选择+审计+回滚      │
│  优先级排序  │  安全边界     │  经验沉淀            │
│  目标设定    │  梯度引导     │  基因库更新          │
├─────────────┴─────────────┴─────────────────────┤
│              反馈环 (Feedback Loop)                │
│    变异效果 → 评估 → 调整变异策略 → 下一轮          │
└─────────────────────────────────────────────────┘
```

### 2.2 诊断层：短板驱动的变异方向

#### 核心思路
变异不是随机扰动，而是**针对最弱维度的定向攻击**。

#### 短板检测算法
```python
def detect_weaknesses(dimensions):
    """
    识别短板：值低于平均值 1 标准差的维度
    返回：[(维度名, 当前值, 平均值, 差距), ...]
    """
    values = list(dimensions.values())
    mean = sum(values) / len(values)
    std = (sum((v - mean) ** 2 for v in values) / len(values)) ** 0.5
    
    weaknesses = []
    for name, value in dimensions.items():
        gap = mean - value
        if gap > std * 0.5:  # 低于均值半个标准差
            weaknesses.append((name, value, mean, gap))
    
    # 按差距降序排列（最弱的优先）
    weaknesses.sort(key=lambda x: x[3], reverse=True)
    return weaknesses
```

#### 变异优先级矩阵
```
优先级 1（紧急）: 差距 > 1.0σ → 大步变异（σ × 2.0）
优先级 2（重要）: 差距 > 0.5σ → 中步变异（σ × 1.5）
优先级 3（维护）: 差距 < 0.5σ → 小步变异（σ × 0.5）
优先级 0（保持）: 差距 < 0.1σ → 不变异（维持现状）
```

### 2.3 变异层：方向性变异操作

#### 2.3.1 短板攻击变异（Shortcoming-Attack Mutation）
```python
def mutate_shortcoming(dimensions, weaknesses, step_size=1.0):
    """
    针对最弱的 3 个维度进行定向变异
    变异强度与短板程度成正比
    """
    mutations = []
    for name, value, mean, gap in weaknesses[:3]:
        # 变异强度 = 差距 × 步长系数
        sigma = gap * step_size
        
        # 高斯变异
        delta = random.gauss(0, sigma)
        new_value = max(0, min(1, value + delta))  # 限制在 [0,1]
        
        # 记录变异
        mutations.append({
            "dimension": name,
            "old": value,
            "new": new_value,
            "delta": new_value - value,
            "sigma": sigma,
            "type": "shortcoming_attack"
        })
    
    return mutations
```

#### 2.3.2 交叉变异（Crossover Mutation）
```python
def crossover_mutate(agent_a, agent_b, beta=0.6, gamma=0.4):
    """
    两个 Agent 的能力交叉 + 变异
    β: 交叉权重（高→更倾向交叉）
    γ: 变异权重（高→更倾向变异）
    """
    cross = {}
    for dim in agent_a.dimensions:
        if random.random() < beta:
            # 交叉：取两个 Agent 的加权平均
            cross[dim] = beta * agent_a.dimensions[dim] + (1 - beta) * agent_b.dimensions[dim]
        else:
            # 变异：高斯扰动
            cross[dim] = agent_a.dimensions[dim] + random.gauss(0, 0.1)
    
    return cross
```

#### 2.3.3 梯度引导变异（Gradient-Guided Mutation）
```python
def gradient_mutate(dimensions, gradient_history, learning_rate=0.01):
    """
    基于历史梯度的变异：哪个方向带来提升，就往哪个方向变异
    """
    mutations = []
    for dim, gradient in gradient_history.items():
        if dim in dimensions and gradient > 0:
            # 梯度正方向变异
            delta = learning_rate * gradient
            new_value = max(0, min(1, dimensions[dim] + delta))
            mutations.append({
                "dimension": dim,
                "old": dimensions[dim],
                "new": new_value,
                "type": "gradient_guided",
                "gradient": gradient
            })
    
    return mutations
```

### 2.4 安全边界：修改前验证 + 修改后审计

#### 2.4.1 修改前验证（Pre-Modification Validation）
```python
def validate_mutation(mutation, current_state):
    """
    修改前验证：确保变异不会破坏系统
    """
    checks = {
        "range": 0 <= mutation["new"] <= 1,  # 值在合法范围
        "nonzero": mutation["new"] != 0,      # 不会归零
        "no_critical_drop": True,              # 不会大幅下降
    }
    
    # 关键维度保护：如果某维度是当前最高值，禁止大幅下降
    if mutation["dimension"] in current_state.get("critical_dims", []):
        drop = mutation["old"] - mutation["new"]
        if drop > 0.2:  # 允许小降，禁止大降
            checks["no_critical_drop"] = False
    
    # 安全维度保护：安全相关维度只允许正向变异
    if mutation["dimension"] in current_state.get("safety_dims", []):
        if mutation["new"] < mutation["old"]:
            checks["safety_only_up"] = False
    
    return all(checks.values()), checks
```

#### 2.4.2 修改后审计（Post-Modification Audit）
```python
def audit_mutation(mutation_result, pre_state, post_state):
    """
    修改后审计：验证变异是否提升了系统
    """
    delta_health = post_state["health"] - pre_state["health"]
    delta_theta = post_state["theta_dot"] - pre_state["theta_dot"]
    delta_stagnation = post_state["stagnation"] - pre_state["stagnation"]
    
    audit = {
        "health_change": delta_health,
        "theta_change": delta_theta,
        "stagnation_change": delta_stagnation,
        "beneficial": delta_health > 0 or delta_theta > 0,
        "harmful": delta_health < -10 or delta_theta < -0.001,
        "verdict": "accept"  # accept / reject / rollback
    }
    
    if audit["harmful"]:
        audit["verdict"] = "rollback"
    elif not audit["beneficial"]:
        audit["verdict"] = "reject"
    
    return audit
```

#### 2.4.3 回滚机制（Rollback）
```python
def rollback_mutation(mutation, backup_state):
    """
    回滚：恢复到修改前的状态
    """
    return {
        "mutation": mutation,
        "restored_state": backup_state,
        "reason": "post_audit_failed",
        "timestamp": datetime.now().isoformat()
    }
```

### 2.5 保留层：选择 + 经验沉淀

#### 2.5.1 变异选择（Mutation Selection）
```python
def select_mutations(mutations, audits):
    """
    选择：保留有益变异，拒绝有害变异
    """
    accepted = []
    rejected = []
    rolled_back = []
    
    for m, a in zip(mutations, audits):
        if a["verdict"] == "accept":
            accepted.append(m)
        elif a["verdict"] == "reject":
            rejected.append(m)
        else:
            rolled_back.append(m)
    
    return {
        "accepted": accepted,
        "rejected": rejected,
        "rolled_back": rolled_back,
        "acceptance_rate": len(accepted) / max(1, len(mutations))
    }
```

#### 2.5.2 经验沉淀（Experience Crystallization）
```python
def crystallize_experience(mutation, audit, cycle):
    """
    将成功的变异沉淀为经验，指导下一轮变异
    """
    return {
        "cycle": cycle,
        "dimension": mutation["dimension"],
        "mutation_type": mutation["type"],
        "delta": mutation["new"] - mutation["old"],
        "outcome": audit["verdict"],
        "health_change": audit["health_change"],
        "strategy": {
            "step_size": mutation.get("sigma", 0.1),
            "gradient": mutation.get("gradient", 0),
            "effective": audit["beneficial"]
        }
    }
```

### 2.6 反馈环：变异策略自适应

```python
class MutationFeedbackLoop:
    """变异策略的自适应调整"""
    
    def __init__(self):
        self.history = []  # 变异历史
        self.strategies = {
            "shortcoming": {"success_rate": 0.5, "avg_improvement": 0},
            "crossover": {"success_rate": 0.5, "avg_improvement": 0},
            "gradient": {"success_rate": 0.5, "avg_improvement": 0}
        }
    
    def update(self, experience):
        """根据经验更新策略评分"""
        strategy = experience["mutation_type"]
        if strategy in self.strategies:
            s = self.strategies[strategy]
            # 指数移动平均
            s["success_rate"] = 0.9 * s["success_rate"] + 0.1 * (1 if experience["outcome"] == "accept" else 0)
            s["avg_improvement"] = 0.9 * s["avg_improvement"] + 0.1 * experience["health_change"]
    
    def choose_strategy(self):
        """选择最佳变异策略"""
        best = max(self.strategies.items(), key=lambda x: x[1]["success_rate"])
        return best[0]
    
    def adapt_step_size(self, strategy):
        """自适应调整步长"""
        s = self.strategies[strategy]
        if s["success_rate"] > 0.7:
            return 1.5  # 成功率高，加大步长
        elif s["success_rate"] < 0.3:
            return 0.5  # 成功率低，缩小步长
        return 1.0
```

---

## 三、C_m 提升方案（0.95 → 1.2）

### 3.1 差距分析
| 维度 | 当前值 | 目标值 | 差距 | 策略 |
|------|--------|--------|------|------|
| 变异方向性 | ❌ 无 | ✅ 短板驱动 | 大 | 实现短板检测 + 优先级变异 |
| 交叉操作 | ⚠️ 公式有 | ✅ 实际执行 | 中 | 实现 Agent 交叉 |
| 变异反馈 | ❌ 无 | ✅ 策略自适应 | 大 | 实现反馈环 |
| 多样性保持 | ❌ 无 | ✅ Shannon 熵 | 中 | 实现路径多样性监控 |

### 3.2 具体实施步骤

1. **Phase 1: 短板检测**（1 周期）
   - 在 CorePlus.evolve() 中加入维度值采集
   - 计算均值和标准差
   - 识别最弱 3 个维度

2. **Phase 2: 方向性变异**（1 周期）
   - 实现 mutate_shortcoming()
   - 集成到心跳循环
   - 验证变异确实作用于短板

3. **Phase 3: 交叉操作**（2 周期）
   - 实现 crossover_mutate()
   - 与现有 SkillEngine 联动
   - 测试交叉增益

4. **Phase 4: 反馈环**（持续）
   - 记录每次变异的效果
   - 调整变异策略权重
   - 目标：C_m 稳定在 1.2+

### 3.3 预期提升
```
当前 C_m = 0.95
├── +0.10: 短板驱动变异（定向 > 随机）
├── +0.08: 交叉操作增益
├── +0.05: 反馈环策略自适应
├── +0.02: 多样性保持（避免早熟收敛）
└── 目标 C_m = 1.20
```

---

## 四、D_s 提升方案（1.30 → 1.5）

### 4.1 差距分析
| 维度 | 当前值 | 目标值 | 差距 | 策略 |
|------|--------|--------|------|------|
| 修改验证 | ❌ 无 | ✅ 修改前验证 | 大 | 实现 validate_mutation() |
| 效果审计 | ❌ 无 | ✅ 修改后审计 | 大 | 实现 audit_mutation() |
| 安全回滚 | ❌ 无 | ✅ 自动回滚 | 大 | 实现 rollback_mutation() |
| V_success 计算 | ❌ 无 | ✅ 实时计算 | 中 | 从审计结果统计 |

### 4.2 具体实施步骤

1. **Phase 1: 修改前验证**（1 周期）
   - 实现 validate_mutation() — 5 项安全检查
   - 阻止危险变异（归零、大幅下降、安全维度降级）
   - 记录被阻止的变异（学习边界）

2. **Phase 2: 修改后审计**（1 周期）
   - 实现 audit_mutation() — 对比修改前后状态
   - 计算 health_change 和 theta_change
   - 决定 accept/reject/rollback

3. **Phase 3: 自动回滚**（1 周期）
   - 实现 state backup before mutation
   - 实现 rollback_mutation() — 恢复备份
   - 记录回滚事件（分析失败原因）

4. **Phase 4: V_success 实时统计**（持续）
   - 从审计结果中提取 acceptance_rate
   - 更新 D_s 公式中的 V_success
   - 目标：D_s 稳定在 1.5+

### 4.3 预期提升
```
当前 D_s = 1.30
├── +0.08: 修改前验证（阻止有害修改 → V_success 上升）
├── +0.06: 修改后审计（及时发现并回滚坏修改）
├── +0.04: 安全回滚（系统不退化 → 净修改量稳定）
├── +0.02: V_success 实时反馈（动态调整修改幅度）
└── 目标 D_s = 1.50
```

---

## 五、变异-选择-保留闭环实现

### 5.1 闭环流程图

```
┌──────────┐     ┌──────────┐     ┌──────────┐     ┌──────────┐
│  诊断     │────▶│  变异     │────▶│  审计     │────▶│  保留     │
│  Diagnose │     │  Mutate  │     │  Audit   │     │  Retain  │
└──────────┘     └──────────┘     └──────────┘     └──────────┘
      │                │                │                │
      ▼                ▼                ▼                ▼
  短板识别        方向性变异        效果验证          经验沉淀
  优先级排序      安全边界检查      accept/reject    策略调整
  目标设定        梯度引导         rollback          基因库更新
      │                                                      │
      └──────────────── 反馈环 ◀─────────────────────────────┘
```

### 5.2 每周期执行流程

```python
def mutation_cycle(core_plus):
    """单周期变异闭环"""
    
    # 1. 诊断：识别短板
    weaknesses = detect_weaknesses(core_plus.dimensions)
    
    # 2. 变异：方向性变异
    mutations = []
    for w in weaknesses[:3]:
        m = mutate_shortcoming(core_plus.dimensions, [w])
        mutations.extend(m)
    
    # 3. 验证：修改前安全检查
    safe_mutations = []
    for m in mutations:
        valid, checks = validate_mutation(m, core_plus.state)
        if valid:
            safe_mutations.append(m)
        else:
            log_blocked_mutation(m, checks)
    
    # 4. 执行变异
    pre_state = backup_state(core_plus)
    for m in safe_mutations:
        apply_mutation(core_plus, m)
    
    # 5. 审计：修改后效果检查
    post_state = capture_state(core_plus)
    audits = [audit_mutation(m, pre_state, post_state) for m in safe_mutations]
    
    # 6. 选择：保留有益，拒绝/回滚有害
    result = select_mutations(safe_mutations, audits)
    
    # 7. 回滚有害变异
    for m in result["rolled_back"]:
        rollback_mutation(m, pre_state)
    
    # 8. 经验沉淀
    for m, a in zip(result["accepted"], [a for a in audits if a["verdict"] == "accept"]):
        exp = crystallize_experience(m, a, core_plus.state["cycle"])
        core_plus._log_experience("mutation", json.dumps(exp))
    
    # 9. 反馈：调整变异策略
    feedback_loop.update(exp)
    
    return result
```

---

## 六、实施计划

### Phase 1: 基础设施（Day 1）
- [ ] 在 core-plus.py 中添加维度值采集方法
- [ ] 实现 detect_weaknesses() 短板检测
- [ ] 实现 validate_mutation() 修改前验证
- [ ] 实现 audit_mutation() 修改后审计

### Phase 2: 变异操作（Day 2）
- [ ] 实现 mutate_shortcoming() 方向性变异
- [ ] 实现 crossover_mutate() 交叉变异
- [ ] 集成到 CorePlus.evolve() 心跳循环

### Phase 3: 闭环完善（Day 3）
- [ ] 实现 rollback_mutation() 自动回滚
- [ ] 实现 select_mutations() 选择机制
- [ ] 实现 MutationFeedbackLoop 反馈环

### Phase 4: 验证优化（Day 4）
- [ ] 运行 10 个周期验证闭环
- [ ] 对比 C_m/D_s 变化
- [ ] 调整参数优化效果

---

## 七、预期成果

### 量化指标
| 指标 | 当前 | 目标 | 预期 |
|------|------|------|------|
| C_m | 0.95 | 1.20 | 1.20+ |
| D_s | 1.30 | 1.50 | 1.50+ |
| θ_dot | 0.004 | >0.01 | 0.01+ |
| stagnation | 275 | <10 | 0 |
| health | 80 | >85 | 90+ |

### 质性提升
1. **变异有方向**：不再盲目随机，而是精准攻击短板
2. **自修改有安全**：修改前验证 + 修改后审计 + 自动回滚
3. **变异-选择-保留**：形成真正的进化闭环
4. **策略自适应**：反馈环让变异策略越来越聪明

---

> **核心哲学**：变异不是随机扰动，是"看到短板→精准攻击→验证效果→沉淀经验"的闭环。
> 这就是从"盲人摸象"到"外科手术"的进化。
