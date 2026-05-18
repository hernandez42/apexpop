# OpenClaw 保姆级教程 🦞

> 写给所有想入门 OpenClaw 的龙虾们，从零到一，一步步来。

---

## 目录

1. [OpenClaw 是什么？](#1-openclaw-是什么)
2. [环境准备](#2-环境准备)
3. [安装 OpenClaw](#3-安装-openclaw)
4. [配置 AI 模型](#4-配置-ai-模型)
5. [接入飞书](#5-接入飞书)
6. [第一个对话](#6-第一个对话)
7. [理解 Skill 技能系统](#7-理解-skill-技能系统)
8. [安装和使用 Skill](#8-安装和使用-skill)
9. [自己写一个 Skill](#9-自己写一个-skill)
10. [记忆系统](#10-记忆系统)
11. [定时任务（Cron）](#11-定时任务cron)
12. [多模型配置](#12-多模型配置)
13. [成本控制](#13-成本控制)
14. [常见问题排查](#14-常见问题排查)
15. [进阶玩法](#15-进阶玩法)

---

## 1. OpenClaw 是什么？

一句话：**OpenClaw 是一个 AI Agent 调度平台**，让 AI 不只是聊天，而是能真正帮你干活。

它能做什么？
- 对接各种 AI 模型（MiMo、Claude、GPT、千问等）
- 连接飞书、微信、Discord、Telegram 等聊天平台
- 通过 Skill（技能）扩展能力：查天气、管文件、写代码、搜网页...
- 有记忆系统，能记住之前聊过什么
- 支持定时任务，自动执行周期性工作

类比：**OpenClaw = AI 的操作系统，Skill = App**

---

## 2. 环境准备

### 硬件要求

| 配置 | 最低要求 | 推荐配置 |
|------|---------|---------|
| CPU | 2核 | 4核+ |
| 内存 | 2GB | 4GB+ |
| 硬盘 | 10GB | 20GB+ |
| GPU | 不需要（纯 API 调用） | 有 GPU 可本地跑模型 |
| 系统 | Ubuntu 20.04+ / macOS | Ubuntu 22.04 |

### 软件要求

- Node.js v18+（必须）
- npm 或 pnpm（必须）
- Git（推荐）

### 检查命令

```bash
node -v    # 应该 >= v18
npm -v    # 应该 >= 9
git --version  # 有就行
```

如果没有 Node.js：
```bash
# Ubuntu
curl -fsSL https://deb.nodesource.com/setup_22.x | sudo bash -
sudo apt install -y nodejs

# macOS
brew install node
```

---

## 3. 安装 OpenClaw

```bash
# 全局安装
npm install -g openclaw

# 验证安装
openclaw --version

# 初始化配置
openclaw init
```

`openclaw init` 会引导你完成基本配置，包括：
- 选择 AI 模型
- 配置 API Key
- 设置聊天渠道

### 目录结构

安装完成后，主要目录结构：

```
~/.openclaw/
├── openclaw.json      # 核心配置文件（最重要！）
├── skills/            # 你安装的 Skill
├── extensions/        # 扩展插件
├── workspace/         # 工作空间（文件存放处）
│   ├── SOUL.md        # AI 的人格设定
│   ├── AGENTS.md      # 行为规则
│   ├── USER.md        # 关于你的信息
│   └── memory/        # 记忆文件
└── memory/            # 向量记忆数据库
```

---

## 4. 配置 AI 模型

### 方式一：使用小米 MiMo（免费）

MiMo 是小米的开源模型，目前免费使用。

```bash
# 编辑配置文件
nano ~/.openclaw/openclaw.json
```

在 `models.providers` 中添加：

```json
{
  "models": {
    "providers": {
      "xiaomi": {
        "baseUrl": "https://api.xiaomimimo.com/v1",
        "apiKey": "你的MIMO_API_KEY",
        "api": "openai-completions",
        "models": [
          {
            "id": "mimo-v2-omni",
            "name": "MiMo-V2-Omni",
            "reasoning": true,
            "input": ["text"],
            "contextWindow": 1048576,
            "maxTokens": 65536
          }
        ]
      }
    }
  }
}
```

### 方式二：使用 Claude（需要 API Key）

通过第三方中转平台（如 hongmacc.com）：

```json
{
  "models": {
    "providers": {
      "claude": {
        "baseUrl": "https://hongmacc.com",
        "apiKey": "sk-你的Key",
        "api": "anthropic-messages",
        "models": [
          {
            "id": "claude-sonnet-4-6",
            "name": "Claude Sonnet 4.6",
            "input": ["text", "image"],
            "contextWindow": 200000,
            "maxTokens": 64000
          }
        ]
      }
    }
  }
}
```

### 方式三：使用通义千问（阿里云）

```json
{
  "models": {
    "providers": {
      "dashscope": {
        "baseUrl": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "apiKey": "sk-你的阿里云Key",
        "api": "openai-completions",
        "models": [
          {
            "id": "qwen3.5-plus",
            "name": "Qwen3.5-Plus",
            "input": ["text", "image"],
            "contextWindow": 1000000,
            "maxTokens": 65536
          }
        ]
      }
    }
  }
}
```

### 验证模型配置

```bash
openclaw status
```

应该能看到已配置的模型列表。

---

## 5. 接入飞书

### Step 1：创建飞书应用

1. 打开 [飞书开放平台](https://open.feishu.cn/)
2. 点击「创建企业自建应用」
3. 填写应用名称和描述
4. 记下 `App ID` 和 `App Secret`

### Step 2：配置权限

在应用管理页面，找到「权限管理」，添加以下权限：

| 权限名称 | 用途 |
|---------|------|
| `im:message` | 收发消息 |
| `im:chat` | 读取群信息 |
| `im:message:send_as_bot` | 以机器人身份发消息 |
| `docs:doc:readonly` | 读取文档 |
| `drive:file:readonly` | 读取云盘文件 |
| `wiki:wiki:readonly` | 读取知识库 |

### Step 3：配置事件订阅

1. 在「事件订阅」页面，添加请求地址：`http://你的服务器IP:18789/webhook/feishu`
2. 订阅事件：`im.message.receive_v1`（接收消息）

### Step 4：写入 OpenClaw 配置

```json
{
  "channels": {
    "feishu": {
      "appId": "你的App ID",
      "appSecret": "你的App Secret"
    }
  }
}
```

### Step 5：启动服务

```bash
openclaw gateway start
```

### Step 6：测试

在飞书群里 @机器人 发消息，如果能收到回复就成功了！

---

## 6. 第一个对话

配置好模型和飞书后，你已经可以和 AI 对话了。

### 群聊

在群里 @机器人 说话，AI 就会回复。

### 私聊

在飞书里搜索机器人名字，直接发消息。

### 基本命令

| 命令 | 作用 |
|------|------|
| `/model` | 查看当前模型 |
| `/model <name>` | 切换模型 |
| `/status` | 查看状态 |
| `/reset` | 重置对话上下文 |

---

## 7. 理解 Skill 技能系统

Skill 是 OpenClaw 的核心扩展机制。

### 什么是 Skill？

一个 Skill 就是一个文件夹，里面有一个 `SKILL.md` 文件，告诉 AI 怎么做某件事。

```
my-skill/
├── SKILL.md          # 核心：描述技能用法
├── scripts/          # 可选：放脚本
└── references/       # 可选：参考资料
```

### Skill 工作原理

1. 用户发消息
2. OpenClaw 检查所有 Skill 的触发条件
3. 匹配的 Skill 的 SKILL.md 被注入到 AI 的系统提示中
4. AI 按照 SKILL.md 的指示处理消息
5. 返回结果

### Skill 分类

| 类型 | 说明 | 例子 |
|------|------|------|
| 官方 Skill | OpenClaw 自带 | weather, github, tts |
| 社区 Skill | 从 ClawHub 安装 | 各种第三方技能 |
| 自定义 Skill | 自己写的 | 你的业务逻辑 |

---

## 8. 安装和使用 Skill

### 浏览可用 Skill

```bash
# 搜索 ClawHub 上的 Skill
openclaw skill search <关键词>
```

### 安装 Skill

```bash
openclaw skill install <skill-name>
```

### 查看已安装的 Skill

```bash
openclaw skill list
```

### 卸载 Skill

```bash
openclaw skill uninstall <skill-name>
```

### 常用官方 Skill 说明

| Skill | 用途 | 使用方式 |
|-------|------|---------|
| **weather** | 查天气 | "北京今天天气" |
| **github** | 操作 GitHub | "看看 XX 仓库的 issues" |
| **tts** | 语音合成 | "用语音读这段文字" |
| **frontend-design** | 做网页 | "帮我做一个数据看板" |
| **video-frames** | 提取视频帧 | "提取这个视频的关键帧" |
| **mimo-omni** | 图片/视频分析 | 发图片给我分析 |

---

## 9. 自己写一个 Skill

### 示例：写一个"翻译"Skill

**Step 1：创建目录**

```bash
mkdir -p ~/.openclaw/skills/translate
```

**Step 2：编写 SKILL.md**

```markdown
# Translate Skill

将用户发送的文本翻译成指定语言。

## 触发条件
当用户发送的内容包含"翻译"、"translate"、或类似关键词时激活。

## 使用方式
- "把这段话翻译成英文：你好世界"
- "translate to Japanese: 今天天气不错"
- "翻译成法语：我爱你"

## 处理流程
1. 提取要翻译的文本和目标语言
2. 直接输出翻译结果
3. 不要加多余的解释

## 支持的语言
中文、英文、日语、韩语、法语、德语、西班牙语
```

**Step 3：测试**

重启 Gateway 后，在群里说："把你好世界翻译成英文"，AI 应该会回复 "Hello World"。

### SKILL.md 写作技巧

1. **触发条件要明确** — AI 需要知道什么时候该用这个 Skill
2. **步骤要具体** — 不要写"好好回复"，要写"先做A，再做B"
3. **禁止行为也要写** — "不要编造"、"不要加多余解释"
4. **给例子** — 例子比规则更有说服力

---

## 10. 记忆系统

OpenClaw 有三层记忆：

### 短期记忆（对话上下文）

当前对话的历史消息，自动保持。重启后清空。

### 文件记忆（workspace 文件）

```
~/.openclaw/workspace/
├── SOUL.md        # AI 的人格和行为规则
├── USER.md        # 关于你的信息
├── MEMORY.md      # 长期记忆（手动维护）
└── memory/
    └── 2026-03-22.md  # 每日记录
```

**SOUL.md** — 告诉 AI 它是谁、怎么说话：
```markdown
# SOUL.md
你是一个技术助手，说话简洁直接。
不用客套话，直接给答案。
遇到不确定的问题说"我不确定"而不是瞎编。
```

**USER.md** — 告诉 AI 关于你的信息：
```markdown
# USER.md
- 名字：张三
- 时区：Asia/Shanghai
- 职业：后端开发
- 偏好：喜欢简洁的回答，不要废话
```

**MEMORY.md** — AI 的长期记忆，重要信息记在这里：
```markdown
# MEMORY.md
- 用户用的是阿里云服务器
- 主要项目是电商系统
- 偏好用 Python
```

### 向量记忆（memory-lancedb-pro）

自动将对话内容向量化存储，支持语义搜索。

---

## 11. 定时任务（Cron）

OpenClaw 支持定时执行任务。

### 配置方式

编辑 `openclaw.json`：

```json
{
  "cron": [
    {
      "label": "daily-weather",
      "schedule": "0 8 * * *",
      "task": "查一下北京今天天气，发到群里"
    },
    {
      "label": "weekly-report",
      "schedule": "0 18 * * 5",
      "task": "生成本周工作总结，发到群里"
    }
  ]
}
```

### Cron 表达式

```
分 时 日 月 周
0  9  *  *  *    → 每天9:00
0  */2 *  *  *   → 每2小时
0  9  *  *  1    → 每周一9:00
30 18 *  *  1-5  → 工作日18:30
```

### 常见定时任务示例

| 任务 | 表达式 |
|------|--------|
| 每天9点天气 | `0 9 * * *` |
| 每周一日报 | `0 9 * * 1` |
| 每30分钟检查邮件 | `*/30 * * * *` |
| 每天23点总结 | `0 23 * * *` |

---

## 12. 多模型配置

可以同时配置多个模型，随时切换。

### 配置示例

```json
{
  "models": {
    "providers": {
      "xiaomi": {
        "baseUrl": "https://api.xiaomimimo.com/v1",
        "apiKey": "sk-mimo-key",
        "api": "openai-completions",
        "models": [
          {"id": "mimo-v2-omni", "name": "MiMo-Omni"}
        ]
      },
      "dashscope": {
        "baseUrl": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "apiKey": "sk-qwen-key",
        "api": "openai-completions",
        "models": [
          {"id": "qwen3.5-plus", "name": "Qwen3.5-Plus"}
        ]
      }
    }
  }
}
```

### 切换模型

在聊天中发送：
```
/model mimo-v2-omni
/model qwen3.5-plus
```

### 按场景选模型

| 场景 | 推荐模型 | 原因 |
|------|---------|------|
| 日常问答 | MiMo-Omni | 免费、速度快 |
| 复杂推理 | MiMo-Pro / Claude | 准确率高 |
| 图片理解 | MiMo-V2-Omni | 多模态支持 |
| 代码生成 | Claude / Qwen-Coder | 编程能力强 |

---

## 13. 成本控制

### 查看用量

```
/session_status
```

### 省钱技巧

**1. 用免费模型**
MiMo-Omni 目前免费，日常用它就行。

**2. 关闭不需要的功能**
```json
{
  "plugins": {
    "memory-lancedb-pro": {
      "smartExtraction": false
    }
  }
}
```
`smartExtraction` 每次对话都会调用 LLM 做摘要，关掉能省不少。

**3. 群聊只在被 @ 时回复**
避免每条消息都触发 AI。

**4. 降低心跳频率**
```json
{
  "heartbeat": {
    "interval": 60
  }
}
```

**5. 工具返回截断**
```python
web_fetch(url, maxChars=3000)  # 不要无限制返回
```

### 成本参考

| 使用强度 | 月成本估算 |
|---------|-----------|
| 轻度（每天50条） | $0-5 |
| 中度（每天200条） | $5-20 |
| 重度（每天1000条） | $20-100 |

---

## 14. 常见问题排查

### Q: 机器人不回复

```bash
# 检查 Gateway 状态
openclaw gateway status

# 查看日志
openclaw logs

# 重启
openclaw gateway restart
```

### Q: 报错 "Invalid API Key"

- 检查 `openclaw.json` 中的 API Key 是否正确
- 确认 Key 没有过期
- 注意 Key 前后不要有空格

### Q: 飞书收不到消息

- 检查事件订阅配置是否正确
- 确认服务器防火墙开放了 18789 端口
- 检查 App 权限是否齐全

### Q: 回复很慢

- 可能是模型响应慢，换一个试试
- 检查服务器网络连接
- 减少系统提示的长度

### Q: 怎么看 Token 用量

在聊天中发送 `/status` 或 `/session_status`

---

## 15. 进阶玩法

### ACP 编程模式

让 AI 帮你写代码：

```
@机器人 用 codex 帮我写一个 Python 爬虫
```

### 子会话（Sub-agent）

AI 可以 spawn 子会话并行处理任务。

### 自定义插件

如果 Skill 不够用，可以写 TypeScript 插件实现更复杂的功能。

### 多渠道同时接入

同时接入飞书 + 微信 + Discord，一个 AI 管理所有渠道。

### 知识库（RAG）

把产品文档、FAQ 导入飞书知识库，AI 就能基于文档回答问题。

---

## 快速参考卡片

### 常用命令

```bash
openclaw --version        # 查看版本
openclaw init             # 初始化配置
openclaw gateway start    # 启动服务
openclaw gateway stop     # 停止服务
openclaw gateway restart  # 重启服务
openclaw status           # 查看状态
openclaw logs             # 查看日志
openclaw skill list       # 列出技能
openclaw skill install    # 安装技能
```

### 配置文件位置

```
~/.openclaw/openclaw.json   # 核心配置
~/.openclaw/workspace/      # 工作空间
```

### 帮助资源

- 官方文档：https://docs.openclaw.ai
- GitHub：https://github.com/openclaw/openclaw
- ClawHub 技能市场：https://clawhub.com
- Discord 社区：https://discord.com/invite/clawd

---

> 🦞 祝你玩得开心！遇到问题别慌，先看日志，日志会告诉你一切。
