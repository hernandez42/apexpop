"""
superclaw GEP 生命周期引擎 — 10 步进化循环

对照 Evolver 的 10 步进化周期：
1. Scan Logs       → 扫描记忆系统/日志
2. Extract Signals → 提取进化信号
3. Select Gene     → 选择要进化的 Gene/Capsule
4. Generate Prompt → 生成 GEP 提示词
5. Execute Modify  → 执行修改（通过 LLM）
6. Validate Tests  → 验证结果
7. Solidify        → 固化成功的变更
8. Publish         → 发布到 Gene 库
9. Log Event       → 记录 EvolutionEvent
10. Return Monitor → 返回监控

融合关系：
- LLMRouter: 驱动步骤 2(信号提取)、4(策略生成)、6(验证)
- MemoryStore: 步骤 1(扫描记忆)、7(固化到记忆)、9(记录事件)
- APEX ApexState: 步骤 6 后更新 Φ 值
- GeneLibrary: 步骤 3(检索)、8(发布)
"""
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .gep_schema import (
    Gene, Capsule, EvolutionEvent, Signal,
    GeneLibrary, VALID_STRATEGIES,
)
from .memory import MemoryStore
from .llm_router import LLMRouter, get_router, CompletionResult

# ============================================================
# 自进化模块导入 — 可选依赖，导入失败不阻断核心 GEP 循环
# ============================================================
try:
    from .capability_registry import Capability, analyze_gaps
    _CAPABILITY_REGISTRY_AVAILABLE = True
except ImportError:  # pragma: no cover
    _CAPABILITY_REGISTRY_AVAILABLE = False

try:
    from .github_tools import GitHubSearcher, FileDownloader
    _GITHUB_TOOLS_AVAILABLE = True
except ImportError:  # pragma: no cover
    _GITHUB_TOOLS_AVAILABLE = False

try:
    from .code_generator import CodeSpec, GeneratedCode
    _CODE_GENERATOR_AVAILABLE = True
except ImportError:  # pragma: no cover
    _CODE_GENERATOR_AVAILABLE = False

try:
    from .evolution_validator import EvolutionAction
    _EVOLUTION_VALIDATOR_AVAILABLE = True
except ImportError:  # pragma: no cover
    _EVOLUTION_VALIDATOR_AVAILABLE = False

try:
    from .curiosity import (
        CuriosityDrive,
        CuriosityDrivenExplorer,
    )
    _CURIOSITY_AVAILABLE = True
except ImportError:  # pragma: no cover
    _CURIOSITY_AVAILABLE = False

try:
    from .experience_learner import ExperienceLearner, StrategyOutcome
    _EXPERIENCE_LEARNER_AVAILABLE = True
except ImportError:  # pragma: no cover
    _EXPERIENCE_LEARNER_AVAILABLE = False

try:
    from .feedback_learner import FeedbackLearner
    _FEEDBACK_LEARNER_AVAILABLE = True
except ImportError:  # pragma: no cover
    _FEEDBACK_LEARNER_AVAILABLE = False

# core_bridge 仅用 stdlib，无外部依赖，始终可用
_CORE_BRIDGE_AVAILABLE = True


# ============================================================
# 信号提取器 — 对照 Evolver analyzer.js
# ============================================================

class SignalExtractor:
    """从记忆系统/日志中提取进化信号

    对照 Evolver src/gep/analyzer.js 的 analyzeFailures()
    """

    def __init__(self, memory: MemoryStore, llm: Optional[LLMRouter] = None,
                 feedback_learner: Optional["FeedbackLearner"] = None):
        self.memory = memory
        self.llm = llm or get_router()
        self.feedback_learner = feedback_learner

    def scan(self) -> List[Signal]:
        """扫描记忆系统，提取进化信号"""
        signals: List[Signal] = []

        # 1. 从反思日志提取（APEX 四问）
        reflections = self.memory.reflection.history(3)
        for r in reflections:
            for gap in r.get("gaps", []):
                signals.append(Signal(
                    signal_type="feature",
                    source="reflection:gaps",
                    severity="medium",
                    pattern=gap,
                    context=f"反思时间: {r.get('timestamp', '')}",
                ))
            for problem in r.get("problems", []):
                signals.append(Signal(
                    signal_type="error",
                    source="reflection:problems",
                    severity="high",
                    pattern=problem,
                    context=f"反思时间: {r.get('timestamp', '')}",
                ))

        # 2. 从进化历史提取失败模式
        history = self.memory.evolution.recent(20)
        for h in history:
            if not h.get("retained", False):
                signals.append(Signal(
                    signal_type="error",
                    source="evolution:failed",
                    severity="medium",
                    pattern=f"领域 {h.get('domain', 'unknown')} 进化失败",
                    context=f"cycle={h.get('cycle')}, score={h.get('score', 0)}",
                ))

        # 3. 从知识库检索短板（通过 memory 工具）
        status = self.memory.evolution.summary()
        if status.get("retention_rate", 1.0) < 0.5:
            signals.append(Signal(
                signal_type="performance",
                source="evolution:stats",
                severity="high",
                pattern=f"保留率低: {status.get('retention_rate', 0):.1%}",
                context=f"总循环: {status.get('total_cycles', 0)}",
            ))

        # 4. 从用户反馈提取信号（可选）
        if self.feedback_learner is not None:
            try:
                feedback_signals = self.feedback_learner.to_signals(limit=5)
                # 反馈信号优先级高，插入到前面
                signals = feedback_signals + signals
            except Exception:
                pass  # 反馈提取失败不阻断主流程

        # 5. 用 LLM 深度分析（如果可用）
        # L3+ 2026-06-19 修复自进化学习推理缺陷 (师父 14:49 明示)
        # 1. signals 为空时也调 LLM 主动生成 (冷启动 fallback)
        # 2. 显式选 agens 避免 mock fallback 回显 prompt
        if self.llm:
            llm_signal = self._llm_extract_signals(signals)
            if llm_signal:
                signals.extend(llm_signal)

        return signals

    def _llm_extract_signals(self, existing_signals: List[Signal]) -> List[Signal]:
        """用 LLM 深度分析信号（发现隐藏模式）

        L3+ 2026-06-19: signals 为空时主动调 LLM 生成 hypothesis (冷启动),
        避免空 workspace 100% 失败. 显式 provider="agnes" 避免 mock fallback.
        """
        # 冷启动 prompt — workspace 无历史信号时主动生成 hypothesis
        if not existing_signals:
            ws_path = str(getattr(self.memory, "root", "unknown"))
            prompt = (
                "你是一个 AI 自进化分析器。当前工作区是冷启动状态（无历史信号/无反思/无进化记录）。\n"
                "请根据工作区根目录名和已知上下文, 主动生成 3-5 个合理的进化 hypothesis\n"
                "（修复/优化/添加能力/补全测试等）。\n\n"
                "输出严格 JSON 数组, 每个元素:\n"
                "{\n"
                '  "signal_type": "error|performance|feature|pattern",\n'
                '  "severity": "low|medium|high|critical",\n'
                '  "pattern": "简短描述 (≤40 字)",\n'
                '  "context": "上下文说明 (≤80 字)"\n'
                "}\n\n"
                "要求:\n"
                "1. pattern 必须可执行 (LLM 能基于此生成代码/配置变更)\n"
                "2. 涵盖至少 2 个不同 severity (low/medium/high)\n"
                "3. 优先考虑: 错误处理、测试覆盖、文档、监控、配置校验\n"
                "4. 只输出 JSON 数组, 不要其他文字\n\n"
                "工作区路径: " + ws_path
            )
        else:
            signals_summary = "\n".join(
                f"- [{s.severity}] {s.signal_type}: {s.pattern}"
                for s in existing_signals[:5]
            )
            prompt = (
                f"分析以下进化信号,找出隐藏的失败模式或优化机会。\n\n"
                f"当前信号:\n{signals_summary}\n\n"
                f"请输出 JSON 数组,每个元素包含:\n"
                f"- signal_type: error/performance/feature/pattern\n"
                f"- severity: low/medium/high/critical\n"
                f"- pattern: 信号描述\n"
                f"- context: 上下文\n\n"
                f"只输出 JSON,不要其他文字。"
            )

        # 显式选 agens (主路由) 避免 mock fallback (mock 会回显 prompt 当 reply)
        try:
            result = self.llm.complete(
                [{"role": "user", "content": prompt}],
                provider="agens",
                complexity="high",
            )
        except Exception:
            result = self.llm.complete(
                [{"role": "user", "content": prompt}],
                complexity="high",
            )

        if result.error or not result.content:
            return []

        try:
            content = result.content.strip()
            if content.startswith("REPLACED_FENCE"):
                content = content.split("REPLACED_FENCE")[1]
                if content.startswith("json"):
                    content = content[4:]
            items = json.loads(content)
            if not isinstance(items, list):
                items = [items]

            return [
                Signal(
                    signal_type=item.get("signal_type", "pattern"),
                    source="llm:analysis" if existing_signals else "llm:cold_start",
                    severity=item.get("severity", "low"),
                    pattern=item.get("pattern", ""),
                    context=item.get("context", ""),
                )
                for item in items[:3]  # 最多取 3 个
            ]
        except (json.JSONDecodeError, KeyError):
            return []
# 策略管理器 — 对照 Evolver Strategy Manager
# ============================================================

class StrategyManager:
    """进化策略管理器

    对照 Evolver 的策略系统:
    - balanced:     70% 修复 + 30% 创新
    - innovate:     30% 修复 + 70% 创新
    - harden:       90% 修复 + 10% 创新
    - repair-only:  100% 修复
    """

    STRATEGY_RATIOS = {
        "balanced":        (0.7, 0.3),
        "innovate":        (0.3, 0.7),
        "harden":          (0.9, 0.1),
        "repair-only":     (1.0, 0.0),
        "early-stabilize": (0.8, 0.2),
        "steady-state":    (0.6, 0.4),
        "auto":            (0.7, 0.3),  # auto 默认 balanced
    }

    def __init__(self, strategy: str = "balanced",
                 curiosity: Optional["CuriosityDrive"] = None,
                 experience_learner: Optional["ExperienceLearner"] = None):
        if strategy not in VALID_STRATEGIES:
            strategy = "balanced"
        self.strategy = strategy
        self.curiosity = curiosity
        self.experience_learner = experience_learner

    def _current_ratios(self) -> Tuple[float, float]:
        """获取当前策略比例（有经验学习时用调整后权重，否则用默认）"""
        if self.experience_learner is not None:
            return self.experience_learner.adjusted_weight(self.strategy)
        return self.STRATEGY_RATIOS.get(self.strategy, (0.7, 0.3))

    def select_category(self, signals: List[Signal]) -> str:
        """根据策略和信号选择进化类别

        Returns: repair/optimize/innovate/explore

        好奇心影响（可选）：
        - 如果 curiosity.should_explore(current_signals) → 提升 explore 概率
        - 用 curiosity.intrinsic_reward 调整 repair/optimize/innovate/explore 的权重
        - 无 curiosity 时走原逻辑（向后兼容）

        经验学习影响（可选）：
        - 有 experience_learner 时用调整后的 repair/innovate 比例
        - 无 experience_learner 时用 STRATEGY_RATIOS 默认值（向后兼容）
        """
        repair_ratio, innovate_ratio = self._current_ratios()

        # 统计信号类型
        error_count = sum(1 for s in signals if s.signal_type == "error")
        perf_count = sum(1 for s in signals if s.signal_type == "performance")
        feature_count = sum(1 for s in signals if s.signal_type == "feature")

        # 按策略比例决定
        import random
        r = random.random()

        if self.strategy == "repair-only":
            return "repair"

        # ---- 好奇心影响（可选）----
        curiosity_explore = False
        if self.curiosity is not None:
            signal_patterns = [s.pattern for s in signals]
            if self.curiosity.should_explore(signal_patterns):
                # 用 intrinsic_reward 调整权重：reward 越高 → repair_ratio 越低
                if signal_patterns:
                    top_reward = max(
                        self.curiosity.intrinsic_reward(p) for p in signal_patterns
                    )
                else:
                    top_reward = 0.5
                repair_ratio = repair_ratio * (1.0 - min(top_reward, 1.0) * 0.5)
                curiosity_explore = True

        if r < repair_ratio:
            # 修复类
            if error_count > 0:
                return "repair"
            elif perf_count > 0:
                return "optimize"
            else:
                return "repair"
        else:
            # 创新类
            if feature_count > 0:
                # 好奇心探索时优先 explore 而非 innovate
                if curiosity_explore:
                    return "explore"
                return "innovate"
            else:
                return "explore"

    def should_solidify(self, capsule: Capsule) -> bool:
        """决定是否固化 Capsule（对照 70/30 法则）"""
        if capsule.outcome.get("status") != "success":
            return False
        score = capsule.outcome.get("score", 0)
        # 成功且评分 > 0.6 才固化
        return score > 0.6


# ============================================================
# GEP 引擎 — 10 步进化循环
# ============================================================

class GEPEngine:
    """GEP 进化引擎 — 融合 Evolver + APEX + LLM + 记忆系统

    10 步进化循环:
    1. scan_logs         → 扫描记忆系统
    2. extract_signals   → 提取进化信号
    3. select_gene       → 选择 Gene/Capsule
    4. generate_prompt   → 生成 GEP 提示词
    5. execute_modify    → LLM 执行修改
    6. validate_tests    → 验证结果
    7. solidify          → 固化成功变更
    8. publish           → 发布到 Gene 库
    9. log_event         → 记录进化事件
    10. return_monitor   → 返回监控状态
    """

    def __init__(self,
                 memory: Optional[MemoryStore] = None,
                 llm: Optional[LLMRouter] = None,
                 strategy: str = "balanced",
                 workspace: Optional[Path] = None,
                 capability_registry: Optional[Any] = None,
                 code_generator: Optional[Any] = None,
                 sandbox_executor: Optional[Any] = None,
                 dynamic_loader: Optional[Any] = None,
                 evolution_validator: Optional[Any] = None,
                 project_root: Optional[Path] = None,
                 curiosity: Optional["CuriosityDrive"] = None,
                 experience_learner: Optional["ExperienceLearner"] = None,
                 feedback_learner: Optional["FeedbackLearner"] = None,
                 c_core: Optional[Any] = None,
                 rust_engine: Optional[Any] = None):
        self.workspace = workspace or Path(__file__).parent.parent.resolve()
        self.memory = memory or MemoryStore(self.workspace)
        self.llm = llm or get_router()
        self.strategy_mgr = StrategyManager(
            strategy, curiosity=curiosity,
            experience_learner=experience_learner,
        )
        self.library = GeneLibrary(self.workspace / "gep-library")
        self.feedback_learner = feedback_learner
        self.signal_extractor = SignalExtractor(
            self.memory, self.llm, feedback_learner=feedback_learner,
        )
        self.cycle_count = 0

        # ---- 好奇心启发式（可选，非真内在动机模型）----
        self.curiosity = curiosity

        # ---- 经验学习（可选）----
        self.experience_learner = experience_learner

        # ---- C/Rust 核心桥接（可选，None 时走纯 Python 逻辑）----
        self.c_core = c_core
        self.rust_engine = rust_engine

        # ---- 自进化模块（可选，全部 None 时走原有逻辑）----
        self.capability_registry = capability_registry
        self.code_generator = code_generator
        self.sandbox_executor = sandbox_executor
        self.dynamic_loader = dynamic_loader
        self.evolution_validator = evolution_validator
        self.project_root = Path(project_root) if project_root else self.workspace

        # 是否启用 _real 步骤（只有显式传入新模块实例时才启用）
        self._use_real_steps: bool = all([
            self.code_generator is not None,
            self.sandbox_executor is not None,
            self.dynamic_loader is not None,
            self.evolution_validator is not None,
        ])

        # 缓存上一次 _real 步骤的中间结果（跨 step 传递）
        self._last_generated_code: Optional[Any] = None
        self._last_sandbox_result: Optional[Any] = None
        self._last_tool_file: Optional[Path] = None

    def run_cycle(self) -> Dict[str, Any]:
        """执行一个完整的 10 步进化循环"""
        self.cycle_count += 1
        cycle_id = f"cycle-{self.cycle_count}"
        result: Dict[str, Any] = {
            "cycle": self.cycle_count,
            "cycle_id": cycle_id,
            "timestamp": datetime.now().isoformat(),
            "strategy": self.strategy_mgr.strategy,
            "steps": {},
        }

        # ---- Step 1: Scan Logs ----
        result["steps"]["1_scan_logs"] = self._step_scan_logs()

        # ---- Step 2: Extract Signals ----
        signals = self._step_extract_signals()

        # ---- 好奇心注入（可选）：检查是否该主动探索 ----
        if self.curiosity is not None:
            result["curiosity_active"] = True
            if self.curiosity.should_explore([s.pattern for s in signals]):
                # insert 到开头，确保 curiosity 信号在 signals[:5] 截断后仍可见
                signals.insert(0, Signal(
                    signal_type="curiosity",
                    source="curiosity:drive",
                    severity="info",
                    pattern="好奇心启发式触发探索（频次引导，非真内在动机）",
                    context="heuristic curiosity",
                ))
                result["curiosity_exploring"] = True
            else:
                result["curiosity_exploring"] = False

        result["steps"]["2_extract_signals"] = {
            "count": len(signals),
            "signals": [s.__dict__ for s in signals[:5]],
        }

        if not signals:
            result["status"] = "no_signals"
            return result

        # ---- Step 3: Select Gene/Capsule ----
        category, selected_gene = self._step_select_gene(signals)
        result["steps"]["3_select_gene"] = {
            "category": category,
            "selected_gene": selected_gene.id if selected_gene else None,
        }

        # ---- Step 4: Generate GEP Prompt ----
        prompt = self._step_generate_prompt(signals, category, selected_gene)
        result["steps"]["4_generate_prompt"] = {"prompt_length": len(prompt)}

        # ---- Step 5: Execute Modify ----
        if self._use_real_steps:
            modification = self._step_execute_modify_real(prompt, category)
        else:
            modification = self._step_execute_modify(prompt, category)
        result["steps"]["5_execute_modify"] = {
            "provider": modification.provider,
            "error": modification.error,
            "content_length": len(modification.content),
        }

        # ---- Step 6: Validate Tests ----
        if self._use_real_steps:
            validation = self._step_validate_real(modification, category)
        else:
            validation = self._step_validate(modification, category)
        result["steps"]["6_validate"] = validation

        # ---- Step 7: Solidify ----
        if self._use_real_steps:
            capsule = self._step_solidify_real(
                signals, category, modification, validation, selected_gene
            )
        else:
            capsule = self._step_solidify(
                signals, category, modification, validation, selected_gene
            )
        result["steps"]["7_solidify"] = {
            "capsule_id": capsule.id if capsule else None,
            "solidified": capsule is not None,
        }

        # ---- Step 8: Publish ----
        published = self._step_publish(capsule, selected_gene, category)
        result["steps"]["8_publish"] = {"published": published}

        # ---- Step 9: Log Event ----
        event = self._step_log_event(
            cycle_id, category, signals, capsule, validation
        )
        result["steps"]["9_log_event"] = {
            "event_id": event.event_id if event else None,
        }

        # ---- Step 10: Return Monitor ----
        monitor = self._step_return_monitor()
        result["steps"]["10_monitor"] = monitor

        # ---- 经验学习：记录策略执行结果（可选）----
        if self.experience_learner is not None:
            try:
                outcome = StrategyOutcome(
                    strategy=self.strategy_mgr.strategy,
                    category=category,
                    score=validation.get("score", 0.0),
                    retained=capsule is not None,
                    cycle=self.cycle_count,
                    signal_count=len(signals),
                )
                self.experience_learner.record(outcome)
                result["experience_recorded"] = True
            except Exception:
                result["experience_recorded"] = False

        # ---- C/Rust 核心桥接：真调二进制记录进化（可选）----
        # C 核心心跳推进 cycle + 记录 mutations/knowledge
        # Rust 引擎按 category 变异基因，量化平衡
        core_status: Dict[str, Any] = {}
        if self.c_core is not None:
            try:
                hb = self.c_core.heartbeat()
                if hb.get("status") == "ok":
                    # 记录本次进化的 mutations/knowledge 到 C 核心状态机
                    self.c_core.record_evolution(
                        mutations=1 if capsule is not None else 0,
                        knowledge=len(signals),
                    )
                    core_status["c_heartbeat"] = hb.get("data", {})
            except Exception as e:
                core_status["c_error"] = str(e)
        if self.rust_engine is not None:
            try:
                # 按 category 在 Rust 引擎里变异一个基因
                mut = self.rust_engine.mutate(domain=str(category), change=0.1)
                if mut.get("status") == "ok":
                    core_status["rust_mutate"] = mut.get("data", {})
            except Exception as e:
                core_status["rust_error"] = str(e)
        if core_status:
            result["core_integration"] = core_status

        result["status"] = "success" if validation.get("passed") else "failed"
        return result

    # ---- Step implementations ----

    def _step_scan_logs(self) -> Dict[str, Any]:
        """Step 1: 扫描记忆系统"""
        stats = self.memory.knowledge.stats()
        evo_summary = self.memory.evolution.summary()
        reflections = self.memory.reflection.history(1)

        return {
            "knowledge_files": stats.get("total", 0),
            "evolution_cycles": evo_summary.get("total_cycles", 0),
            "reflections": len(reflections),
            "gene_library": self.library.stats(),
        }

    def _step_extract_signals(self) -> List[Signal]:
        """Step 2: 提取进化信号"""
        return self.signal_extractor.scan()

    def _step_select_gene(self, signals: List[Signal]) -> Tuple[str, Optional[Gene]]:
        """Step 3: 选择 Gene/Capsule"""
        category = self.strategy_mgr.select_category(signals)

        # 尝试从已有 Gene 库中找到匹配的
        if signals:
            top_signal = signals[0]
            matching = self.library.find_by_signal(top_signal.pattern)
            if matching:
                return category, matching[0]

        # 没有匹配的 Gene，创建新的
        new_gene = Gene(
            category=category,
            signals_match=[s.pattern for s in signals[:3]],
            summary=f"自动生成: {category} 类别，基于 {len(signals)} 个信号",
        )
        return category, new_gene

    def _step_generate_prompt(self, signals: List[Signal],
                               category: str, gene: Optional[Gene]) -> str:
        """Step 4: 生成 GEP 提示词"""
        signals_text = "\n".join(
            f"  - [{s.severity}] {s.signal_type}: {s.pattern}"
            for s in signals[:5]
        )

        gene_text = ""
        if gene:
            gene_text = f"""
已有 Gene:
  ID: {gene.id}
  类别: {gene.category}
  信号匹配: {gene.signals_match}
  策略: {gene.strategy}
  摘要: {gene.summary}
"""

        prompt = f"""你是 superclaw GEP 进化引擎。

当前进化类别: {category}
进化策略: {self.strategy_mgr.strategy}

检测到的信号:
{signals_text}
{gene_text}

请基于以上信号，生成一个具体的进化策略。输出 JSON:
{{
  "action": "具体行动描述",
  "target": "目标文件或模块",
  "expected_improvement": "预期改进",
  "risk_level": "low/medium/high"
}}

只输出 JSON。"""
        return prompt

    def _step_execute_modify(self, prompt: str,
                              category: str) -> CompletionResult:
        """Step 5: LLM 执行修改"""
        # 根据类别选择复杂度
        complexity = "low" if category == "repair" else "medium"

        result = self.llm.complete(
            [{"role": "user", "content": prompt}],
            complexity=complexity,
        )
        return result

    def _step_validate(self, modification: CompletionResult,
                        category: str) -> Dict[str, Any]:
        """Step 6: 验证结果"""
        if modification.error:
            return {
                "passed": False,
                "reason": f"LLM 错误: {modification.error}",
                "score": 0.0,
            }

        if not modification.content or len(modification.content) < 10:
            return {
                "passed": False,
                "reason": "LLM 返回内容过短",
                "score": 0.0,
            }

        # 尝试解析 JSON
        action = None
        try:
            content = modification.content.strip()
            if content.startswith("```"):
                content = content.split("```")[1]
                if content.startswith("json"):
                    content = content[4:]
            parsed = json.loads(content)
            action = parsed.get("action", "")
        except (json.JSONDecodeError, IndexError):
            pass

        # 基础验证：内容非空 + 有实际行动
        score = 0.5
        if action and len(action) > 5:
            score = 0.7
        if modification.tokens_used > 50:
            score += 0.1

        return {
            "passed": score >= 0.5,
            "reason": "验证通过" if score >= 0.5 else "评分不足",
            "score": min(score, 1.0),
            "action": action,
            "provider": modification.provider,
            "tokens": modification.tokens_used,
        }

    def _step_solidify(self, signals: List[Signal], category: str,
                        modification: CompletionResult,
                        validation: Dict[str, Any],
                        gene: Optional[Gene]) -> Optional[Capsule]:
        """Step 7: 固化成功的变更"""
        if not validation.get("passed"):
            return None

        capsule = Capsule(
            trigger=[s.pattern for s in signals[:3]],
            gene=gene.id if gene else None,
            summary=f"{category} 进化: {(validation.get('action') or '')[:100]}",
            confidence=validation.get("score", 0.5),
            outcome={
                "status": "success",
                "score": validation.get("score", 0.5),
            },
            source_type="generated",
            derivation_tokens={
                "input_tokens": 0,
                "output_tokens": modification.tokens_used,
                "total_tokens": modification.tokens_used,
                "basis": "measured",
            } if modification.tokens_used else None,
            content=modification.content[:500] if modification.content else None,
            strategy=[category],
            execution_trace=[{
                "step": "llm_complete",
                "provider": modification.provider,
                "latency_ms": modification.latency_ms,
            }],
        )

        # 策略管理器决定是否固化
        if self.strategy_mgr.should_solidify(capsule):
            return capsule
        return None

    # ============================================================
    # _real 步骤实现 — 用自进化模块替换占位逻辑
    # 仅在 __init__ 显式传入新模块实例时启用
    # ============================================================

    def _step_execute_modify_real(self, prompt: str,
                                   category: str) -> CompletionResult:
        """Step 5 (real): 用 CodeGenerator 生成代码 + SandboxExecutor 验证

        替换原 _step_execute_modify 的"只调 LLM 拿文本"逻辑：
        1. 从 prompt/category 构造 CodeSpec
        2. CodeGenerator.generate(spec) → GeneratedCode
        3. SandboxExecutor.execute(generated_code) → SandboxResult
        4. 返回 CompletionResult（content=生成的代码）

        失败时返回带 error 的 CompletionResult。
        """
        # 清空上一次的中间结果
        self._last_generated_code = None
        self._last_sandbox_result = None
        self._last_tool_file = None

        if self.code_generator is None or self.sandbox_executor is None:
            return CompletionResult(
                content="", provider="code_generator", model="",
                error="code_generator 或 sandbox_executor 未配置",
            )

        try:
            # 构造 CodeSpec — 用 category + cycle_count 生成唯一工具名
            tool_name = f"{category}_tool_{self.cycle_count}"
            spec = CodeSpec(
                name=tool_name,
                description=f"GEP {category} 进化：基于信号生成的新能力",
                signature=f"def {tool_name}(*args, **kwargs) -> dict",
                parameters=[],
                context=prompt[:500],
                language="python",
            )

            # 生成代码
            generated = self.code_generator.generate(spec)
            self._last_generated_code = generated

            # 沙箱验证
            sandbox_result = self.sandbox_executor.execute(generated)
            self._last_sandbox_result = sandbox_result

            return CompletionResult(
                content=generated.code,
                provider=generated.llm_provider or "code_generator",
                model="code_generator",
                tokens_used=generated.tokens_used,
                error=None if sandbox_result.passed else (
                    "沙箱验证失败: " + "; ".join(sandbox_result.errors)
                ),
            )
        except Exception as e:
            return CompletionResult(
                content="", provider="code_generator", model="",
                error=f"代码生成/沙箱执行异常: {e}",
            )

    def _step_validate_real(self, modification: CompletionResult,
                             category: str) -> Dict[str, Any]:
        """Step 6 (real): 用 SandboxResult 判断验证结果

        替换原 _step_validate 的"只看文本长度"逻辑：
        - import_ok + call_ok + test_ok → score=0.9, passed=True
        - 否则 → score=0.0, passed=False, reason=errors
        """
        if modification.error:
            return {
                "passed": False,
                "reason": f"代码生成/沙箱错误: {modification.error}",
                "score": 0.0,
            }

        sandbox_result = self._last_sandbox_result
        if sandbox_result is None:
            return {
                "passed": False,
                "reason": "无沙箱验证结果",
                "score": 0.0,
            }

        if sandbox_result.passed:
            return {
                "passed": True,
                "reason": "沙箱验证通过（import + call + test）",
                "score": 0.9,
                "action": f"{category}_tool_generated",
                "provider": modification.provider,
                "tokens": modification.tokens_used,
                "sandbox": {
                    "import_ok": sandbox_result.import_ok,
                    "call_ok": sandbox_result.call_ok,
                    "test_ok": sandbox_result.test_ok,
                    "duration_ms": sandbox_result.duration_ms,
                },
            }

        return {
            "passed": False,
            "reason": "沙箱验证失败: " + "; ".join(sandbox_result.errors),
            "score": 0.0,
            "sandbox": {
                "import_ok": sandbox_result.import_ok,
                "call_ok": sandbox_result.call_ok,
                "test_ok": sandbox_result.test_ok,
                "errors": sandbox_result.errors,
            },
        }

    def _step_solidify_real(self, signals: List[Signal], category: str,
                             modification: CompletionResult,
                             validation: Dict[str, Any],
                             gene: Optional[Gene]) -> Optional[Capsule]:
        """Step 7 (real): 注册工具 + EvolutionValidator 验证 + 创建 Capsule

        替换原 _step_solidify 的"只创建 Capsule 数据结构"逻辑：
        1. 用 DynamicToolLoader.load_from_code() 注册工具
        2. 用 EvolutionValidator.validate_evolution() 验证（含 blast radius + 回滚）
        3. critical 风险 → 拒绝，返回 None
        4. 验证失败 → 回滚，返回 None
        5. 验证通过 → 创建 Capsule，注册新能力到 CapabilityRegistry

        安全边界：
        - 只能新增能力（写到 dynamic-tools/），不能修改 superclaw/ 核心包
        - critical 风险直接拒绝（EvolutionValidator 自动判定）
        - 所有外部调用 try-except 包裹
        """
        if not validation.get("passed"):
            return None

        generated = self._last_generated_code
        if generated is None:
            return None

        if self.dynamic_loader is None or self.evolution_validator is None:
            return None

        try:
            # 1. 用 DynamicToolLoader 注册工具
            tool_name = generated.name
            registered = self.dynamic_loader.load_from_code(
                code=generated.code,
                module_name=generated.name,
                tool_name=tool_name,
                function_name=generated.name,
                description=f"GEP {category} 进化生成的工具",
                params=[],
                force=False,
            )

            if not registered:
                return None

            # 记录工具文件路径（用于 EvolutionAction）
            tool_file = self.dynamic_loader.tools_dir / f"{generated.name}.py"
            self._last_tool_file = tool_file

            # 2. 创建快照（用于回滚）
            snapshot_id = None
            snapshot_mgr = getattr(self.evolution_validator, "snapshot_mgr", None)
            if snapshot_mgr is not None:
                try:
                    snapshot_id = snapshot_mgr.create_snapshot(
                        files=[tool_file],
                        label=f"gep_{category}_{self.cycle_count}",
                    )
                except Exception:
                    snapshot_id = None

            # 3. 构造 EvolutionAction
            action = EvolutionAction(
                action_type="add_tool",
                target_files=[tool_file],
                backup_snapshot_id=snapshot_id,
                description=f"GEP {category} 进化：新增工具 {tool_name}",
            )

            # 4. 验证（含 blast radius + 测试 + 回滚）
            validation_result = self.evolution_validator.validate_evolution(action)

            if not validation_result.passed:
                # 验证失败（critical 或测试失败）→ 不创建 Capsule
                # EvolutionValidator 已经处理了回滚
                return None

            # 5. 验证通过 → 创建 Capsule
            capsule = Capsule(
                trigger=[s.pattern for s in signals[:3]],
                gene=gene.id if gene else None,
                summary=f"{category} 进化: 新增工具 {tool_name}",
                confidence=validation.get("score", 0.9),
                outcome={
                    "status": "success",
                    "score": validation.get("score", 0.9),
                    "tool_name": tool_name,
                    "blast_radius": validation_result.blast_radius.risk_level,
                },
                source_type="generated",
                derivation_tokens={
                    "input_tokens": 0,
                    "output_tokens": modification.tokens_used,
                    "total_tokens": modification.tokens_used,
                    "basis": "measured",
                } if modification.tokens_used else None,
                content=modification.content[:500] if modification.content else None,
                strategy=[category],
                execution_trace=[{
                    "step": "code_generate_and_sandbox",
                    "provider": modification.provider,
                    "tool_name": tool_name,
                    "sandbox_passed": True,
                }],
            )

            # 6. 注册新能力到 CapabilityRegistry
            if self.capability_registry is not None:
                try:
                    new_cap = Capability(
                        name=tool_name,
                        description=f"GEP {category} 进化生成的工具",
                        category="tool",
                        source="generated",
                        enabled=True,
                    )
                    self.capability_registry.register(new_cap)
                    save_fn = getattr(self.capability_registry, "save", None)
                    if save_fn is not None:
                        save_fn()
                except Exception:
                    pass  # 注册失败不阻断主流程

            # 策略管理器决定是否固化
            if self.strategy_mgr.should_solidify(capsule):
                return capsule
            return None

        except Exception:
            return None

    def _step_publish(self, capsule: Optional[Capsule],
                       gene: Optional[Gene], category: str) -> bool:
        """Step 8: 发布到 Gene 库

        Gene 无论是否固化都保存（记录尝试），
        Capsule 只在固化成功时保存。
        """
        published = False

        # 发布 Gene（无论 Capsule 是否固化）
        if gene:
            gene.learning_history.append({
                "cycle": self.cycle_count,
                "result": "success" if (capsule and capsule.outcome.get("status") == "success") else "failed",
                "score": capsule.outcome.get("score", 0) if capsule else 0,
                "timestamp": datetime.now().isoformat(),
            })
            self.library.upsert_gene(gene)
            published = True

        # 发布 Capsule（仅固化成功时）
        if capsule:
            self.library.upsert_capsule(capsule)
            published = True

        return published

    def _step_log_event(self, cycle_id: str, category: str,
                         signals: List[Signal],
                         capsule: Optional[Capsule],
                         validation: Dict[str, Any]) -> Optional[EvolutionEvent]:
        """Step 9: 记录进化事件"""
        # 获取当前 APEX 状态
        apex_state = self._get_apex_state()
        phi_before = apex_state.get("phi", 0)

        # 记录到记忆系统的进化历史
        self.memory.evolution.record(
            cycle=self.cycle_count,
            phi=phi_before,
            domain=category,
            gene_id=(capsule.gene if capsule else "") or "",
            score=validation.get("score", 0),
            retained=capsule is not None,
            tier=apex_state.get("tier", 1),
        )

        # 创建 EvolutionEvent
        event = EvolutionEvent(
            event_type="innovation" if category in ("innovate", "explore") else "repair",
            gene_id=capsule.gene if capsule else None,
            capsule_id=capsule.id if capsule else None,
            strategy=self.strategy_mgr.strategy,
            trigger_signal=signals[0].pattern if signals else "",
            summary=f"Cycle {self.cycle_count}: {category} - {validation.get('reason', '')}",
            phi_before=phi_before,
            phi_after=phi_before + validation.get("score", 0) * 0.1,
            tier_before=apex_state.get("tier", 1),
            tier_after=apex_state.get("tier", 1),
            success=validation.get("passed", False),
        )

        # 追加到事件日志
        self.library.append_event(event)

        # 同时触发 APEX 反思
        reflect_state = {
            "phi": phi_before,
            "tier": apex_state.get("tier", 1),
            "fitness": validation.get("score", 0.5),
            "mutations": self.cycle_count,
            "knowledge": self.library.stats().get("total_genes", 0),
            "health": 2 if validation.get("passed") else 0,
            "balance": 0.5,
        }
        self.memory.reflection.reflect(reflect_state)

        return event

    def _step_return_monitor(self) -> Dict[str, Any]:
        """Step 10: 返回监控状态"""
        stats = self.library.stats()
        return {
            "cycle": self.cycle_count,
            "gene_library": stats,
            "llm_status": self.llm.status(),
            "memory_status": {
                "knowledge_files": self.memory.knowledge.stats().get("total", 0),
                "evolution_cycles": self.memory.evolution.summary().get("total_cycles", 0),
            },
        }

    def _get_apex_state(self) -> Dict[str, Any]:
        """获取当前 APEX 状态"""
        try:
            # 从 apex-state.json 读取
            apex_file = self.workspace / "apex-state" / "apex-state.json"
            if apex_file.exists():
                data = json.loads(apex_file.read_text(encoding="utf-8"))
                return data.get("current", {"phi": 0, "tier": 1})
        except (json.JSONDecodeError, IOError):
            pass
        return {"phi": 0, "tier": 1}

    def run(self, cycles: int = 3, verbose: bool = True) -> List[Dict[str, Any]]:
        """运行多个进化循环"""
        results = []
        for i in range(cycles):
            if verbose:
                print(f"\n{'='*60}")
                print(f"  🧬 GEP 进化循环 #{self.cycle_count + 1}")
                print(f"{'='*60}")

            result = self.run_cycle()
            results.append(result)

            if verbose:
                status = "✅" if result.get("status") == "success" else "❌"
                print(f"\n  {status} 循环完成: {result.get('status')}")
                steps = result.get("steps", {})
                if "2_extract_signals" in steps:
                    print(f"  信号: {steps['2_extract_signals']['count']} 个")
                if "3_select_gene" in steps:
                    print(f"  类别: {steps['3_select_gene']['category']}")
                if "6_validate" in steps:
                    print(f"  验证: score={steps['6_validate'].get('score', 0):.2f}")
                if "7_solidify" in steps:
                    print(f"  固化: {'是' if steps['7_solidify']['solidified'] else '否'}")

            time.sleep(0.1)

        return results

    # ============================================================
    # 完整自进化循环 — 感知短板 → 获取能力 → 自我构建 → 验证闭环
    # ============================================================

    def run_self_evolution_cycle(self,
                                  task_requirements: Optional[List[str]] = None
                                  ) -> Dict[str, Any]:
        """完整自进化循环：感知短板 → 获取能力 → 自我构建 → 验证闭环

        安全边界：
        - 只能新增能力（写到 dynamic-tools/ 或 skills/）
        - 不能修改 superclaw/ 核心包（EvolutionValidator 自动拒绝 critical）
        - critical 风险直接拒绝 + 回滚
        - 所有外部调用 try-except 包裹

        Args:
            task_requirements: 任务需要的能力名列表（None 时用默认任务集）

        Returns:
            包含 gaps/acquired/validated/rolled_back/rejected/errors 字段的字典
        """
        result: Dict[str, Any] = {
            "timestamp": datetime.now().isoformat(),
            "cycle": self.cycle_count + 1,
            "gaps": [],
            "acquired": [],
            "validated": [],
            "rolled_back": [],
            "rejected": [],
            "errors": [],
            "status": "success",
        }

        # ---- 检查必要模块 ----
        if self.capability_registry is None:
            result["status"] = "skipped"
            result["errors"].append("capability_registry 未配置，跳过自进化")
            return result

        if not self._use_real_steps:
            result["status"] = "skipped"
            missing = []
            if self.code_generator is None:
                missing.append("code_generator")
            if self.sandbox_executor is None:
                missing.append("sandbox_executor")
            if self.dynamic_loader is None:
                missing.append("dynamic_loader")
            if self.evolution_validator is None:
                missing.append("evolution_validator")
            result["errors"].append(
                f"自进化模块未完整配置（缺少: {', '.join(missing)}），跳过"
            )
            return result

        # 此时 _use_real_steps=True，所有模块都已配置（mypy 无法推断，显式断言）
        sandbox_executor = self.sandbox_executor
        dynamic_loader = self.dynamic_loader
        evolution_validator = self.evolution_validator
        if (sandbox_executor is None or dynamic_loader is None
                or evolution_validator is None):
            result["status"] = "skipped"
            result["errors"].append("自进化模块未完整配置，跳过")
            return result

        # ---- Step 1: 感知短板 ----
        try:
            if task_requirements is None:
                # 默认任务集：检测一些常见但 superclaw 未注册的能力
                task_requirements = [
                    "weather_query", "fetch_url", "parse_json",
                ]

            gaps = analyze_gaps(self.capability_registry, task_requirements)
            result["gaps"] = [
                {
                    "missing_capability": g.missing_capability,
                    "severity": g.severity,
                    "suggested_action": g.suggested_action,
                    "search_query": g.search_query,
                }
                for g in gaps
            ]
        except Exception as e:
            result["status"] = "failed"
            result["errors"].append(f"短板感知失败: {e}")
            return result

        if not gaps:
            result["status"] = "no_gaps"
            return result

        # ---- Step 2-4: 对每个 gap 获取能力 + 自我构建 + 验证 ----
        for gap in gaps:
            gap_info = {
                "missing_capability": gap.missing_capability,
                "severity": gap.severity,
                "suggested_action": gap.suggested_action,
            }

            try:
                # 跳过 manual（无法自动获取）
                if gap.suggested_action == "manual":
                    result["rejected"].append({
                        **gap_info,
                        "reason": "manual action required",
                    })
                    continue

                generated_code = None

                # ---- Step 2: 获取能力 ----
                if gap.suggested_action == "github_search":
                    generated_code = self._acquire_from_github(gap)
                    # GitHub 搜索失败（无网络/rate limit）→ 回退到代码生成
                    if generated_code is None:
                        generated_code = self._acquire_from_generator(gap)
                elif gap.suggested_action == "code_generate":
                    generated_code = self._acquire_from_generator(gap)

                if generated_code is None:
                    result["rejected"].append({
                        **gap_info,
                        "reason": "capability acquisition failed",
                    })
                    continue

                result["acquired"].append({
                    **gap_info,
                    "code_name": generated_code.name,
                    "code_length": len(generated_code.code),
                })

                # ---- Step 3: 自我构建（沙箱验证 + 注册工具）----
                sandbox_result = sandbox_executor.execute(generated_code)
                if not sandbox_result.passed:
                    result["rejected"].append({
                        **gap_info,
                        "reason": f"sandbox failed: {'; '.join(sandbox_result.errors)}",
                    })
                    continue

                # 注册工具
                tool_name = generated_code.name
                registered = dynamic_loader.load_from_code(
                    code=generated_code.code,
                    module_name=generated_code.name,
                    tool_name=tool_name,
                    function_name=generated_code.name,
                    description=f"自进化生成：{gap.missing_capability}",
                    params=[],
                    force=False,
                )

                if not registered:
                    result["rejected"].append({
                        **gap_info,
                        "reason": "tool registration failed",
                    })
                    continue

                tool_file = dynamic_loader.tools_dir / f"{generated_code.name}.py"

                # ---- Step 4: 验证闭环 ----
                # 创建快照（用于回滚）
                snapshot_id = None
                snapshot_mgr = getattr(evolution_validator, "snapshot_mgr", None)
                if snapshot_mgr is not None:
                    try:
                        snapshot_id = snapshot_mgr.create_snapshot(
                            files=[tool_file],
                            label=f"self_evo_{gap.missing_capability}",
                        )
                    except Exception:
                        pass

                action = EvolutionAction(
                    action_type="add_tool",
                    target_files=[tool_file],
                    backup_snapshot_id=snapshot_id,
                    description=f"自进化：新增能力 {gap.missing_capability}",
                )

                validation_result = evolution_validator.validate_evolution(action)

                if not validation_result.passed:
                    if validation_result.rollback_performed:
                        result["rolled_back"].append({
                            **gap_info,
                            "reason": "; ".join(validation_result.errors),
                            "snapshot_id": snapshot_id,
                        })
                    else:
                        result["rejected"].append({
                            **gap_info,
                            "reason": "; ".join(validation_result.errors),
                            "risk_level": validation_result.blast_radius.risk_level,
                        })
                    continue

                # ---- 验证通过 → 注册新能力 ----
                try:
                    new_cap = Capability(
                        name=gap.missing_capability,
                        description="自进化生成的能力",
                        category="tool",
                        source="generated",
                        enabled=True,
                    )
                    self.capability_registry.register(new_cap)
                    save_fn = getattr(self.capability_registry, "save", None)
                    if save_fn is not None:
                        save_fn()
                except Exception:
                    pass  # 注册失败不阻断主流程

                result["validated"].append({
                    **gap_info,
                    "tool_name": tool_name,
                    "risk_level": validation_result.blast_radius.risk_level,
                    "test_passed": validation_result.test_result.passed_count,
                })

            except Exception as e:
                result["errors"].append(
                    f"处理 gap {gap.missing_capability} 失败: {e}"
                )
                result["rejected"].append({
                    **gap_info,
                    "reason": f"exception: {e}",
                })

        # 总结状态
        if result["errors"] and not result["validated"]:
            result["status"] = "failed"
        elif result["validated"]:
            result["status"] = "success"
        else:
            result["status"] = "no_acquisition"

        self.cycle_count += 1
        return result

    def _acquire_from_github(self, gap: Any) -> Optional[Any]:
        """从 GitHub 搜索获取能力（搜索 + 下载代码）

        安全：所有调用 try-except，失败返回 None

        注意：GitHub 下载的代码本身不带测试，这里生成一个最小冒烟测试
        （import + callable 检查），否则沙箱会因"无测试代码"直接拒绝，
        导致 GitHub 获取路径永远走不通（修复自审 #4 发现的死路径）。
        """
        if not _GITHUB_TOOLS_AVAILABLE:
            return None
        try:
            searcher = GitHubSearcher()
            results = searcher.search_code(gap.search_query, limit=3)

            # 过滤错误结果
            valid_results = [
                r for r in results
                if "error" not in r and r.get("download_url")
            ]
            if not valid_results:
                return None

            # 取第一个结果，下载代码
            downloader = FileDownloader()
            target_dir = self.workspace / "dynamic-tools"
            target_dir.mkdir(parents=True, exist_ok=True)
            target_path = target_dir / f"{gap.missing_capability}.py"

            downloaded = downloader.download_raw(
                valid_results[0]["download_url"], target_path
            )
            if downloaded is None:
                return None

            # 读取下载的代码
            code_text = downloaded.read_text(encoding="utf-8")

            # 生成最小冒烟测试：import + callable 检查
            # 无法为任意 GitHub 代码写完整测试，但至少验证可导入且函数存在
            cap_name = gap.missing_capability
            smoke_test = (
                f"import {cap_name}\n"
                f"def test_importable():\n"
                f"    assert hasattr({cap_name}, '{cap_name}')\n"
                f"def test_callable():\n"
                f"    assert callable(getattr({cap_name}, '{cap_name}'))\n"
            )

            return GeneratedCode(
                name=gap.missing_capability,
                code=code_text,
                imports=[],
                dependencies=[],
                test_code=smoke_test,
                llm_provider="github_search",
                tokens_used=0,
            )
        except Exception:
            return None

    def _acquire_from_generator(self, gap: Any) -> Optional[Any]:
        """用 CodeGenerator 生成能力

        安全：所有调用 try-except，失败返回 None
        """
        if self.code_generator is None or not _CODE_GENERATOR_AVAILABLE:
            return None
        try:
            spec = CodeSpec(
                name=gap.missing_capability,
                description=f"自进化生成能力：{gap.missing_capability}",
                signature=f"def {gap.missing_capability}(*args, **kwargs) -> dict",
                parameters=[],
                context=f"Gap severity: {gap.severity}, search query: {gap.search_query}",
                language="python",
            )
            return self.code_generator.generate(spec)
        except Exception:
            return None

    # ============================================================
    # 好奇心驱动探索 — 主动发现探索目标并转成 Signal 处理
    # ============================================================

    def run_curious_exploration(self) -> Dict[str, Any]:
        """好奇心驱动探索：发现探索目标 → 转成 Signal → 调自进化循环处理

        流程：
        1. 用 CuriosityDrivenExplorer 发现探索目标（分析能力清单 vs 可能领域）
        2. 把探索目标转成 GEP engine 能理解的 Signal
        3. 记录探索过的领域到 NoveltyScorer
        4. 调 run_self_evolution_cycle 处理（用目标领域作为任务需求）

        Returns:
            包含 status/targets/reasons/signals/evolution 字段的字典
        """
        if self.curiosity is None:
            return {
                "status": "skipped",
                "reason": "curiosity not configured",
                "targets": [],
                "signals": 0,
            }

        if self.capability_registry is None:
            return {
                "status": "skipped",
                "reason": "capability_registry not configured",
                "targets": [],
                "signals": 0,
            }

        explorer = CuriosityDrivenExplorer(
            self.curiosity, self.capability_registry, self.llm
        )

        current_state: Dict[str, Any] = {
            "known_domains": [c.name for c in self.capability_registry.list_all()],
        }
        targets = explorer.discover_targets(current_state)

        if not targets:
            return {
                "status": "no_targets",
                "targets": [],
                "reasons": [],
                "signals": 0,
            }

        # 转成 Signal
        signals = [explorer.generate_curiosity_signal(t) for t in targets]

        # 记录探索过的领域到 NoveltyScorer（持久化新颖度衰减）
        for t in targets:
            self.curiosity.novelty_scorer.record(t.target_domain, "domain")

        # 调 run_self_evolution_cycle 处理（用目标领域作为任务需求）
        task_requirements = [t.target_domain for t in targets]
        evo_result = self.run_self_evolution_cycle(
            task_requirements=task_requirements
        )

        return {
            "status": evo_result.get("status", "unknown"),
            "targets": [t.target_domain for t in targets],
            "reasons": [t.reason for t in targets],
            "signals": len(signals),
            "evolution": evo_result,
        }

    # ============================================================
    # 经验驱动调整 — 分析历史 → 调整策略权重
    # ============================================================

    def run_experience_driven_adjustment(self) -> Dict[str, Any]:
        """经验驱动调整：分析历史策略表现 → 调整 StrategyManager 权重

        流程：
        1. 用 ExperienceLearner.analyze_all() 分析所有策略的成功率/平均分/趋势
        2. 用 ExperienceLearner.adjusted_weights() 计算调整后权重
        3. 报告 best/worst 策略
        4. 权重调整会自动影响下一次 run_cycle 的 select_category（通过 _current_ratios）

        Returns:
            包含 status/stats/adjusted_weights/best/worst/report 字段的字典
        """
        if self.experience_learner is None:
            return {
                "status": "skipped",
                "reason": "experience_learner not configured",
                "stats": {},
                "adjusted_weights": {},
            }

        try:
            stats_all = self.experience_learner.analyze_all()
            adjusted = self.experience_learner.adjusted_weights()
            report = self.experience_learner.report()
            best = self.experience_learner.best_strategy()
            worst = self.experience_learner.analyzer.worst_strategy()

            return {
                "status": "success",
                "stats": {
                    s: {
                        "attempts": st.attempts,
                        "success_rate": round(st.success_rate, 3),
                        "avg_score": round(st.avg_score, 3),
                        "recent_trend": round(st.recent_trend, 3),
                    }
                    for s, st in stats_all.items()
                },
                "adjusted_weights": {
                    s: {"repair": r, "innovate": i}
                    for s, (r, i) in adjusted.items()
                },
                "best_strategy": best,
                "worst_strategy": worst,
                "report": report,
            }
        except Exception as e:
            return {
                "status": "failed",
                "reason": str(e),
                "stats": {},
                "adjusted_weights": {},
            }

    # ============================================================
    # 反馈驱动进化 — 从用户反馈提取信号 → 驱动进化循环
    # ============================================================

    def run_feedback_driven_evolution(self) -> Dict[str, Any]:
        """反馈驱动进化：分析用户反馈 → 提取信号 → 驱动进化循环

        流程：
        1. 用 FeedbackLearner.analyze() 统计反馈
        2. 用 FeedbackLearner.to_signals() 把反馈转成进化信号
        3. 如果有 critical 信号（bug），优先调 run_cycle 处理
        4. 报告反馈统计和处理的信号数

        Returns:
            包含 status/stats/signals_extracted/cycle_run 字段的字典
        """
        if self.feedback_learner is None:
            return {
                "status": "skipped",
                "reason": "feedback_learner not configured",
                "stats": {},
                "signals_extracted": 0,
            }

        try:
            stats = self.feedback_learner.analyze()
            signals = self.feedback_learner.to_signals(limit=10)
            critical_signals = self.feedback_learner.critical_signals(limit=3)

            result: Dict[str, Any] = {
                "status": "success",
                "stats": {
                    "total": stats.total,
                    "positive": stats.positive,
                    "negative": stats.negative,
                    "suggestion": stats.suggestion,
                    "bug": stats.bug,
                    "avg_sentiment": stats.avg_sentiment,
                    "satisfaction_rate": round(
                        self.feedback_learner.analyzer.satisfaction_rate(), 3
                    ),
                },
                "signals_extracted": len(signals),
                "critical_signals": len(critical_signals),
            }

            # 有 critical 信号（bug）→ 驱动一次进化循环处理
            if critical_signals:
                cycle_result = self.run_cycle()
                result["cycle_run"] = True
                result["cycle_status"] = cycle_result.get("status")
            else:
                result["cycle_run"] = False

            return result
        except Exception as e:
            return {
                "status": "failed",
                "reason": str(e),
                "stats": {},
                "signals_extracted": 0,
            }
