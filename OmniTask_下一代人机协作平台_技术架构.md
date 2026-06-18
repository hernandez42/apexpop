# OmniTask — 下一代人机协作平台 技术架构文档

> 版本：v1.0 | 日期：2026-04-13
> 定位：超越滴滴、美团的通用智能协作网络

---

## 一、项目概述

### 1.1 背景与问题

当前主流平台（滴滴出行、美团外卖）的核心模式：

- **AI 只做调度**：算法仅负责匹配和派单，不参与执行
- **人是执行工具**：劳动者被当执行单元，没有成长和赋能
- **场景孤立**：滴滴只管出行，美团只管外卖，数据和能力无法复用
- **平台抽成模式不可持续**：两头压榨，劳动者和用户都缺乏归属感

### 1.2 核心理念转变

> **从「平台调度人」→「AI 与人共同组成任务单元」**

不再是 AI 派单给人，而是 AI + 人 组成一个协作体，共同完成任务。
AI 不是调度器，是协作者。

### 1.3 产品定位

**OmniTask = 通用智能协作网络**

一个平台覆盖出行、外卖、家政、维修、跑腿、远程协助等全场景。
底层是统一的任务描述语言和调度框架。

---

## 二、系统总体架构

### 2.1 三层架构总览

```
┌─────────────────────────────────────────────────────┐
│                   用户端 / 劳动者端                   │
│        (App / 小程序 / AR眼镜 / 语音设备)             │
└──────────────────────┬──────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────┐
│                   API Gateway                        │
│          认证 · 限流 · 路由 · 协议转换                │
└──────────────────────┬──────────────────────────────┘
                       │
         ┌─────────────┼─────────────┐
         ▼             ▼             ▼
  ┌─────────────┐ ┌─────────────┐ ┌─────────────┐
  │   感知层     │ │  协作调度层  │ │   进化层     │
  │  Perception │ │ Collaboration│ │  Evolution  │
  └──────┬──────┘ └──────┬──────┘ └──────┬──────┘
         │               │               │
         ▼               ▼               ▼
    多模态NLP        任务图引擎       经验回放池
    环境感知          技能匹配         策略优化
    意图理解          路径规划         模板沉淀
```

### 2.2 各层职责

- **感知层 (Perception Layer)**：理解用户需求，多模态输入处理，环境感知
- **协作调度层 (Collaboration Layer)**：任务拆解、人机编排、技能匹配、实时协作
- **进化层 (Evolution Layer)**：经验学习、策略优化、模板沉淀

---

## 三、感知层 — Perception Layer

### 3.1 多模态输入处理

**输入类型**：
- 语音（自然语言描述）
- 文字（聊天/表单）
- 图片（拍照上传）
- 视频（实时视频流）
- 传感器数据（位置、加速度等）

**处理流程**：

```
1. 多模态编码
   text_encoder(query_text)     → T    (文本语义向量)
   image_encoder(query_img)     → V    (视觉语义向量)
   audio_encoder(query_voice)   → A    (语音语义向量)
   fuse(T, V, A)               → U    (统一语义向量)

2. 意图分类
   intent = IntentClassifier(U)
   // 输出: {domain: "家政", intent: "深度清洁", urgency: "高"}

3. 需求澄清（如置信度不足）
   if confidence(intent) < threshold:
       ask_clarification_questions(intent, U)
```

### 3.2 环境感知

- **地理位置**：用户和劳动者的实时位置
- **天气状况**：影响配送/户外任务的时效
- **交通状态**：影响出行类任务的 ETA
- **人员分布**：区域内可用劳动者密度
- **设备状态**：劳动者端设备电量、网络状况

---

## 四、协作调度层 — Collaboration Layer

### 4.1 任务理解与拆解 (Task Decomposition Engine)

#### 核心数据结构 — 任务图 (TaskGraph)

```python
class TaskNode:
    id: str                          # 节点唯一标识
    description: str                 # 任务步骤描述
    capability_required: str         # 所需能力标签
    machine_capable: float           # AI 可独立完成度 0-1
    human_required: bool             # 是否必须人工介入
    estimated_duration: int          # 预估耗时(秒)
    dependencies: List[str]          # 前置依赖节点
    handoff_point: str               # 人机交接协议
    quality_checkpoints: List[str]   # 质量检查点

class TaskGraph:
    id: str
    root_intent: Intent              # 原始用户意图
    nodes: Dict[str, TaskNode]       # 节点集合
    edges: List[TaskEdge]            # 依赖关系
    estimated_total_time: int        # 预估总耗时
    orchestration_mode: str          # 编排模式
```

#### 任务拆解算法

```python
def decompose_task(intent: Intent, context: Context, kg: KnowledgeGraph) -> TaskGraph:
    """
    基于大模型 + 知识图谱的任务拆解
    """
    # 1. 从知识图谱检索类似历史任务
    similar_tasks = kg.query_similar(intent, top_k=10)
    
    # 2. LLM 生成初始拆解方案
    decomposition_prompt = build_prompt(intent, context, similar_tasks)
    raw_nodes = llm_generate(decomposition_prompt)
    
    # 3. 构建 DAG（有向无环图）
    task_graph = build_dag(raw_nodes)
    
    # 4. 标注人机分工
    for node in task_graph.nodes:
        node.machine_capable = estimate_ai_capability(node, kg)
        node.human_required = check_human_necessity(node)
    
    # 5. 优化执行顺序（拓扑排序 + 并行化检测）
    task_graph.optimize_order()
    
    return task_graph
```

#### 拆解示例

**用户请求**：「帮我装个热水器」

- **T0**: 信息收集（型号确认、安装条件）→ AI完成度 90%
- **T1**: 安装方案生成（管路设计、配件清单）→ AI完成度 80%
- **T2**: 配件采购 → AI完成度 100%
- **T3**: 现场安装（打孔、接管、调试）→ 人工完成度 100%
- **T4**: 安全检测 → AI+人工各 50%
- **T5**: 用户验收 → 人工完成度 100%

### 4.2 人机协作编排 (Human-AI Orchestration)

#### 协作模式枚举

- **AI-First**：AI 先做完，人只验收。适用：信息整理、方案生成
- **Human-First**：人先操作，AI 辅助纠错。适用：复杂维修、医疗辅助
- **Parallel**：人和 AI 同时做不同部分。适用：大型任务并行处理
- **Sequential**：严格先后交接。适用：安装类、物流类
- **Supervised**：AI 执行，人实时监督。适用：高风险操作

#### 协作分割优化算法

**目标**：找到 AI 和人的最优分工边界

```python
def optimize_orchestration(task_graph: TaskGraph) -> OrchestrationPlan:
    """
    最小化总成本，满足质量、时间、安全约束
    """
    # 决策变量：每个节点分配给 AI、人、或混合
    variables = {n.id: assign_var(n) for n in task_graph.nodes}
    
    # 目标函数
    minimize = sum(
        cost_ai(n) * variables[n.id].ai_ratio +
        cost_human(n) * variables[n.id].human_ratio
        for n in task_graph.nodes
    )
    
    # 约束条件
    constraints = [
        quality(n) >= threshold for n in task_graph.nodes,   # 质量达标
        total_time <= task_graph.deadline,                    # 时间不超
        human_safety(n) == True for n in task_graph.nodes,   # 安全
        handoff_latency(n) <= MAX_HANDOFF for n in task_graph.nodes,  # 交接延迟
    ]
    
    return solve_optimization(minimize, constraints)
```

#### 编排输出格式

```json
{
  "task_id": "t_20260413_001",
  "orchestration_mode": "Sequential",
  "pipeline": [
    {"node": "T0", "executor": "ai", "model": "gpt-4o", "timeout": 30, "fallback": "human"},
    {"node": "T1", "executor": "ai", "model": "gpt-4o", "timeout": 60, "fallback": "human"},
    {"node": "T2", "executor": "ai", "model": "agent", "timeout": 300, "fallback": "human"},
    {"node": "T3", "executor": "human", "skill": "水电安装/高级", "timeout": 3600, "ai_support": "AR安装指导"},
    {"node": "T4", "executor": "hybrid", "ai_role": "传感器检测", "human_role": "现场确认"},
    {"node": "T5", "executor": "human", "ai_role": "引导验收清单"}
  ],
  "handoff_protocols": {
    "T2→T3": "推送配件清单+安装指南到劳动者AR眼镜",
    "T3→T4": "AI读取安装传感器数据，人确认"
  }
}
```

### 4.3 技能匹配与调度 (Skill-Aware Matching)

#### 劳动者技能建模

```python
class WorkerProfile:
    id: str
    skill_vector: Dict[str, float]  # {技能标签: 熟练度 0-1}
    availability: TimeRange         # 可用时间段
    location: GeoPoint              # 实时位置
    reputation: float               # 综合信用分 0-1
    preference: Dict                # 偏好（不想接的类型等）
    collaboration_style: str        # 工作风格标签
    ai_assist_level: float          # 使用 AI 辅助的熟练度 0-1
    history: List[TaskRecord]       # 历史任务记录
```

#### 匹配算法 — 多目标优化

```python
def match_score(worker: WorkerProfile, task: TaskGraph) -> float:
    """
    多维度匹配评分
    """
    α, β, γ, δ, ε = 0.30, 0.20, 0.20, 0.15, 0.15  # 权重
    
    return (
        α * skill_match(worker.skill_vector, task.requirements)   # 技能匹配
      + β * proximity(worker.location, task.location)             # 地理距离
      + γ * reputation(worker.reputation)                         # 信用分
      + δ * availability(worker, task.deadline)                   # 时间可用
      + ε * ai_synergy(worker.ai_assist_level, task.ai_available) # AI协作能力
    )
```

**关键创新项 `ai_synergy`**：
- 有些劳动者善于和 AI 配合（会用 AR 眼镜、信任 AI 建议）
- 这类人在 AI 协作平台上效率更高
- 匹配时优先选「人机协作能力强」的劳动者

#### 完整匹配流程

```python
def match_workers(task: TaskGraph, candidates: List[WorkerProfile]) -> List[WorkerProfile]:
    """
    三阶段匹配：粗筛 → 精排 → 多样化
    """
    # 阶段1：粗筛 — 技能覆盖 + 时间可用 + 地理合理
    filtered = [
        w for w in candidates
        if has_skill_coverage(w, task)
        and is_available(w, task)
        and in_range(w, task)
    ]
    
    # 阶段2：精排 — 多目标打分
    scored = [(w, match_score(w, task)) for w in filtered]
    scored.sort(key=lambda x: x[1], reverse=True)
    
    # 阶段3：多样性保证 — 避免同质化推荐
    top_k = diversity_rerank(scored, k=5)
    
    return top_k
```

### 4.4 实时协作引擎 (Real-time Collaboration Engine)

#### 通信架构

```
┌──────────────┐    WebSocket/gRPC    ┌──────────────────┐
│   用户 App   │◄──────────────────►│    协作引擎       │
└──────────────┘                     └────────┬─────────┘
                                              │
                  ┌───────────────────────────┤
                  ▼                           ▼
          ┌──────────────┐          ┌──────────────────┐
          │  劳动者端     │          │   AI Agent       │
          │ (AR/语音/APP)│          │  (多模型集群)    │
          └──────────────┘          └──────────────────┘
```

#### 状态机设计

```
┌────────┐  匹配成功  ┌──────────┐  AI预处理  ┌──────────┐
│PENDING │─────────►│ MATCHING │─────────►│ AI_PREP  │
└────────┘          └──────────┘          └────┬─────┘
                                               │
                                        人加入  ▼
                                      ┌──────────────┐
                                      │HUMAN_JOINED  │
                                      └──────┬───────┘
                                             │
                                             ▼
                                      ┌──────────────┐
                                      │ IN_PROGRESS  │◄────┐
                                      └──────┬───────┘     │
                                             │             │
                                        交接  ▼             │
                                      ┌──────────────┐     │
                                      │   HANDOFF    │─────┘
                                      └──────┬───────┘
                                             │
                                             ▼
                                      ┌──────────────┐
                                      │  QA_CHECK    │
                                      └──────┬───────┘
                                             │
                                        通过  ▼
                                      ┌──────────────┐
                                      │  COMPLETED   │
                                      └──────────────┘

任何状态均可转换至 → ┌──────────┐
                    │ESCALATED │（异常升级）
                    └──────────┘
```

#### 事件协议

```python
class CollaborationEvent:
    event_id: str
    task_id: str
    timestamp: float
    event_type: str          # STATE_CHANGE | DATA_UPDATE | HANDOFF | ALERT
    source: str              # "ai" | "human" | "system"
    payload: Dict
    requires_ack: bool       # 是否需要确认

# 事件类型定义
EVENT_TYPES = {
    "STATE_CHANGE":     "状态变更通知",
    "DATA_UPDATE":      "数据更新（位置、进度等）",
    "HANDOFF_REQUEST":  "人机交接请求",
    "HANDOFF_COMPLETE": "人机交接完成",
    "QUALITY_ALERT":    "质量异常告警",
    "ESCALATION":       "异常升级请求",
    "AI_SUGGESTION":    "AI 建议推送",
    "HUMAN_FEEDBACK":   "人工反馈",
}
```

---

## 五、进化层 — Evolution Layer

### 5.1 经验回放池 (Experience Replay Pool)

```python
class Experience:
    task_type: str
    task_graph: TaskGraph              # 原始任务图（计划）
    actual_execution: ExecutionLog     # 实际执行记录
    ai_performance: Metrics            # AI 各节点表现
    human_performance: Metrics         # 人各节点表现
    handoff_efficiency: float          # 交接效率 0-1
    outcome: Outcome                   # 成功 / 失败 / 部分成功
    feedback: List[Feedback]           # 双方反馈
    duration_actual: int               # 实际耗时
    duration_planned: int              # 计划耗时

class Metrics:
    accuracy: float                    # 准确率
    completion_rate: float             # 完成率
    error_count: int                   # 错误次数
    retry_count: int                   # 重试次数
    user_satisfaction: float           # 用户满意度
```

### 5.2 策略优化算法

```python
def optimize_strategy(experiences: List[Experience]) -> List[Template]:
    """
    从历史经验中学习，优化平台策略
    """
    # 1. 发现瓶颈 — 哪些节点频繁出问题
    bottleneck_nodes = analyze_failures(experiences)
    
    # 2. 优化人机分割点
    for node in bottleneck_nodes:
        if node.human_required and ai_improvement_possible(node):
            new_threshold = adjust_machine_capable(node, experiences)
            node.machine_capable = new_threshold
    
    # 3. 生成新的协作模板
    successful_patterns = filter_successful(experiences)
    templates = cluster_and_abstract(successful_patterns)
    
    # 4. 更新匹配权重（强化学习）
    update_matching_weights(experiences)
    
    # 5. 更新知识图谱
    update_knowledge_graph(experiences)
    
    return templates
```

### 5.3 协作模板系统

```json
{
  "template_id": "tpl_home_install_waterheater_v3",
  "derived_from": 127,
  "success_rate": 0.94,
  "avg_duration": 4200,
  "best_match_profile": {
    "skill": {"水电安装": 0.8, "燃气设备": 0.6},
    "min_ai_synergy": 0.5,
    "experience_years": 2
  },
  "optimized_pipeline": "T0→T1→T2→T3→T4→T5",
  "known_pitfalls": [
    "T3 需确认墙体材质（混凝土/砖墙/轻质墙）",
    "T4 必须检测气密性，不可跳过"
  ],
  "ai_support_assets": {
    "T0": "户型识别模型",
    "T1": "管路设计助手",
    "T3": "AR安装指导3D模型",
    "T4": "传感器检测脚本"
  }
}
```

---

## 六、数据模型

### 6.1 核心实体关系

- **User (用户)** → 创建 TaskRequest，给出 Review，收到 Invoice
- **Worker (劳动者)** → 拥有 SkillProfile，执行 TaskAssignment，获得 Reward，积累 Reputation
- **TaskRequest (任务请求)** → 分解为 TaskGraph，分配到 TaskAssignment，产生 Experience
- **TaskGraph (任务图)** → 包含 TaskNode[]，遵循 OrchestrationPlan，记录 ExecutionLog
- **Experience (经验)** → 喂入 Template，更新 SkillProfile，优化 MatchingWeights

### 6.2 关键数据库设计

```sql
-- 任务表
CREATE TABLE tasks (
    id           UUID PRIMARY KEY,
    user_id      UUID NOT NULL,
    domain       VARCHAR(50),          -- 场景分类
    intent       JSONB,                -- 意图结构化数据
    status       VARCHAR(20),          -- 状态机当前状态
    graph        JSONB,                -- TaskGraph 序列化
    orchestration JSONB,               -- 编排方案
    worker_id    UUID,                 -- 匹配的劳动者
    created_at   TIMESTAMP,
    completed_at TIMESTAMP,
    outcome      VARCHAR(20),
    feedback     JSONB
);

-- 劳动者技能表
CREATE TABLE worker_skills (
    worker_id    UUID REFERENCES workers(id),
    skill_tag    VARCHAR(100),
    proficiency  FLOAT CHECK (proficiency BETWEEN 0 AND 1),
    ai_synergy   FLOAT CHECK (ai_synergy BETWEEN 0 AND 1),
    updated_at   TIMESTAMP,
    PRIMARY KEY (worker_id, skill_tag)
);

-- 经验回放表
CREATE TABLE experiences (
    id           UUID PRIMARY KEY,
    task_type    VARCHAR(100),
    task_graph   JSONB,
    execution    JSONB,
    metrics      JSONB,
    outcome      VARCHAR(20),
    template_id  UUID,
    created_at   TIMESTAMP
);

-- 协作模板表
CREATE TABLE templates (
    id           UUID PRIMARY KEY,
    task_type    VARCHAR(100),
    version      INT,
    graph_schema JSONB,
    success_rate FLOAT,
    usage_count  INT,
    derived_from INT,                  -- 基于多少个经验生成
    created_at   TIMESTAMP,
    updated_at   TIMESTAMP
);
```

---

## 七、关键技术选型

### 7.1 技术栈

- **多模态理解**：GPT-4o / Gemini 2.0 / 自研 VLM
- **语音识别**：Whisper V3 / 自研 ASR
- **图像理解**：GPT-4V / InternVL
- **任务图引擎**：自研 DAG + Temporal (工作流编排)
- **实时通信**：WebSocket + WebRTC (音视频)
- **匹配系统**：向量检索(Milvus) + 运筹优化(OR-Tools)
- **状态管理**：Redis (实时) + PostgreSQL (持久化)
- **经验存储**：向量数据库(Milvus) + 关系数据库
- **策略优化**：PyTorch + 自研 RL 框架
- **容器编排**：Kubernetes
- **边缘计算**：Cloudflare Workers / 阿里云边缘节点
- **消息队列**：Apache Kafka
- **监控**：Prometheus + Grafana

### 7.2 部署架构

```
                    ┌─────────────────────┐
                    │   CDN / 边缘节点     │
                    │  (静态资源+WebSocket) │
                    └──────────┬──────────┘
                               │
                    ┌──────────▼──────────┐
                    │   API Gateway       │
                    │  (Kong / Envoy)     │
                    └──────────┬──────────┘
                               │
              ┌────────────────┼────────────────┐
              ▼                ▼                ▼
     ┌──────────────┐ ┌──────────────┐ ┌──────────────┐
     │  感知服务     │ │  协作服务     │ │  进化服务     │
     │  (GPU节点)   │ │  (CPU节点)   │ │  (GPU节点)   │
     └──────────────┘ └──────────────┘ └──────────────┘
              │                │                │
              └────────────────┼────────────────┘
                               ▼
                    ┌─────────────────────┐
                    │   数据层             │
                    │ PostgreSQL + Redis   │
                    │ Milvus + Kafka      │
                    └─────────────────────┘
```

---

## 八、扩展场景

### 8.1 场景矩阵

- **同城出行**：AI 路线规划 + 动态定价，人力驾驶。场景：打车、代驾、包车
- **即时配送**：AI 路径优化 + ETA预测，人力取送。场景：外卖、快递、跑腿
- **家政维修**：AI 方案生成 + AR指导，人力技术操作。场景：安装、维修、清洁
- **医疗陪诊**：AI 信息整理 + 排队预约，人力陪同照顾。场景：陪诊、取药、护理
- **企业协作**：AI 文档处理 + 数据分析，人力创意决策。场景：项目外包、众包
- **远程技术**：AI 代码生成 + Debug，人力架构决策。场景：编程、运维、设计

### 8.2 新场景接入流程

1. 定义场景的 TaskNode 类型库
2. 标注 AI 可完成度和所需技能
3. 积累种子模板（人工执行 50-100 单）
4. AI 学习协作模式，生成初始模板
5. 进入进化循环，持续优化

---

## 九、与现有平台对比

- **场景覆盖**：滴滴/美团 → 单一场景 | OmniTask → 通用多场景
- **AI 角色**：滴滴/美团 → 调度器 | OmniTask → 协作者
- **匹配逻辑**：滴滴/美团 → 就近派单 | OmniTask → 技能+协作能力匹配
- **劳动者角色**：滴滴/美团 → 执行工具 | OmniTask → 协作伙伴
- **学习能力**：滴滴/美团 → 有限（各自场景内）| OmniTask → 跨场景经验迁移
- **进化速度**：滴滴/美团 → 依赖人工调参 | OmniTask → 自动策略优化
- **治理模式**：滴滴/美团 → 平台中心化 | OmniTask → 去中心化治理
- **收入模式**：滴滴/美团 → 抽成 | OmniTask → 透明分成+技能溢价

---

## 十、技术可行性评估

- **多模态任务理解**：✅ 已具备（GPT-4o等）— 现在
- **任务自动拆解**：✅ 已具备（LLM）— 现在
- **实时协作通信**：✅ 基础已有 — 现在
- **技能图谱构建**：⏳ 需大量领域数据 — 1-2 年
- **人机协作模板学习**：⏳ 需要实践数据积累 — 2-3 年
- **跨场景经验迁移**：🔬 研究阶段 — 3-5 年
- **去中心化治理**：⏳ 技术成熟，监管待跟进 — 不确定

---

## 十一、实施路线图

### Phase 1：MVP（0-6个月）
- 选择单一场景（如家政维修）验证核心架构
- 实现基础任务拆解 + 人工匹配
- 建立经验回放池雏形

### Phase 2：智能化（6-18个月）
- 上线 AI 自动匹配
- 实现协作模板自动生成
- 扩展至 2-3 个场景

### Phase 3：通用化（18-36个月）
- 统一任务描述语言成熟
- 跨场景经验迁移可用
- 开放平台 API，第三方接入

### Phase 4：生态化（36个月+）
- 去中心化治理上线
- 劳动者自治社区
- 全场景覆盖

---

## 十二、总结

OmniTask 的核心创新：

1. **AI 从调度器升级为协作者** — 不只派单，而是参与任务执行
2. **通用任务描述语言** — 一套框架覆盖全场景
3. **技能银行 + 人机协作能力匹配** — 找最合适的「人+AI 组合」
4. **经验回放 + 策略优化** — 平台越用越聪明
5. **协作模板沉淀** — 成功经验可复用、可进化

**核心判断**：下一代平台的竞争不是运力竞争，是**智能协作密度**的竞争。
谁能让 AI 和人配合得更紧密、更高效，谁就赢。

---

*文档结束*
