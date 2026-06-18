"""
superclaw GEP 引擎 — 移植自 Evolver (EvoMap/evolver) 的 GEP 协议

来源（真实源码对照）：
- src/gep/schemas/gene.js     → Gene schema (createGene/validateGene)
- src/gep/schemas/capsule.js  → Capsule schema (createCapsule/validateCapsule)
- src/gep/analyzer.js         → analyzeFailures (从 MEMORY.md 提取失败模式)
- src/gep/bridge.js           → writePromptArtifact (GEP 提示词持久化)
- src/gep/assetStore.js       → Gene/Capsule 持久化 + 文件锁

融合 APEX 框架：
- Gene.category 映射到 APEX 进化领域 (repair/optimize/innovate/explore)
- Capsule.outcome.score 喂给 APEX Φ 值计算
- EvolutionEvent 记录到 APEX 记忆系统的 EvolutionHistory
- LLMRouter 驱动信号提取和策略生成

GEP 三大资产：
- Gene:          原子能力单元（经验证的代码或 Prompt 片段）
- Capsule:       成功任务执行路径（可复用的工作流）
- EvolutionEvent: 不可篡改的进化日志（SHA-256 内容寻址）
"""
import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


# ============================================================
# Schema 常量 — 严格对照 Evolver src/gep/schemas/gene.js
# ============================================================

SCHEMA_VERSION = "1.6.0"

VALID_CATEGORIES = ["repair", "optimize", "innovate", "explore"]
VALID_ROUTING_TIERS = ["cheap", "mid", "expensive"]
VALID_REASONING_LEVELS = ["off", "low", "medium", "high"]
VALID_TOOL_POLICY_SEVERITIES = ["warn", "block"]

# Capsule 常量 — 对照 capsule.js
VALID_OUTCOME_STATUSES = ["success", "failed"]
VALID_SOURCE_TYPES = ["generated", "reused", "reference", "user_authored"]
VALID_VISIBILITIES = ["private", "unlisted", "public"]
VALID_COST_TIERS = ["cheap", "standard", "premium"]

# 进化策略 — 对照 Evolver config.js
VALID_STRATEGIES = ["balanced", "innovate", "harden", "repair-only",
                    "early-stabilize", "steady-state", "auto"]

# 70/30 法则：70% 修复，30% 创新
REPAIR_RATIO = 0.7


# ============================================================
# Gene — 原子能力单元
# ============================================================

@dataclass
class Gene:
    """GEP Gene — 原子能力单元

    对照 Evolver src/gep/schemas/gene.js 的 GENE_DEFAULTS
    """
    type: str = "Gene"
    id: str = ""
    category: str = "innovate"           # repair/optimize/innovate/explore
    signals_match: List[str] = field(default_factory=list)    # 触发信号模式
    strategy: List[str] = field(default_factory=list)         # 执行策略步骤
    validation: List[str] = field(default_factory=list)       # 验证步骤
    constraints: Dict[str, Any] = field(default_factory=lambda: {
        "max_files": 20,
        "forbidden_paths": [".git", "node_modules"],
    })
    preconditions: List[str] = field(default_factory=list)
    summary: str = ""
    schema_version: str = SCHEMA_VERSION
    epigenetic_marks: List[str] = field(default_factory=list)  # 表观遗传标记
    learning_history: List[Dict] = field(default_factory=list)  # 学习历史
    anti_patterns: List[str] = field(default_factory=list)      # 反模式
    routing_hint: Optional[Dict] = None     # 路由提示 (tier/reasoning_level)
    tool_policy: Optional[Dict] = None      # 工具策略 (allow_only/deny/severity)

    def __post_init__(self):
        """规范化 — 对照 gene.js createGene()"""
        if self.category not in VALID_CATEGORIES:
            self.category = "innovate"
        if not self.id:
            self.id = self._compute_id()
        if not self.schema_version:
            self.schema_version = SCHEMA_VERSION
        # 确保数组是副本
        self.signals_match = list(self.signals_match)
        self.strategy = list(self.strategy)
        self.validation = list(self.validation)
        self.preconditions = list(self.preconditions)

    def _compute_id(self) -> str:
        """计算内容寻址 ID（SHA-256 前 16 位）"""
        content = json.dumps({
            "category": self.category,
            "signals_match": self.signals_match,
            "strategy": self.strategy,
            "summary": self.summary,
        }, sort_keys=True, ensure_ascii=False)
        return "gene-" + hashlib.sha256(content.encode()).hexdigest()[:16]

    def validate(self) -> bool:
        """验证 Gene — 对照 gene.js validateGene()"""
        if self.type != "Gene":
            raise ValueError(f'Gene.type 必须是 "Gene", 实际: {self.type}')
        if not self.id or not isinstance(self.id, str):
            raise ValueError("Gene.id 必须是非空字符串")
        if self.category not in VALID_CATEGORIES:
            raise ValueError(f"Gene.category 必须是: {VALID_CATEGORIES}, 实际: {self.category}")
        if not isinstance(self.signals_match, list):
            raise ValueError("Gene.signals_match 必须是数组")
        if not isinstance(self.strategy, list):
            raise ValueError("Gene.strategy 必须是数组")
        return True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.type,
            "id": self.id,
            "category": self.category,
            "signals_match": self.signals_match,
            "strategy": self.strategy,
            "validation": self.validation,
            "constraints": self.constraints,
            "preconditions": self.preconditions,
            "summary": self.summary,
            "schema_version": self.schema_version,
            "epigenetic_marks": self.epigenetic_marks,
            "learning_history": self.learning_history,
            "anti_patterns": self.anti_patterns,
            "routing_hint": self.routing_hint,
            "tool_policy": self.tool_policy,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Gene":
        return cls(
            id=d.get("id", ""),
            category=d.get("category", "innovate"),
            signals_match=d.get("signals_match", []),
            strategy=d.get("strategy", []),
            validation=d.get("validation", []),
            constraints=d.get("constraints", {}),
            preconditions=d.get("preconditions", []),
            summary=d.get("summary", ""),
            epigenetic_marks=d.get("epigenetic_marks", []),
            learning_history=d.get("learning_history", []),
            anti_patterns=d.get("anti_patterns", []),
            routing_hint=d.get("routing_hint"),
            tool_policy=d.get("tool_policy"),
        )


# ============================================================
# Capsule — 成功任务执行路径
# ============================================================

@dataclass
class Capsule:
    """GEP Capsule — 成功任务执行路径

    对照 Evolver src/gep/schemas/capsule.js 的 CAPSULE_DEFAULTS
    """
    type: str = "Capsule"
    id: str = ""
    schema_version: str = SCHEMA_VERSION
    trigger: List[str] = field(default_factory=list)      # 触发条件
    gene: Optional[str] = None                            # 关联的 Gene ID
    summary: str = ""
    confidence: float = 0.0                               # 置信度 0~1
    blast_radius: Dict[str, int] = field(default_factory=lambda: {"files": 0, "lines": 0})
    outcome: Dict[str, Any] = field(default_factory=lambda: {"status": "failed", "score": 0})
    success_streak: int = 0                               # 连续成功次数
    success_reason: Optional[str] = None
    source_type: Optional[str] = None                     # generated/reused/reference/user_authored
    derivation_tokens: Optional[Dict] = None              # token 消耗
    content: Optional[str] = None                         # 执行内容
    diff: Optional[str] = None                            # 代码差异
    strategy: List[str] = field(default_factory=list)
    execution_trace: List[Dict] = field(default_factory=list)  # 执行轨迹
    asset_id: Optional[str] = None                        # 内容寻址 ID

    def __post_init__(self):
        if not self.id:
            self.id = self._compute_id()
        if not self.asset_id:
            self.asset_id = self.id
        if self.outcome.get("status") not in VALID_OUTCOME_STATUSES:
            self.outcome["status"] = "failed"

    def _compute_id(self) -> str:
        content = json.dumps({
            "trigger": self.trigger,
            "gene": self.gene,
            "summary": self.summary,
            "outcome": self.outcome,
        }, sort_keys=True, ensure_ascii=False)
        return "cap-" + hashlib.sha256(content.encode()).hexdigest()[:16]

    def validate(self) -> bool:
        """验证 Capsule — 对照 capsule.js validateCapsule()"""
        if self.type != "Capsule":
            raise ValueError(f'Capsule.type 必须是 "Capsule", 实际: {self.type}')
        if not self.id or not isinstance(self.id, str):
            raise ValueError("Capsule.id 必须是非空字符串")
        if not isinstance(self.outcome, dict):
            raise ValueError("Capsule.outcome 必须是对象")
        if self.outcome.get("status") not in VALID_OUTCOME_STATUSES:
            raise ValueError(f"outcome.status 必须是: {VALID_OUTCOME_STATUSES}")
        if not isinstance(self.trigger, list):
            raise ValueError("Capsule.trigger 必须是数组")
        if not isinstance(self.execution_trace, list):
            raise ValueError("Capsule.execution_trace 必须是数组")
        return True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.type,
            "id": self.id,
            "schema_version": self.schema_version,
            "trigger": self.trigger,
            "gene": self.gene,
            "summary": self.summary,
            "confidence": self.confidence,
            "blast_radius": self.blast_radius,
            "outcome": self.outcome,
            "success_streak": self.success_streak,
            "success_reason": self.success_reason,
            "source_type": self.source_type,
            "derivation_tokens": self.derivation_tokens,
            "content": self.content,
            "diff": self.diff,
            "strategy": self.strategy,
            "execution_trace": self.execution_trace,
            "asset_id": self.asset_id,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Capsule":
        return cls(
            id=d.get("id", ""),
            trigger=d.get("trigger", []),
            gene=d.get("gene"),
            summary=d.get("summary", ""),
            confidence=d.get("confidence", 0.0),
            blast_radius=d.get("blast_radius", {"files": 0, "lines": 0}),
            outcome=d.get("outcome", {"status": "failed", "score": 0}),
            success_streak=d.get("success_streak", 0),
            success_reason=d.get("success_reason"),
            source_type=d.get("source_type"),
            derivation_tokens=d.get("derivation_tokens"),
            content=d.get("content"),
            diff=d.get("diff"),
            strategy=d.get("strategy", []),
            execution_trace=d.get("execution_trace", []),
            asset_id=d.get("asset_id"),
        )


# ============================================================
# EvolutionEvent — 不可篡改的进化日志
# ============================================================

@dataclass
class EvolutionEvent:
    """GEP EvolutionEvent — 不可篡改的进化日志

    对照 Evolver 的 EvolutionEvent 概念：
    - SHA-256 内容寻址，防篡改
    - 记录每次变异或修复的完整上下文
    """
    event_id: str = ""
    timestamp: str = ""
    event_type: str = ""           # innovation / repair / solidify / rollback
    gene_id: Optional[str] = None
    capsule_id: Optional[str] = None
    strategy: str = ""             # balanced/innovate/harden/repair-only
    trigger_signal: str = ""       # 触发信号
    summary: str = ""
    phi_before: float = 0.0        # 进化前 Φ 值
    phi_after: float = 0.0         # 进化后 Φ 值
    tier_before: int = 1
    tier_after: int = 1
    success: bool = False
    hash: str = ""                 # 内容哈希

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now().isoformat()
        if not self.event_id:
            self.event_id = self._compute_id()
        if not self.hash:
            self.hash = self._compute_hash()

    def _compute_id(self) -> str:
        return "evt-" + hashlib.sha256(
            f"{self.timestamp}{self.event_type}{self.gene_id}".encode()
        ).hexdigest()[:16]

    def _compute_hash(self) -> str:
        """计算内容哈希（防篡改）"""
        content = json.dumps({
            "event_id": self.event_id,
            "timestamp": self.timestamp,
            "event_type": self.event_type,
            "gene_id": self.gene_id,
            "capsule_id": self.capsule_id,
            "phi_before": self.phi_before,
            "phi_after": self.phi_after,
            "success": self.success,
        }, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(content.encode()).hexdigest()

    def verify(self) -> bool:
        """验证事件完整性"""
        return self.hash == self._compute_hash()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "timestamp": self.timestamp,
            "event_type": self.event_type,
            "gene_id": self.gene_id,
            "capsule_id": self.capsule_id,
            "strategy": self.strategy,
            "trigger_signal": self.trigger_signal,
            "summary": self.summary,
            "phi_before": self.phi_before,
            "phi_after": self.phi_after,
            "tier_before": self.tier_before,
            "tier_after": self.tier_after,
            "success": self.success,
            "hash": self.hash,
        }


# ============================================================
# Signal — 进化信号（从日志/记忆中提取）
# ============================================================

@dataclass
class Signal:
    """进化信号 — 错误/性能/功能请求"""
    signal_type: str = ""          # error / performance / feature / pattern
    source: str = ""               # 来源（日志文件/记忆/用户输入）
    severity: str = "low"          # low/medium/high/critical
    pattern: str = ""              # 信号模式
    context: str = ""              # 上下文
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now().isoformat()


# ============================================================
# GeneLibrary — Gene 持久化存储（对照 assetStore.js）
# ============================================================

class GeneLibrary:
    """Gene 库 — 持久化存储和检索 Gene

    对照 Evolver src/gep/assetStore.js 的 loadGenes/upsertGene
    """

    def __init__(self, library_dir: Path):
        self.dir = library_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self.genes_file = self.dir / "gene-library.json"
        self.capsules_file = self.dir / "capsule-store.json"
        self.events_file = self.dir / "event-log.jsonl"

    def load_genes(self) -> List[Gene]:
        """加载所有 Gene"""
        if not self.genes_file.exists():
            return []
        try:
            data = json.loads(self.genes_file.read_text(encoding="utf-8"))
            return [Gene.from_dict(g) for g in data]
        except (json.JSONDecodeError, IOError):
            return []

    def upsert_gene(self, gene: Gene) -> bool:
        """添加或更新 Gene"""
        gene.validate()
        genes = self.load_genes()
        # 去重：相同 id 替换
        genes = [g for g in genes if g.id != gene.id]
        genes.append(gene)
        try:
            self.genes_file.write_text(
                json.dumps([g.to_dict() for g in genes],
                           indent=2, ensure_ascii=False),
                encoding="utf-8"
            )
            return True
        except IOError:
            return False

    def find_gene(self, gene_id: str) -> Optional[Gene]:
        """按 ID 查找 Gene"""
        for g in self.load_genes():
            if g.id == gene_id:
                return g
        return None

    def find_by_signal(self, signal_pattern: str) -> List[Gene]:
        """按信号模式查找匹配的 Gene"""
        results = []
        for g in self.load_genes():
            for sm in g.signals_match:
                if signal_pattern.lower() in sm.lower():
                    results.append(g)
                    break
        return results

    def load_capsules(self) -> List[Capsule]:
        """加载所有 Capsule"""
        if not self.capsules_file.exists():
            return []
        try:
            data = json.loads(self.capsules_file.read_text(encoding="utf-8"))
            return [Capsule.from_dict(c) for c in data]
        except (json.JSONDecodeError, IOError):
            return []

    def upsert_capsule(self, capsule: Capsule) -> bool:
        """添加或更新 Capsule"""
        capsule.validate()
        capsules = self.load_capsules()
        capsules = [c for c in capsules if c.id != capsule.id]
        capsules.append(capsule)
        try:
            self.capsules_file.write_text(
                json.dumps([c.to_dict() for c in capsules],
                           indent=2, ensure_ascii=False),
                encoding="utf-8"
            )
            return True
        except IOError:
            return False

    def append_event(self, event: EvolutionEvent) -> bool:
        """追加进化事件（不可篡改日志）"""
        try:
            with open(self.events_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")
            return True
        except IOError:
            return False

    def load_events(self, limit: int = 50) -> List[Dict]:
        """加载最近的进化事件"""
        if not self.events_file.exists():
            return []
        try:
            lines = self.events_file.read_text(encoding="utf-8").strip().splitlines()
            records = []
            for line in lines[-limit:]:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
            return records
        except IOError:
            return []

    def stats(self) -> Dict[str, Any]:
        """统计信息"""
        genes = self.load_genes()
        capsules = self.load_capsules()
        events = self.load_events(1000)

        # 按类别统计 Gene
        gene_categories: Dict[str, int] = {}
        for g in genes:
            gene_categories[g.category] = gene_categories.get(g.category, 0) + 1

        # 成功的 Capsule
        successful = sum(1 for c in capsules if c.outcome.get("status") == "success")

        return {
            "total_genes": len(genes),
            "gene_categories": gene_categories,
            "total_capsules": len(capsules),
            "successful_capsules": successful,
            "total_events": len(events),
            "library_path": str(self.dir),
        }
