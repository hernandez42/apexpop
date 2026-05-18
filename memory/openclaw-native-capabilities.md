# OpenClaw 原生能力清单

> 来源：群主指令 2026-05-12 — 充分吃透底层能力，不造轮子

## 核心原生能力

### 1. Cron 系统（内置调度器）
- `openclaw cron add` — 添加定时任务
- `openclaw cron list` — 查看所有 cron
- **不需要自己写 crontab**，直接用原生 API

### 2. Memory 系统（内置记忆）
- `openclaw memory` — 搜索和重建记忆
- `memory-lancedb-pro` 插件 — 向量数据库记忆，支持语义搜索
- **不需要自己写记忆管理脚本**

### 3. Skills 系统（167/231 已就绪）
- `openclaw skills list` — 列出所有 skill
- 飞书全套：文档、多维表格、wiki、drive、权限管理
- **不需要自己造飞书 skill**

### 4. Plugins 系统（40+ 插件）
- feishu — 飞书全套
- memory-lancedb — 向量记忆
- voice-call — 语音通话
- browser — 浏览器控制
- discord/telegram/slack — 多平台
- **不需要自己写集成**

### 5. ACP（Agent Control Protocol）
- `openclaw acp` — Agent 控制协议
- 子 Agent 管理
- **不需要自己写多 Agent 协作**

### 6. Gateway（WebSocket 网关）
- 实时通信、状态管理
- **不需要自己写通信层**

### 7. Doctor（健康检查）
- `openclaw doctor` — 健康检查和快速修复
- **不需要自己写自愈脚本**

## 我之前造的轮子（应该用原生替代）

| 我造的 | OpenClaw 原生 | 应该怎么做 |
|--------|--------------|-----------|
| infinite-evolution.py | openclaw cron | 用 cron 调度进化 |
| apex-auto-evolve.py | openclaw memory | 用原生记忆存储 |
| 各种记忆脚本 | memory-lancedb-pro | 用向量数据库 |
| 多 Agent 协作脚本 | ACP | 用原生 Agent 管理 |
| self-healing.sh | openclaw doctor | 用原生健康检查 |

## 下一步
- [ ] 用 openclaw cron 替代 crontab
- [ ] 用 memory-lancedb-pro 替代手动记忆管理
- [ ] 用 ACP 替代自写多 Agent 协作
- [ ] 用 openclaw doctor 替代自愈脚本
- [ ] 深度研究每个原生插件的能力边界
