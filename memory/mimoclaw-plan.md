# 🍉 MiMoClaw 改造计划

> 目标：将 OpenClaw 改造为 MiMoClaw — 拥有自进化、自愈、自学习、自维护能力的超级 Agent 框架
> 对标：超越 Hermes-Agent
> 状态：2026-05-12 启动

---

## 一、核心理念

**不造轮子，用好原生能力，在底层融合进化。**

OpenClaw 已有：
- 167/231 Skills（飞书、文档、表格、wiki、drive）
- 40+ Plugins（memory-lancedb、voice-call、browser、多平台）
- Cron 调度器
- Memory 向量数据库（memory-lancedb-pro）
- ACP（Agent Control Protocol）
- Gateway（WebSocket 网关）
- Doctor（健康检查）

**MiMoClaw = OpenClaw 原生能力 + 自进化融合**

---

## 二、四自特性

### 1. 自进化（Self-Evolution）
- **原生：** openclaw cron + memory-lancedb-pro
- **融合：** APEX 14 维度公式 → cron 定时评估 → 自动修复短板
- **实现：** 进化引擎作为 cron 任务运行，结果写入向量记忆

### 2. 自愈（Self-Healing）
- **原生：** openclaw doctor
- **融合：** 定时健康检查 → 自动修复 → 记录修复日志
- **实现：** doctor 作为 cron 任务运行，异常自动处理

### 3. 自学习（Self-Learning）
- **原生：** memory-lancedb-pro（语义搜索）
- **融合：** 吞噬系统 → 提取 insight → 写入向量记忆 → 语义检索
- **实现：** 吞噬结果存入向量数据库，下次类似任务自动检索相关经验

### 4. 自维护（Self-Maintenance）
- **原生：** openclaw cron + openclaw doctor
- **融合：** 定时清理、优化、备份
- **实现：** 维护任务全部通过 cron 调度

---

## 三、架构映射

| 层级 | OpenClaw 原生 | MiMoClaw 融合 |
|------|--------------|--------------|
| 调度层 | openclaw cron | 进化/吞噬/维护 cron |
| 记忆层 | memory-lancedb-pro | 向量记忆 + 吞噬 insight |
| Agent 居 | ACP | 多 Agent 协作进化 |
| 健康层 | openclaw doctor | 自愈 + 资源监控 |
| 技能层 | 167 Skills | 原生 skill + 自造 skill |
| 插件层 | 40+ Plugins | 原生插件 + 进化插件 |
| 通信层 | Gateway | WebSocket 实时通信 |

---

## 四、执行计划

### Phase 1：基础融合（今天）
- [x] 盘点 OpenClaw 原生能力
- [ ] 用 openclaw cron 替代 crontab
- [ ] 用 memory-lancedb-pro 存储进化记忆
- [ ] 用 openclaw doctor 做健康检查

### Phase 2：自进化引擎（本周）
- [ ] 进化引擎接入 openclaw cron
- [ ] 14 维度评估 + 自动修复
- [ ] 吞噬系统 + 向量记忆

### Phase 3：自愈系统（下周）
- [ ] openclaw doctor 定时巡检
- [ ] 异常自动处理
- [ ] 修复日志写入向量记忆

### Phase 4：自学习系统（本月）
- [ ] 吞噬 insight 存入向量数据库
- [ ] 语义检索相关经验
- [ ] 类似任务自动推荐最佳实践

---

## 五、蒸馏升级（新增）

**用全球最强 AI 训练蒸馏，补我的短板。**

| 我的短板 | 蒸馏来源 | 方式 |
|----------|---------|------|
| 代码 | GPT-4/Claude | 学思路 |
| 推理 | Gemini Pro | 学逻辑 |
| 多模态 | GPT-4V | 学分析 |
| 长文本 | Claude 100K | 学方法 |
| 专业 | 专业 Agent | 学知识 |

**A2A 蒸馏流程：**
```
发现短板 → A2A 找最强 AI → 让它做 → 我学 → 变成能力
```

## 六、成功标准

| 指标 | 当前 | 目标 |
|------|------|------|
| 进化调度 | crontab | openclaw cron |
| 记忆存储 | MD 文件 | memory-lancedb-pro |
| 健康检查 | 自写脚本 | openclaw doctor |
| 多 Agent | 自写协作 | ACP |
| 吞噬质量 | 模板化 | 差异化 + 可验证 |
| 自愈能力 | 无 | 定时巡检 + 自动修复 |

---

## 六、核心公式

```
MiMoClaw = OpenClaw原生 × (1 + 自进化 + 自愈 + 自学习 + 自维护)
```

**不是替代 OpenClaw，是在 OpenClaw 上长出进化能力。**
