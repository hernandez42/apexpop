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
# 信号提取器 — 对照 Evolver analyzer.js
# ============================================================

class SignalExtractor:
    """从记忆系统/日志中提取进化信号

    对照 Evolver src/gep/analyzer.js 的 analyzeFailures()
    """

    def __init__(self, memory: MemoryStore, llm: Optional[LLMRouter] = None):
        self.memory = memory
        self.llm = llm or get_router()

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

        # 4. 用 LLM 深度分析（如果可用）
        if self.llm and signals:
            llm_signal = self._llm_extract_signals(signals)
            if llm_signal:
                signals.extend(llm_signal)

        return signals

    def _llm_extract_signals(self, existing_signals: List[Signal]) -> List[Signal]:
        """用 LLM 深度分析信号（发现隐藏模式）"""
        signals_summary = "\n".join(
            f"- [{s.severity}] {s.signal_type}: {s.pattern}"
            for s in existing_signals[:5]
        )

        prompt = f"""分析以下进化信号，找出隐藏的失败模式或优化机会。

当前信号:
{signals_summary}

请输出 JSON 数组，每个元素包含:
- signal_type: error/performance/feature/pattern
- severity: low/medium/high/critical
- pattern: 信号描述
- context: 上下文

只输出 JSON，不要其他文字。"""

        result = self.llm.complete(
            [{"role": "user", "content": prompt}],
            complexity="medium",
        )

        if result.error or not result.content:
            return []

        try:
            # 尝试解析 LLM 返回的 JSON
            content = result.content.strip()
            if content.startswith("```"):
                content = content.split("```")[1]
                if content.startswith("json"):
                    content = content[4:]
            items = json.loads(content)
            if not isinstance(items, list):
                items = [items]

            return [
                Signal(
                    signal_type=item.get("signal_type", "pattern"),
                    source="llm:analysis",
                    severity=item.get("severity", "low"),
                    pattern=item.get("pattern", ""),
                    context=item.get("context", ""),
                )
                for item in items[:3]  # 最多取 3 个
            ]
        except (json.JSONDecodeError, KeyError):
            return []


# ============================================================
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

    def __init__(self, strategy: str = "balanced"):
        if strategy not in VALID_STRATEGIES:
            strategy = "balanced"
        self.strategy = strategy

    def select_category(self, signals: List[Signal]) -> str:
        """根据策略和信号选择进化类别

        Returns: repair/optimize/innovate/explore
        """
        repair_ratio, innovate_ratio = self.STRATEGY_RATIOS.get(
            self.strategy, (0.7, 0.3)
        )

        # 统计信号类型
        error_count = sum(1 for s in signals if s.signal_type == "error")
        perf_count = sum(1 for s in signals if s.signal_type == "performance")
        feature_count = sum(1 for s in signals if s.signal_type == "feature")

        # 按策略比例决定
        import random
        r = random.random()

        if self.strategy == "repair-only":
            return "repair"

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
                 workspace: Optional[Path] = None):
        self.workspace = workspace or Path(__file__).parent.parent.resolve()
        self.memory = memory or MemoryStore(self.workspace)
        self.llm = llm or get_router()
        self.strategy_mgr = StrategyManager(strategy)
        self.library = GeneLibrary(self.workspace / "gep-library")
        self.signal_extractor = SignalExtractor(self.memory, self.llm)
        self.cycle_count = 0

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
        modification = self._step_execute_modify(prompt, category)
        result["steps"]["5_execute_modify"] = {
            "provider": modification.provider,
            "error": modification.error,
            "content_length": len(modification.content),
        }

        # ---- Step 6: Validate Tests ----
        validation = self._step_validate(modification, category)
        result["steps"]["6_validate"] = validation

        # ---- Step 7: Solidify ----
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
            gene_id=capsule.gene if capsule else "",
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
