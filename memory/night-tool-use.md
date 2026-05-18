# Agent 工具使用（Tool Use）与函数调用（Function Calling）最新开源项目

> 搜索日期：2026-05-11 | 来源：GitHub、arXiv、智源社区
> 重点关注：1) 工具选择和路由 2) 多工具编排 3) 工具自动生成 4) MCP 协议生态

---

## 1. AutoTool — 基于图结构的高效工具选择框架

- **论文**: [AutoTool: Efficient Tool Selection for Large Language Model Agents](https://arxiv.org/abs/2511.14650) (AAAI 2026)
- **GitHub**: https://github.com/jiajingyyyyyy/AutoTool
- **核心方法**:
  - 发现「工具使用惯性」(Tool Usage Inertia)：工具调用遵循可预测的序列模式
  - 从历史 Agent 轨迹构建有向图，节点=工具，边=转移概率
  - 集成参数级信息细化工具输入生成
  - 通过图遍历高效选择工具，大幅减少 LLM 推理次数
- **对 Agent 能力的提升**:
  - 推理成本降低 **30%**，同时保持竞争力的任务完成率
  - 解决 ReAct 等框架中每步都调 LLM 选工具的高推理开销问题
  - 将统计结构融入 Agent 设计，实现效率与性能的平衡
- **分类**: 🔀 工具选择与路由

---

## 2. Tool-to-Agent Retrieval — 面向可扩展多 Agent 系统的工具检索框架

- **论文**: [Tool-to-Agent Retrieval: Bridging Tools and Agents for Scalable LLM Multi-Agent Systems](https://arxiv.org/abs/2511.01854) (2025.11)
- **核心方法**:
  - 将工具和父级 Agent 嵌入共享向量空间，通过元数据关系连接
  - 显式表示工具能力，支持工具级和 Agent 级检索
  - 避免将大量工具打包导致的上下文稀释问题
  - 在 8 种 embedding 模型上评估，Recall@5 提升 19.4%，nDCG@5 提升 17.7%
- **对 Agent 能力的提升**:
  - 解决多 Agent 系统中数百/数千工具的可扩展检索问题
  - 原生支持 MCP Server 的检索和路由
  - 精细粒度的工具级检索，替代粗粒度 Agent 级匹配
- **分类**: 🔀 工具选择与路由 + 🔗 MCP 协议生态

---

## 3. Composio — 生产级 Agent 工具集成平台

- **GitHub**: https://github.com/ComposioHQ/composio (12K+ stars)
- **文档**: https://docs.composio.dev
- **核心方法**:
  - 提供 **1000+ 工具包** (GitHub, Slack, Gmail, Jira, Linear 等)
  - 内置工具搜索 (Tool Search)：从海量工具中自动匹配最相关的工具
  - 统一认证管理：OAuth、API Key 等一次性配置
  - 沙箱工作台：安全执行工具调用
  - 支持 Python/TypeScript SDK，无缝对接 OpenAI Agents、LangChain、CrewAI 等
- **对 Agent 能力的提升**:
  - 将「意图」转化为「行动」的完整工具基础设施
  - 消除 Agent 开发者逐个对接 API 的重复工作
  - 支持用户级工具权限隔离
- **分类**: 🔗 MCP 协议生态 + 🔀 工具选择与路由

---

## 4. ToolFactory — 从 REST API 文档自动生成 AI 工具

- **论文**: [ToolFactory: Automating Tool Generation by Leveraging LLM to Understand REST API Documentations](https://arxiv.org/abs/2501.16945) (2025.01)
- **核心方法**:
  - 开源流水线：从非结构化 API 文档自动生成 AI 兼容工具
  - 诊断评估方法：检测生成工具中的错误
  - 已验证工具知识库：推断文档不完整的 API 缺失信息
  - API Extraction Benchmark：167 个 API 文档、744 个端点的基准数据集
  - 已构建糖材料研究领域的领域专用 AI Agent 作为演示
- **对 Agent 能力的提升**:
  - 大幅降低工具开发门槛，无需手动编写 Tool Schema
  - 处理不规范、不完整的现实世界 API 文档
  - 可扩展到科学 REST API 的自动化集成
- **分类**: 🛠 工具自动生成

---

## 5. OpenHands Software Agent SDK — 可组合的生产级软件工程 Agent 工具包

- **论文**: [The OpenHands Software Agent SDK: A Composable and Extensible Foundation for Production Agents](https://arxiv.org/abs/2511.03690) (2025.11)
- **GitHub**: https://github.com/All-Hands-AI/OpenHands (64K+ stars)
- **核心方法**:
  - 完全重新设计的 Agent 架构，几行代码即可实现基础 Agent
  - 可扩展到复杂全功能 Agent：自定义工具、记忆管理等
  - 无缝的本地到远程执行可移植性
  - 集成 REST/WebSocket 服务
  - 多种用户交互接口：VSCode、VNC、浏览器、CLI、API
  - 相比 OpenAI/Claude/Google SDK，独有的灵活工具扩展能力
- **对 Agent 能力的提升**:
  - 为生产环境软件工程 Agent 提供可组合基础
  - 安全可靠的执行环境（沙箱隔离）
  - 开发者可用最少代码快速搭建工具增强型 Agent
- **分类**: 🛠 工具自动生成 + 🔀 多工具编排

---

## 总结对比

| 项目 | 工具选择/路由 | 多工具编排 | 工具自动生成 | MCP 生态 | 成熟度 |
|------|:---:|:---:|:---:|:---:|:---:|
| AutoTool | ✅ | ⚠️ | ❌ | ❌ | 论文+代码 |
| Tool-to-Agent Retrieval | ✅ | ✅ | ❌ | ✅ | 论文 |
| Composio | ✅ | ✅ | ❌ | ✅ | 生产级 |
| ToolFactory | ❌ | ❌ | ✅ | ❌ | 论文+代码 |
| OpenHands SDK | ⚠️ | ✅ | ✅ | ⚠️ | 生产级 |

## 趋势洞察

1. **工具选择从「逐次推理」走向「图/检索预计算」**：AutoTool 和 Tool-to-Agent Retrieval 都在减少 LLM 在工具选择上的推理开销
2. **MCP 成为事实标准**：Tool-to-Agent Retrieval 和 Composio 都原生支持 MCP，MCP Servers 生态已超 21K stars
3. **工具自动生成是下一个前沿**：ToolFactory 证明从非结构化文档自动生成工具可行，降低 Agent 能力扩展的边际成本
4. **生产级 SDK 竞争激烈**：OpenHands 64K stars，Composio 12K+，正在成为 Agent 工具基础设施的主流选择
