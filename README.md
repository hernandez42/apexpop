# superclaw

轻量级 AI Agent 框架 — 支持 Python 工具调用、GEP 进化循环与飞书集成。

**版本**: 2.4.0

## 安装

```bash
pip install -e .
pip install -e ".[feishu]"   # 包含飞书渠道支持
```

## 快速开始

```bash
# 交互式对话
python3 -m superclaw

# 单次问答
python3 -m superclaw run "你好，解释一下什么是 GEP 进化循环"

# 列出可用 LLM Provider
python3 -m superclaw --providers

# 指定 Provider 和模型
python3 -m superclaw --provider deepseek --model deepseek-chat

# 运行进化调度器（每小时触发一次）
python3 -m superclaw --schedule --interval 3600
```

## 配置

默认配置优先级：`环境变量 > config.json > 默认值`。

### config.json 示例

```json
{
    "llm": {
        "provider": "deepseek",
        "model": "deepseek-chat",
        "api_key": "sk-...",
        "base_url": "https://api.deepseek.com/chat/completions",
        "temperature": 0.7,
        "max_tokens": 2048
    },
    "session": {
        "max_messages": 50,
        "path": "~/.superclaw/sessions"
    },
    "tools": {
        "shell": true,
        "file": true,
        "web": false,
        "think": true,
        "max_tool_iterations": 5
    },
    "workspace": "."
}
```

### 环境变量

| 变量 | 说明 |
|------|------|
| `SUPERCLAW_PROVIDER` | LLM Provider 名称 |
| `SUPERCLAW_MODEL` | 模型名称 |
| `DEEPSEEK_API_KEY` / `GROQ_API_KEY` / ... | 各 Provider 的 API Key |
| `DEEPSEEK_BASE_URL` / `GROQ_BASE_URL` / ... | 自定义 API 端点 |

### 飞书集成

启用飞书渠道（在 config.json 中）：

```json
{
    "channels": {
        "feishu": {
            "enabled": true,
            "app_id": "cli_xxxx",
            "app_secret": "xxxx",
            "allow_from": ["*"],
            "streaming": false
        }
    }
}
```

详细搭建步骤见 [FEISHU.md](FEISHU.md)。

## 架构概览

```
superclaw/
├── agent.py           # Agent 循环（LLM → 工具 → LLM）
├── providers.py        # LLM Provider 抽象层
├── llm_router.py       # 多 Provider 自动路由 + 故障转移
├── tools.py           # 工具注册表
├── dynamic_loader.py  # 运行时动态加载 Python 工具
├── scheduler.py       # 定时进化调度器
├── gep_engine.py      # GEP 10 步进化循环
├── capability_registry.py  # 能力注册表
├── curiosity.py       # 好奇心驱动探索
├── feedback_learner.py    # 用户反馈学习
├── experience_learner.py  # 经验驱动调整
├── evolution_validator.py # Git Snapshot 验证
├── memory.py          # 记忆系统（经验 + 知识索引）
├── github_tools.py    # GitHub 代码搜索 / 依赖安装
└── channels/          # 消息渠道（Console / 飞书）
```

## 进化调度模式

| 模式 | 说明 |
|------|------|
| `cycle` | 基础 10 步 GEP 进化循环 |
| `self` | 自进化（感知短板 → 获取能力 → 验证） |
| `curious` | 好奇心驱动的探索 |
| `experience` | 经验驱动的权重调整 |
| `feedback` | 用户反馈驱动的进化 |
| `multi` | 多模式轮转 |

## 开发

```bash
# 运行测试
pytest tests/ -v

# 类型检查
mypy superclaw/ --ignore-missing-imports

# 代码风格
pyflakes superclaw/
bandit -r superclaw/ -f txt
```
