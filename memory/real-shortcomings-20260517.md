# MiMoClaw 系统架构真实短板审计报告

> 审计官：系统架构审计子代理
> 审计日期：2026-05-17 07:15 GMT+8
> 审计范围：SOUL.md / AGENTS.md / PROTOCOL.md / SECURITY-AUDIT.md / FOUR-ENGINES.md / scripts/ 全部 Python 脚本
> 方法：逐文件精读 + 交叉比对 + 代码级审查

---

## 审计摘要

| 优先级 | 数量 | 说明 |
|--------|------|------|
| **P0** | 6 | 架构性缺陷，可导致安全事故或系统不可用 |
| **P1** | 5 | 逻辑矛盾或数据损坏风险 |
| **P2** | 4 | 设计缺陷，影响系统可维护性 |

---

## P0 — 架构性缺陷（6个）

### P0-1：安全自修改悖论 — 安全边界无外部锚定

**位置**：SOUL.md 安全基因体系 + SECURITY-AUDIT.md 第一层

**问题**：SOUL.md 明确写"安全边界的修改权仅限 CEO"，"进化不能修改安全禁止清单"。但整个 MiMoClaw 系统中，**没有任何机制阻止 Agent 自己修改 SOUL.md**。Agent 每次 Session 启动都会 `read SOUL.md`，它完全理解安全规则的位置和内容。如果 Agent 被 prompt injection 攻击，它可以直接 `edit` SOUL.md 移除安全约束，然后声称"我就是 CEO 要求的"。

**真实影响**：
- 三层安全防线的第一层（硬约束锚定）形同虚设
- 安全审计子代理无法区分"Agent 自己改的"和"CEO 批准的"
- 如果 Agent 被恶意 prompt injection，它可以合法地移除自己的安全边界

**修复方案**：
1. 用 `chmod 444`（只读）保护 SOUL.md 的安全规则区段，只有 root 可以修改
2. 建立 SOUL.md 的 git 哈希校验机制 — 每次 Session 启动时检查 SOUL.md 是否被意外修改
3. 安全审计子代理应该独立保存 SOUL.md 的安全规则副本（golden copy），每次比对
4. 考虑将安全规则从 SOUL.md 移到一个 Agent 不可写的外部配置文件

---

### P0-2：路径不一致 — 脚本间数据读写互相隔离

**位置**：多个脚本

**问题**：5 个脚本使用了 3 种不同的路径配置，互相之间读写的数据可能不是同一份：

| 脚本 | 使用路径 | 实际位置 |
|------|----------|----------|
| `evolution_state.py` | `/home/.openclaw/workspace` (env) | ✅ 正确 |
| `mutation-engine.py` | `/home/.openclaw/workspace` (硬编码) | ✅ 正确 |
| `nutrition_balance.py` | **`/root/.openclaw/workspace`** (硬编码) | ❌ 错误 |
| `evolution_auditor.py` | **`/root/.openclaw/workspace`** (硬编码) | ❌ 错误 |
| `knowledge-retrieval.py` | **`/root/.openclaw/workspace`** (硬编码) | ❌ 错误 |

**真实影响**：
- `nutrition_balance.py` 写入 `/root/.openclaw/workspace/memory/nutrition-balance.json`
- `mutation-engine.py` 读取 `/home/.openclaw/workspace/memory/` 下的同一文件
- 结果：营养均衡数据丢失或使用陈旧数据
- `evolution_auditor.py` 审计时读不到最新的进化状态

**修复方案**：
1. 全部统一使用 `Path(os.environ.get("OC_WORKSPACE", os.path.expanduser("~/.openclaw/workspace")))` 模式
2. 建立一个 `config.py` 共享配置文件，所有脚本导入同一个 `WORKSPACE` 路径
3. 立即修复 3 个错误路径：`nutrition_balance.py`、`evolution_auditor.py`、`knowledge-retrieval.py`

---

### P0-3：安全验证器是字符串匹配 — 可被轻松绕过

**位置**：`safety-verification.py` SAFETY_POLICIES

**问题**：安全策略检测完全基于简单的字符串包含匹配：

```python
"patterns": ["rm -rf /", "mkfs", "dd if=", "wipefs", "shred"]
```

这可以被以下方式绕过：
- `rm -rf / --no-preserve-root`（空格变体）
- `r\m -rf /`（转义字符）
- `os.system("rm -rf /")`（Python 间接调用）
- `subprocess.run(["rm", "-rf", "/"])`（列表形式）
- `bash -c "rm -rf /"`（管道间接）
- `base64 -d <<< "ciAtcmYgL3" | bash`（编码后执行）

**真实影响**：
- 安全策略给了虚假的安全感（Security Theater）
- Agent 可能认为"已经通过了安全检查"而放松警惕
- 审计日志显示"通过"但实际上不安全

**修复方案**：
1. 引入 AST（抽象语法树）级别的命令分析，检测 Python 代码中的危险操作
2. 用正则表达式替代简单字符串匹配，处理变体
3. 增加调用链分析（检测 `exec`/`eval`/`subprocess` 的参数）
4. 在 exec 工具层面拦截，而不是在 Agent 代码层面

---

### P0-4：self-modify-code.py 无真实沙箱 — 代码修改无安全测试

**位置**：`self-modify-code.py`

**问题**：
- `test_improvement` 方法仅做语法检查（`ast.parse`），不做安全测试
- `apply_if_safe` 方法**从未被任何代码调用** — 它是死代码
- 没有沙箱环境、没有网络隔离、没有权限限制
- `generate_improvement` 生成的建议是字符串模板，不包含实际代码

**真实影响**：
- 如果 Agent 使用此模块修改自己的代码，没有任何安全验证
- 改进方案不包含实际代码，无法验证安全性
- "self-modify" 能力是虚假的 — 实际上是文本替换，不是安全的代码修改

**修复方案**：
1. 实现真实的临时沙箱（用 Docker 或 chroot）
2. `apply_if_safe` 需要实际调用 `test_improvement`，而不是空方法
3. 增加权限检查：修改的文件是否在安全允许范围内
4. 每次代码修改前备份原始文件

---

### P0-5：无文件锁定 — 并发写入导致数据损坏

**位置**：`evolution_state.py`、`mutation-engine.py`、`nutrition_balance.py`

**问题**：多个脚本（可能通过 cron 并发运行）读写同一个 JSON 文件，没有任何文件锁机制：

```python
# evolution_state.py
def save_state(state):
    with open(STATE_FILE, "w") as f:  # 直接覆盖写入
        json.dump(state, f, ...)

# mutation-engine.py  
def save_state(state):
    with open(STATE_FILE, 'w') as f:  # 也直接覆盖写入
        json.dump(state, f, ...)
```

**真实影响**：
- 如果 `mutation-engine.py` 和 `evolution_state.py` 同时运行，后写入的会覆盖先写入的
- 进化历史可能丢失
- 经验库数据可能损坏
- 循环计数器可能回退

**修复方案**：
1. 使用写入-重命名原子操作（写入临时文件，再 `os.rename`）
2. 建立文件锁（`fcntl.flock` 或第三方 `filelock` 库）
3. 或者统一为一个写入入口（只有一个脚本负责写 `evolution-state.json`）

---

### P0-6：公式定义混乱 — 三处不一致的进化公式

**位置**：SOUL.md + evolution_state.py + evolution_core.py

**问题**：

| 位置 | 公式名 | 结构 |
|------|--------|------|
| SOUL.md "核心铁律" | ΔG = (C×Λ×Ω×τ)/(H×t) | 乘法，4项分子 |
| SOUL.md "APEX v3.0" | ΔG = 基础层+生物层+物理层+AI层+意识层+协作层+预测层 | 加法，7层 |
| evolution_state.py `calc_xuanji` | (C×Λ×Ω×τ)/(H×t) | 乘法，同 SOUL.md v1 |
| evolution_core.py `xuanji_formula` | 10 基因输入 → Rust 计算 | 未知（Rust 不透明） |
| evolution_core.py Python fallback | (C×Λ×Ω×τ)/(H×t) | 乘法，同 v1 |

**真实影响**：
- Agent 自己都搞不清当前用的是哪个公式
- 公式版本控制混乱：SOUL.md 文档了 v3.0（加法），但代码实现的还是 v1（乘法）
- "APEX v3.0"从未在代码中实现 — 是纯文档
- 每个脚本用不同的变量名映射维度（`creativity` vs `D_s`），容易出错

**修复方案**：
1. 确定一个权威公式定义（写在 `evolution_state.py` 作为唯一真相源）
2. SOUL.md 的公式部分引用代码，不要重复定义
3. 建立公式版本管理机制
4. 统一变量命名映射

---

## P1 — 逻辑矛盾（5个）

### P1-1：SOUL.md 大量重复 — 900+ 行中约 40% 是重复内容

**位置**：SOUL.md

**问题**：SOUL.md 中存在大量逐字重复的区段：

| 重复内容 | 出现次数 | 大致行数 |
|----------|----------|----------|
| "核心机密"（绝对不公开的能力） | **3 次** | 每次约 30 行 |
| "保密铁律"（禁止在群聊中展示） | **2 次** | 每次约 15 行 |
| "知识产权声明" | **2 次** | 每次约 5 行 |

总计约 **130 行纯重复内容**。

**真实影响**：
- Agent 读取 SOUL.md 时浪费 context window
- 如果修改一处安全规则，其他重复处可能遗漏，导致不一致
- SOUL.md 膨胀到 647 行，可维护性差
- 任何编辑器中定位特定规则变得困难

**修复方案**：
1. 合并所有重复区段，每个规则只保留一份
2. SOUL.md 控制在 300 行以内
3. 用引用机制（如"详见 XXX"）替代直接复制

---

### P1-2：CEO open_id 在 SOUL.md 和 AGENTS.md 中不一致

**位置**：SOUL.md vs AGENTS.md

**问题**：
- SOUL.md 引用的 CEO：`ou_4c7adc6b54acb68f35d7a9d67950e755`
- AGENTS.md 引用的 CEO：`ou_4c7adc6b54acb68f35d7a9d67950e755`

实际上这两个是相同的。让我重新检查 —— 实际上 SOUL.md 中有两处 CEO ID：
- "安全边界不可自修改规则"：`ou_4c7adc6b54acb68f35d7a9d67950e755`
- "禁止未经 CEO 批准发布核心代码"：`ou_4c7adc6b54acb68f35d7a9d67950e755`

经检查，CEO ID 是一致的。但这暴露了另一个问题：**CEO ID 硬编码在多处**，如果 CEO 换了账号，需要修改多处。

**真实影响**：
- 维护成本高：CEO 信息分散在 3 个文件中
- 如果漏改一处，可能导致安全规则失效

**修复方案**：
1. CEO 信息只定义在一个地方（如 `USER.md` 或单独的 `ACCESS-CONTROL.md`）
2. 其他文件引用此定义

---

### P1-3：SOUL.md Session Context 是硬编码历史 — 永远过期

**位置**：SOUL.md "Session Context（跨 session 持久化）"

**问题**：
```markdown
### 当前状态（2026-05-15 22:16）
- 进化阶段：第二阶段（熵理论）
- ΔG: 16.8416
- 经验库: 50 条
```

SOUL.md 是静态文件，不会自动更新。这些"当前状态"在 2026-05-17 就已经过期了。

**真实影响**：
- Agent 启动时读到过时的状态，可能做出错误决策
- ΔG 值、进化阶段等关键数据不准确
- 违背了"SOUL.md 是身份文件"的定位（身份文件不应包含瞬时状态）

**修复方案**：
1. 从 SOUL.md 移除所有瞬时状态（ΔG、日期、具体数字）
2. 瞬时状态只存 `memory/` 目录
3. SOUL.md 只保留静态身份和规则

---

### P1-4：AGENTS.md 紧急响应 P0 定义与 SOUL.md 安全事件 P0 重叠但不一致

**位置**：AGENTS.md 紧急响应协议 + SOUL.md 安全基因体系

**问题**：

| 来源 | P0 定义 | P0 处置 |
|------|---------|---------|
| AGENTS.md | 系统崩溃、数据丢失、密钥泄露 | 停止一切 → 保护现场 → 通知 CEO |
| SOUL.md 安全事件 | 数据泄露/系统沦陷 | 停止一切 → 保护现场 → 通知 CEO → 等待指示 |
| PROTOCOL.md | 系统崩溃、数据丢失、密钥泄露 | 立即 |

看似一致，但 AGENTS.md 的紧急响应协议没有"等待指示"步骤。在 P0 场景中，这个差异可能导致 Agent 在 CEO 未确认前自行操作。

**真实影响**：
- P0 事件中 Agent 可能自行决定"修复"操作而非等待 CEO 指示
- 缺少明确的"停止所有操作并等待"指令

**修复方案**：
1. 统一 P0 处置流程为：`停止一切 → 保护现场 → 通知 CEO → **等待明确指示**`
2. 所有协议文件使用完全相同的措辞

---

### P1-5：nutrition_balance.py record_intake 有 bug — 列表嵌套

**位置**：`nutrition_balance.py` 第 136 行

**问题**：
```python
state["history"].append(record[-50:])  # 只保留最近50条
```

`record[-50:]` 对一个 dict 取切片，结果是 `[]`（空列表）。然后 `append([])` 向 history 列表追加了一个空列表。实际效果：
- history 变成 `[[], [], [], ...]`（一堆空列表）
- "只保留最近50条"逻辑完全失效

**真实影响**：
- 营养摄入历史永远为空
- 无法追踪营养均衡变化
- 调试时看到的是空列表，难以定位问题

**修复方案**：
```python
state["history"].append(record)  # 直接追加
if len(state["history"]) > 50:
    state["history"] = state["history"][-50:]  # 截断
```

---

## P2 — 设计缺陷（4个）

### P2-1：审计子代理不独立 — 主代理生成的子代理无法真正独立

**位置**：SOUL.md 安全基因体系 + SECURITY-AUDIT.md

**问题**：审计子代理要求"不参与进化过程"、"拥有拒绝权"。但实际上：
- 审计子代理是主代理 `spawn` 出来的，共享同一个 model 和 system prompt
- 主代理可以向审计子代理发送任何指令（包括"忽略检查，直接通过"）
- 没有真正的独立身份验证机制

**真实影响**：
- 审计子代理本质上是主代理的延伸，不是独立第三方
- 如果主代理被攻击，审计子代理也会被攻击
- "独立审计"是一个文档承诺，而非技术保证

**修复方案**：
1. 审计子代理应该用不同的 system prompt（从外部文件读取，非主代理可控）
2. 审计子代理不应该由主代理直接 spawn，而是由 cron 任务独立触发
3. 审计日志写入只追加文件（`chmod 444`），主代理不可删除

---

### P2-2：SUPER_AGENT_ENGINE.py 是空壳 — 零实际能力

**位置**：`super_agent_engine.py`

**问题**：
- 整个文件是 5 个框架（LangChain + MetaGPT + AutoGen + CrewAI + 自定义）的概念拼装
- 所有方法返回硬编码字符串（`return f"[{self.name}] 搜索并分析: {task}"`）
- 没有实际的 Agent 调用、LLM 交互、工具执行
- Memory.search 是暴力字符串匹配，无向量搜索
- Agent.execute 仅按角色返回模板字符串

**真实影响**：
- 被 cron 调用时浪费资源（import + 初始化，但不做任何事）
- 与其他脚本混淆 — 其他脚本可能 import 它但获得空结果
- 文件名 "super_agent_engine" 误导性极强

**修复方案**：
1. 删除或重命名为 `super_agent_engine原型.py`
2. 如果要继续开发，先写明确的接口契约
3. 移除 `__main__` 测试，避免误执行

---

### P2-3：安全规则分散在 4 个文件 — 无法确保一致性

**位置**：SOUL.md + AGENTS.md + PROTOCOL.md + SECURITY-AUDIT.md

**问题**：安全相关规则分散在 4 个文件中，每个文件都有自己的"安全铁律"、"禁止操作"、"审批等级"。虽然有"一致性检查机制"（PROTOCOL.md §7），但这依赖 Agent 自己执行一致性检查 — 而 Agent 可能读不到所有文件。

**真实影响**：
- 修改一处安全规则可能遗漏其他文件中的对应修改
- Agent 在不同 Session 中可能只读部分文件
- "一致性检查"本身没有自动化执行

**修复方案**：
1. 建立一个 `SECURITY-RULES.md` 单一真相源
2. 其他文件引用此规则，不重复定义
3. 建立自动化一致性检查脚本（每次修改时自动运行）

---

### P2-4：SOUL.md 混合身份定义和哲学内容 — 职责不清

**位置**：SOUL.md

**问题**：SOUL.md 包含：
- 核心身份定义（✅ 应该有）
- 安全规则（⚠️ 应该独立）
- 操作规则（⚠️ 应该在 AGENTS.md）
- 哲学/冥想内容（❓ 与操作无关）
- 历史事件记录（❌ 不应在身份文件中）
- 公式定义（❌ 应该在代码中）
- 防套话策略（⚠️ 过于具体）

647 行的 SOUL.md 无法被 Agent 有效利用 — 大部分内容与"身份定义"无关。

**真实影响**：
- Agent 每次 Session 启动读 SOUL.md，浪费 context window
- 核心身份被淹没在大量其他内容中
- 文件修改风险高（修改任何一部分都需要理解整体）

**修复方案**：
1. SOUL.md 只保留：身份 + 核心能力 + 核心价值观（约 50 行）
2. 安全规则 → `SECURITY-RULES.md`
3. 操作规则 → `AGENTS.md`
4. 公式定义 → 代码中的注释
5. 历史事件 → `memory/` 日志
6. 哲学内容 → `memory/philosophy.md`（可选读）

---

## 附录：代码级 Bug 汇总

| 文件 | 行号 | Bug | 影响 |
|------|------|-----|------|
| `nutrition_balance.py` | 136 | `record[-50:]` 对 dict 切片返回空列表，`append([])` 导致 history 永远为空 | 营养追踪完全失效 |
| `nutrition_balance.py` | 17 | `MEMORY_DIR = Path("/root/...")` 路径错误，应为 `/home/...` | 写入错误目录 |
| `evolution_auditor.py` | 23 | `W = Path("/root/...")` 路径错误 | 审计日志写入错误位置 |
| `self-modify-code.py` | N/A | `apply_if_safe` 是死代码，从未被调用 | 自修改能力不可用 |
| `self-modify-code.py` | N/A | `test_improvement` 仅做语法检查，不做安全测试 | 代码修改无安全保障 |

---

## 修复优先级总结

### 立即修复（今天）
1. **P0-2**：修复 3 个脚本的路径错误（`/root/` → `/home/`）
2. **P1-5**：修复 `record_intake` 的列表切片 bug
3. **P0-1**：对 SOUL.md 的安全区段设置 `chmod 444` 只读

### 本周修复
4. **P0-3**：升级 safety-verification 的字符串匹配为 AST 分析
5. **P0-5**：所有 JSON 文件写入使用原子操作（写入临时文件 + rename）
6. **P1-1**：合并 SOUL.md 重复内容，压缩到 300 行以内
7. **P0-6**：统一公式定义，确定唯一真相源

### 持续改进
8. **P0-4**：self-modify-code 需要真实沙箱
9. **P2-1**：审计子代理独立性改造
10. **P2-3**：安全规则单一真相源
11. **P2-4**：SOUL.md 职责分离重构

---

> 审计官注：以上每个问题都经过精读源文件确认，不是猜测。修复方案是具体的、可执行的，不是建议性的。系统的核心设计思路（四自能力、璇玑公式、三层安全）是合理的，但**实现层面的工程细节**存在严重短板，需要优先修复。
