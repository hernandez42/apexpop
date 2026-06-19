# 飞书（Feishu / Lark）集成指南

本文档说明如何将 superclaw 与飞书消息渠道连接，实现通过飞书机器人与 Agent 对话。

## 前置要求

- Python >= 3.10
- 已安装 superclaw：`pip install -e ".[feishu]"`
- 拥有飞书企业账号，并具有创建企业应用的权限

## 步骤一：创建飞书应用

1. 访问 [飞书开放平台](https://open.feishu.cn/app) 并登录
2. 点击「创建企业自建应用」
3. 填写应用名称（如 `superclaw`）和描述，点击创建
4. 在「凭证与基础信息」页面获取：
   - **App ID**（格式：`cli_xxxxxxxx`）
   - **App Secret**

> **安全提示**：App Secret 等同于应用密码，请勿泄露或提交到代码仓库。

## 步骤二：配置机器人

1. 在应用详情页左侧菜单选择「添加应用能力」
2. 找到「机器人」能力，点击启用
3. 在「机器人」配置页面，确认以下选项：
   - **消息接收权限**：已开启
   - **使用长连接接收消息**：已开启（推荐）

## 步骤三：配置权限

1. 在左侧菜单选择「权限管理」
2. 添加以下权限（按名称搜索）：

| 权限名称 | 权限标识 | 用途 |
|----------|----------|------|
| 获取与发送单聊消息 | `im:message:send_as_bot` | 发送消息 |
| 获取用户发给机器人的单聊消息 | `im:message` | 接收消息 |

3. 点击「发布」申请权限（如企业开启了权限审批，需要管理员同意）

## 步骤四：在 superclaw 中配置

创建或编辑 `config.json`：

```json
{
    "channels": {
        "feishu": {
            "enabled": true,
            "app_id": "cli_xxxxxxxx",       // 替换为你的 App ID
            "app_secret": "xxxxxxxx",        // 替换为你的 App Secret
            "allow_from": ["*"],
            "streaming": false
        }
    },
    "llm": {
        "provider": "deepseek",
        "model": "deepseek-chat",
        "api_key": "sk-..."
    }
}
```

**安全建议**：将 `app_secret` 放在环境变量中，通过 `config.json` 引用：

```json
{
    "channels": {
        "feishu": {
            "enabled": true,
            "app_id": "cli_xxxxxxxx",
            "app_secret": "${FEISHU_APP_SECRET}",
            "allow_from": ["*"]
        }
    }
}
```

然后在启动前设置环境变量：
```bash
export FEISHU_APP_SECRET="your-secret-here"
python3 -m superclaw
```

## 步骤五：启动

```bash
# 启用飞书渠道（需先设置环境变量 FEISHU_APP_SECRET）
python3 -m superclaw

# 验证启动日志中包含：
# [Feishu] ✅ 飞书渠道启动 (app_id=cli_xxxx****)
```

## 本地开发测试

飞书长连接模式支持本地开发，无需公网地址。

在「应用发布」→「测试版本」中添加测试人员后，可直接向机器人发送消息进行测试。

## 常见问题

**Q: 启动时报 `lark-oapi 未安装`**
```bash
pip install lark-oapi>=1.0
```

**Q: 机器人无法接收消息**
检查应用是否已发布（测试版本或正式版本），以及是否添加了「接收消息」权限。

**Q: 发送消息失败 `tenant_access_token`**
确认 App ID 和 App Secret 填写正确，且应用具备发送消息的权限。

## 架构说明

```
飞书服务器  →  WebSocket 长连接  →  FeishuChannel.start()
                                          ↓
                                    MessageBus
                                          ↓
                                    Agent.run()
                                          ↓
FeishuChannel.send()  ←  OutboundMessage  ←  Agent 响应
         ↓
   飞书 IM API
```

飞书渠道使用 `tenant_access_token` 进行身份验证，Token 会自动缓存在实例变量中，避免频繁刷新。
