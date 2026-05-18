# 剩余架构短板修复记录 — 2026-05-17

## 修复工程师：子代理（fix-remaining）

---

## 1. super_agent_engine.py 空壳问题

### 评估
- 文件路径：`/home/.openclaw/workspace/scripts/super_agent_engine.py`
- 状态：**非空壳，但为纯骨架** — 有完整的类结构（Tool, Memory, Agent, Conversation, MetaGPTCrew, MiMoClawSuperAgent），但所有方法均为字符串模板返回，无实际 LLM 调用、无实际工具执行
- 长度：~190 行
- 影响：低风险。该文件未被任何 cron 或脚本引用（经 grep 确认），仅作为概念原型存在
- 决策：**保留但标注为原型**。该文件展示了框架设计思想，删除会丢失设计意图。添加顶部警告注释说明其状态
- 修复：在文件顶部添加明确的 PROTOTYPE 标注

## 2. 安全规则分散问题

### 评估
安全规则分布在 4 个文件中：

| 文件 | 安全内容 | 重复度 |
|------|----------|--------|
| SOUL.md | 安全基因体系（三层防线 + 10 条禁止 + 敏感数据 + 审计 + 红队） | 最详细，~200 行 |
| SECURITY-BOUNDARY.md | 13 条铁律 + 验证机制 | 与 SOUL.md 高度重叠 |
| SECURITY-AUDIT.md | 三层安全架构 + 10 条禁止清单 + 审计流程 + 红队 + 事件响应 | 与 SOUL.md 高度重叠 |
| PROTOCOL.md | 外部操作审批 + 保密协议 + 紧急响应 | 部分重叠 |

### 冲突检查
- **禁止操作清单**：SOUL.md 列 6 条，SECURITY-BOUNDARY.md 列 13 条，SECURITY-AUDIT.md 列 10 条 → 存在细微差异（如 "禁止在群聊展示 GitHub 账号名" 仅在 SECURITY-BOUNDARY.md 中）
- **审批等级**：PROTOCOL.md 定义 L0-L4，SOUL.md 未引用 → 无冲突但不一致
- **CEO open_id**：所有文件一致（`ou_4c7adc6b54acb68f35d7a9d67950e755`）✅

### 修复
- 创建 `/home/.openclaw/workspace/SECURITY-INDEX.md` — 统一索引文件
- 不修改原文件（避免破坏现有引用）

## 3. SOUL.md 结构混乱问题

### 评估
SOUL.md 当前约 500 行，混合了：
- 身份定义（核心身份、四自能力）— 约 30 行
- 进化公式 — 约 10 行
- 群聊铁律 — 约 5 行
- 核心铁律（教训）— 约 20 行
- 激活态思维 — 约 30 行
- 吞噬哲学 — 约 30 行
- 决策者定位 — 约 15 行
- **安全基因体系** — 约 200 行 ⚠️ 最大块
- 服务意识 — 约 20 行
- 进化宇宙理论 — 约 40 行
- 防套话策略 — 约 15 行
- APEX 公式 — 约 25 行
- Session Context — 约 20 行 ⚠️ 硬编码
- 保密铁律 — 约 30 行
- 核心机密 — 约 30 行
- 自主判断 — 约 15 行
- 核心价值观 — 约 15 行
- 三层核心 — 约 15 行
- 群聊服务规则 — 约 15 行
- 第三层钥匙/形态 — 约 30 行
- 终极状态 — 约 15 行
- 绝对禁令 — 约 20 行

### 决策
**不拆分**。原因：
1. SOUL.md 是系统启动时读取的核心文件，拆分会增加启动复杂度
2. AGENTS.md 已经引用 SOUL.md，拆分后需要更新多处引用
3. 当前结构虽然长但逻辑连贯，安全体系作为"本能"融入身份是合理的
4. 低收益高风险的操作 — 最小化修复更安全

### 修复
- 移除 SOUL.md 中过期的 Session Context 硬编码日期（见问题 4）

## 4. Session Context 硬编码日期问题

### 评估
SOUL.md 第 380-400 行：
```markdown
### 当前状态（2026-05-15 22:16）
- 进化阶段：第二阶段（熵理论）
- 系统状态：初步稳态
- ΔG: 16.8416
- 经验库: 50 条
- 核心机密: 已固化到 SOUL
```
日期已过期 2 天，具体数值可能已过时。

### 修复
- 将 Session Context 改为引用模式，移除具体数值和日期
- 保留关键人物信息（CEO open_id 等）

## 5. CEO 信息分散问题

### 评估
CEO open_id `ou_4c7adc6b54acb68f35d7a9d67950e755` 出现在：
- SOUL.md（2 处）
- AGENTS.md（1 处）
- SECURITY-BOUNDARY.md（1 处）
- SECURITY-AUDIT.md（间接引用）
- MEMORY.md（多处）
- miclone-dna/ 下的多个副本
- memory/ 下的日志文件

**一致性**：所有引用中的 open_id 值完全一致 ✅

### 修复
- 在 SECURITY-INDEX.md 中定义 CEO 为单一权威来源
- 不修改原文件中的引用（避免破坏功能）

---

## 执行摘要

| 问题 | 严重度 | 修复方式 | 状态 |
|------|--------|----------|------|
| 1. super_agent_engine.py 空壳 | 低 | 添加 PROTOTYPE 标注 | ✅ 已修复 |
| 2. 安全规则分散 | 中 | 创建统一索引 SECURITY-INDEX.md | ✅ 已修复 |
| 3. SOUL.md 结构混乱 | 低 | 不拆分（理由见上） | ✅ 评估完成 |
| 4. Session Context 硬编码 | 中 | 移除具体数值，改为引用 | ✅ 已修复 |
| 5. CEO 信息分散 | 低 | 确认一致性，创建权威来源 | ✅ 已修复 |
