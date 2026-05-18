# 真实消化报告 — 2026-05-17

> 消化引擎 v1.0 | 不是记名字，是拆解机制
> 消化对象：LangChain (Runnable 抽象) + Mem0 (Memory Layer)

---

## 一、LangChain Runnable 模式

### 核心设计模式：可组合的计算单元

**本质**：LangChain 不是"LLM 工具箱"，它定义了一种**计算单元的接口标准**。所有组件（LLM、Chain、Tool、Retriever）都实现同一个接口 `Runnable[Input, Output]`，因此可以无损组合。

**关键设计决策**：

1. **泛型约束 `Runnable[Input, Output]`** — 每个组件有明确的输入输出类型，组合时类型自动推导
2. **四态接口** — `invoke`(同步单次) / `batch`(并行批量) / `stream`(流式) / `ainvoke`(异步)，一个实现自动获得四种能力
3. **`|` 管道操作符** — `RunnableSequence` 让组合变成声明式的：`prompt | llm | parser`
4. **RunnableParallel** — 用 dict 字面量声明并行分支：`{"mul_2": ..., "mul_5": ...}`
5. **Config 注入** — 所有方法接受 `RunnableConfig`，包含 callbacks/tags/metadata/可配置字段

### 关键代码片段

```python
# 核心接口 — 所有 LangChain 组件的基因
class Runnable(ABC, Generic[Input, Output]):
    """A unit of work that can be invoked, batched, streamed, transformed and composed."""
    
    # 四态接口（只需实现 invoke，batch/stream 自动获得）
    @abstractmethod
    def invoke(self, input: Input, config: RunnableConfig | None = None) -> Output:
        """Transforms a single input into an output."""
    
    # batch 默认用线程池并行执行 invoke
    def batch(self, inputs: list[Input], ...):
        """默认实现：用 ThreadPoolExecutor 并行跑 invoke"""
    
    # 管道操作符
    def __or__(self, other: Runnable) -> RunnableSequence:
        return RunnableSequence(self, other)
    
    # 附着式 API（不修改自身，返回新 Runnable）
    def with_retry(self, stop_after_attempt=1, ...):
    def with_fallbacks(self, fallbacks: list[Runnable]):
    def configurable_fields(self, *specs: ConfigurableFieldSpec):
```

```python
# BaseLanguageModel 继承链 — 类型系统的威力
class BaseLanguageModel(RunnableSerializable[LanguageModelInput, LanguageModelOutputVar], ABC):
    """所有 LLM 包装器继承这个"""
    # LLM 既是 Runnable（可组合），又有独立接口（generate_prompt）
    # 这意味着：同一个 LLM 实例既可以管道组合，也可以独立调用
```

### 对我们有什么用

**1. 统一技能接口**

我们有 470 个 skill，每个都是独立的。如果用 Runnable 模式重定义技能接口：

```python
class Skill(ABC, Generic[Input, Output]):
    """所有技能的基因"""
    def execute(self, input: Input, config: Config = None) -> Output: ...
    
    # 可组合
    skill_a | skill_b  # 自动串联
    skill_a.batch([input1, input2])  # 自动并行
    skill_a.stream(input)  # 流式输出
```

**应用**：技能不再是"空壳描述+手动调用"，而是可以自动组合的计算单元。

**2. 配置注入机制**

LangChain 的 `configurable_fields` 允许运行时切换组件行为，不需要修改代码：

```python
# 运行时切换 LLM 提供商
model = init_chat_model("openai:gpt-5.4").configurable_alternatives(
    ConfigurableField(id="llm_provider"),
    default_key="openai",
    openai=init_chat_model("openai:gpt-5.4"),
    anthropic=init_chat_model("anthropic:claude-4"),
)
```

**应用**：我们的技能可以接受运行时配置，比如"切换分析模型""切换数据源"。

**3. Callbacks 链路**

所有操作都可注入 callbacks（tracing、logging、metrics），不需要修改组件内部。

**应用**：我们的审计子代理可以通过 callback 机制透明地监控每个操作，不需要侵入式修改。

---

## 二、Mem0 Memory Layer

### 核心设计模式：记忆是一等公民

**本质**：Mem0 把"记忆"从"附属于对话的元数据"提升为"独立的、有生命周期的数据实体"。记忆有自己的存储、检索、更新、删除逻辑。

### 五大机制拆解

#### 机制 1：Additive Extraction（只增不改）

**设计**：每次对话只提取新事实（ADD-only），不更新也不删除旧记忆。记忆自然累积。

```python
# 核心理念
# 旧版：UPDATE/DELETE 语义 — 复杂、容易丢信息
# 新版：ADD-only — 一次 LLM 调用，提取 fact，直接存入
# 效果：LoCoMo 71.4 → 91.6，LongMemEval 67.8 → 94.8
```

**为什么有效**：
- 避免了复杂的冲突检测逻辑
- 旧记忆通过检索时的 **temporal reasoning** 自动失效（问当前状态时，最新的事实权重更高）
- Agent 确认的行为 = 第一等公民事实（agent-generated facts are first-class）

**对我们有什么用**：我们的 `memory_store` 目前是覆盖式的。如果改为 additive：
- 每次对话提取关键事实 → 直接 add
- 检索时用时间加权排序（最新的优先）
- 不需要"记忆更新"逻辑，复杂度降维

#### 机制 2：Multi-signal Retrieval（多信号融合检索）

**设计**：三路并行检索 + 自适应融合：

```python
def score_and_rank(semantic_results, bm25_scores, entity_boosts, threshold, top_k):
    """
    三路信号融合：
    - semantic: 向量相似度（0-1）
    - bm25: 关键词匹配（sigmoid 归一化到 0-1）
    - entity_boost: 实体匹配加成（固定 0.5）
    
    combined = (semantic + bm25 + entity_boost) / max_possible
    max_possible 根据哪些信号活跃自适应：1.0 → 1.5 → 2.0 → 2.5
    """
    for result in semantic_results:
        if semantic_score < threshold:
            continue  # 语义分数是门槛，BM25/entity 只能加分不能救命
        combined = min(raw_combined / max_possible, 1.0)
```

**关键洞察**：
- **语义分数是门槛**：候选必须先通过语义阈值，BM25 和 entity 只能锦上添花
- **BM25 用 sigmoid 归一化**：原始 BM25 分数无界（0-20+），sigmoid 映射到 [0,1]
- **sigmoid 参数自适应查询长度**：短查询（≤3词）midpoint=5,steepness=0.7；长查询（>15词）midpoint=12,steepness=0.5

```python
def normalize_bm25(raw_score, midpoint, steepness):
    return 1.0 / (1.0 + math.exp(-steepness * (raw_score - midpoint)))

def get_bm25_params(query):
    num_terms = len(query.split())
    if num_terms <= 3: return 5.0, 0.7
    elif num_terms <= 6: return 7.0, 0.6
    elif num_terms <= 9: return 9.0, 0.5
    else: return 12.0, 0.5
```

**对我们有什么用**：
- 我们的 memory_recall 目前是纯向量检索。如果加 BM25 + entity boost，召回率会大幅提升
- sigmoid 归一化公式可以直接用
- 自适应参数的思路：短查询偏严格，长查询偏宽松

#### 机制 3：Entity Extraction（NLP 实体提取）

**设计**：用 spaCy 做四类实体提取，不是简单的 NER：

```python
# 四类实体
# 1. PROPER — 专有名词序列（人名、地名、品牌）
# 2. QUOTED — 引号内的文本（标题、术语）
# 3. COMPOUND — 名词复合短语（"machine learning"）
# 4. NOUN — 单个名词回退

# 关键过滤
_GENERIC_HEADS = {"thing", "stuff", "way", "time", ...}  # 太泛的词不算实体
_CIRCUMSTANTIAL_MODS = {"solo", "team", "first", ...}  # 环境描述不算
_GENERIC_ENDINGS = {"work", "job", "task", ...}  # 泛尾词剥离
```

**对我们有什么用**：
- 当前 `memory_store` 存储的是完整文本，没有实体层
- 如果在存储时提取实体，检索时可以用实体匹配作为额外信号
- spaCy NER 比纯 LLM 提取便宜且快，适合批量处理

#### 机制 4：Session Scope + Actor Filtering

**设计**：记忆按 user_id / agent_id / run_id 三维隔离，查询时用 filters 精确匹配：

```python
def _build_filters_and_metadata(user_id, agent_id, run_id, actor_id, ...):
    # 至少需要一个 scope ID
    if not session_ids_provided:
        raise ValidationError("至少需要 user_id, agent_id, run_id 中的一个")
    
    # storage metadata = 模板（存入时用）
    base_metadata_template = {"user_id": ..., "agent_id": ..., "run_id": ...}
    
    # query filters = 精确过滤（查询时用）
    effective_query_filters = {"user_id": ..., "actor_id": ...}
```

**对我们有什么用**：
- 我们的记忆系统没有 session scope，所有记忆混在一起
- 为每个对话 session 建立隔离，检索时自动过滤当前 session
- `run_id` 对应我们的一次进化操作，可以追踪每次进化的记忆贡献

#### 机制 5：敏感数据分层处理

**设计**：三层防御（allowlist → exact denylist → suffix denylist）：

```python
_RUNTIME_FIELDS = frozenset({"http_auth", "auth", "connection_class", "ssl_context"})
_SENSITIVE_FIELDS_EXACT = frozenset({"api_key", "secret_key", "private_key", ...})
_SENSITIVE_SUFFIXES = ("_password", "_secret", "_token", "_credential")

def _is_sensitive_field(field_name):
    name = field_name.lower().strip()
    if name in _RUNTIME_FIELDS: return False      # 白名单最高优先
    if name in _SENSITIVE_FIELDS_EXACT: return True  # 精确匹配
    return any(name.endswith(s) for s in _SENSITIVE_SUFFIXES)  # 后缀兜底
```

**对我们有什么用**：
- 我们的 SOUL.md 已经有保密铁律，但代码层面没有自动脱敏
- 这个三层模型可以直接用：runtime 字段白名单 + 敏感字段黑名单 + 后缀兜底
- 写日志前自动调用 `_is_sensitive_field` 过滤

---

## 三、融合洞察 — 对我们系统的具体改进方案

### 1. 技能基因重组（Runnable 模式 × 进化公式）

**问题**：470 个技能是"空壳"，没有统一接口，不能组合

**方案**：定义 `SkillGene` 接口，每个技能必须实现：

```python
class SkillGene(ABC):
    """技能的基因"""
    name: str
    version: str
    input_type: type
    output_type: type
    
    def execute(self, input, config=None) -> output: ...
    def batch(self, inputs, config=None) -> list: ...
    def compose_with(self, other: SkillGene) -> SkillChain: ...
```

**收益**：技能自动获得组合、并行、流式能力，ΔG 公式的 C 维度直接提升

### 2. 记忆层升级（Mem0 模式 × 自学习本能）

**问题**：memory_store 是覆盖式存储，memory_recall 是纯向量检索

**方案**：
1. 改为 additive-only：每次交互提取事实，直接 add
2. 加 BM25 检索：关键词匹配 + 向量 + entity boost
3. 时间加权：最新事实权重更高
4. Session scope：按对话隔离记忆

**收益**：自学习本能从"存了但检索不到"变成"精准召回"，Ω 维度提升

### 3. 安全脱敏自动化（Mem0 敏感数据模型 × 安全基因）

**问题**：保密铁律是文本规则，没有代码层面自动执行

**方案**：写日志/输出前，自动过 `_is_sensitive_field` 过滤器

**收益**：安全防线从"靠记忆"变成"靠代码"，S_v 从软约束变成硬约束

### 4. 三信号检索融合（Mem0 scoring × 向量记忆）

**问题**：memory_recall 只用向量相似度，长文本查询效果差

**方案**：实现 `score_and_rank` 三路融合：
- 向量相似度（门槛）
- BM25 关键词匹配（加分）
- 实体匹配（加分）

**收益**：检索精度提升，知识消化的"回忆"环节更可靠

---

## 四、关键数字

| 指标 | Mem0 旧版 | Mem0 新版 | 提升 |
|------|-----------|-----------|------|
| LoCoMo | 71.4 | 91.6 | +28% |
| LongMemEval | 67.8 | 94.8 | +40% |
| Token 消耗 | 未公开 | 7K | 极低 |
| 延迟 | 未公开 | ~1s | 极快 |

**关键 insight**：Additive extraction + multi-signal retrieval = 更少的 token，更高的准确率，更快的速度。

---

## 五、消化质量自评

| 维度 | 旧版（空壳） | 本版（真实） |
|------|-------------|-------------|
| 核心设计模式 | ❌ 只有标题 | ✅ 5 个可操作的模式 |
| 可迁移机制 | ❌ "待深入分析" | ✅ 4 个具体方案 |
| 代码片段 | ❌ 无 | ✅ 关键函数+类+公式 |
| 应用方案 | ❌ "写入 MEMORY.md" | ✅ 每个洞察有具体收益 |
| 举一反三 | ❌ "[待扩展]" | ✅ Runnable → 技能基因，Mem0 → 记忆层 |

**结论**：两个空壳技能已经变成真正的知识。核心不是"记住了 LangChain 和 Mem0"，而是"理解了它们怎么设计的，以及我们能用什么"。
