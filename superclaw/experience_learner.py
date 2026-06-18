"""
superclaw 经验学习 — 从历史进化结果中学习，动态调整策略权重

设计理念：
当前 StrategyManager 的 STRATEGY_RATIOS 是固定常量（balanced=0.7/0.3 等），
无论某个策略历史表现好坏，权重都不变。本模块引入"从经验学习"：

- ExperienceStore: 持久化每次策略执行结果（strategy/category/score/retained）
- ExperienceAnalyzer: 按 strategy/category 维度统计成功率、平均分、样本量
- AdaptiveWeights: 根据 stats 动态调整 STRATEGY_RATIOS
  - 成功率高的策略 → 权重增加（最多 +30%）
  - 成功率低的策略 → 权重降低（最多 -30%）
  - 用 EMA 平滑，避免单次结果剧烈波动
  - 样本不足（<5 次）时不调整，保持默认权重

集成到 StrategyManager / GEPEngine 后，系统从"固定策略比例"变为
"根据历史表现自适应调整策略比例"。
"""
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# ============================================================
# StrategyOutcome — 单次策略执行结果
# ============================================================

@dataclass
class StrategyOutcome:
    """单次策略执行结果

    Attributes:
        strategy: 使用的策略（balanced/innovate/harden/repair-only/...）
        category: 实际执行的类别（repair/optimize/innovate/explore）
        score: 验证评分 0-1
        retained: 是否固化成功
        timestamp: ISO 时间戳
        cycle: 进化循环编号
        signal_count: 触发信号数量
    """
    strategy: str
    category: str
    score: float
    retained: bool
    timestamp: str = ""
    cycle: int = 0
    signal_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "strategy": self.strategy,
            "category": self.category,
            "score": round(self.score, 4),
            "retained": self.retained,
            "timestamp": self.timestamp,
            "cycle": self.cycle,
            "signal_count": self.signal_count,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "StrategyOutcome":
        return cls(
            strategy=str(d.get("strategy", "balanced")),
            category=str(d.get("category", "repair")),
            score=float(d.get("score", 0.0)),
            retained=bool(d.get("retained", False)),
            timestamp=str(d.get("timestamp", "")),
            cycle=int(d.get("cycle", 0)),
            signal_count=int(d.get("signal_count", 0)),
        )


# ============================================================
# ExperienceStore — 持久化策略执行结果
# ============================================================

class ExperienceStore:
    """持久化策略执行结果（JSONL 格式，每行一条记录）

    与 memory.py 的 EvolutionHistory 互补：
    - EvolutionHistory 记录 phi/tier/domain 等进化状态
    - ExperienceStore 记录 strategy/category/score 等策略维度
    """

    def __init__(self, log_path: Path):
        self.log_path: Path = Path(log_path)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, outcome: StrategyOutcome) -> None:
        """记录一次策略执行结果"""
        if not outcome.timestamp:
            outcome.timestamp = datetime.now().isoformat()
        try:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(outcome.to_dict(), ensure_ascii=False) + "\n")
        except IOError:
            pass

    def recent(self, limit: int = 50) -> List[StrategyOutcome]:
        """读取最近的策略执行记录"""
        if not self.log_path.exists():
            return []
        try:
            lines = self.log_path.read_text(encoding="utf-8").strip().splitlines()
            records: List[StrategyOutcome] = []
            for line in lines[-limit:]:
                try:
                    records.append(StrategyOutcome.from_dict(json.loads(line)))
                except (json.JSONDecodeError, KeyError, ValueError):
                    continue
            return records
        except IOError:
            return []

    def query_by_strategy(self, strategy: str,
                          limit: int = 100) -> List[StrategyOutcome]:
        """查询某个策略的历史记录"""
        return [o for o in self.recent(limit) if o.strategy == strategy]

    def query_by_category(self, category: str,
                          limit: int = 100) -> List[StrategyOutcome]:
        """查询某个类别的历史记录"""
        return [o for o in self.recent(limit) if o.category == category]


# ============================================================
# StrategyStats — 单个策略的统计信息
# ============================================================

@dataclass
class StrategyStats:
    """单个策略的统计信息

    Attributes:
        strategy: 策略名
        attempts: 总尝试次数
        successes: 成功（retained=True）次数
        success_rate: 成功率 0-1
        avg_score: 平均评分 0-1
        recent_trend: 最近 5 次的平均分 - 历史 avg_score（正=上升，负=下降）
    """
    strategy: str
    attempts: int
    successes: int
    success_rate: float
    avg_score: float
    recent_trend: float = 0.0


# ============================================================
# ExperienceAnalyzer — 分析历史，输出策略统计
# ============================================================

class ExperienceAnalyzer:
    """分析策略执行历史，输出各策略的统计信息"""

    def __init__(self, store: ExperienceStore):
        self.store = store

    def analyze_strategy(self, strategy: str) -> StrategyStats:
        """分析单个策略"""
        records = self.store.query_by_strategy(strategy)
        if not records:
            return StrategyStats(
                strategy=strategy, attempts=0, successes=0,
                success_rate=0.0, avg_score=0.0,
            )
        attempts = len(records)
        successes = sum(1 for r in records if r.retained)
        avg_score = sum(r.score for r in records) / attempts
        success_rate = successes / attempts

        # 最近趋势：最近 5 次平均分 vs 整体平均分
        recent = records[-5:]
        recent_avg = sum(r.score for r in recent) / len(recent)
        trend = recent_avg - avg_score

        return StrategyStats(
            strategy=strategy,
            attempts=attempts,
            successes=successes,
            success_rate=success_rate,
            avg_score=avg_score,
            recent_trend=trend,
        )

    def analyze_all(self) -> Dict[str, StrategyStats]:
        """分析所有策略"""
        all_records = self.store.recent(1000)
        strategies = set(r.strategy for r in all_records)
        return {s: self.analyze_strategy(s) for s in strategies}

    def best_strategy(self, min_samples: int = 5) -> Optional[str]:
        """返回成功率最高的策略（样本不足返回 None）"""
        stats_all = self.analyze_all()
        qualified = {
            s: st for s, st in stats_all.items()
            if st.attempts >= min_samples
        }
        if not qualified:
            return None
        return max(qualified, key=lambda s: qualified[s].success_rate)

    def worst_strategy(self, min_samples: int = 5) -> Optional[str]:
        """返回成功率最低的策略（样本不足返回 None）"""
        stats_all = self.analyze_all()
        qualified = {
            s: st for s, st in stats_all.items()
            if st.attempts >= min_samples
        }
        if not qualified:
            return None
        return min(qualified, key=lambda s: qualified[s].success_rate)


# ============================================================
# AdaptiveWeights — 根据历史表现动态调整策略权重
# ============================================================

# 默认策略比例（与 StrategyManager.STRATEGY_RATIOS 保持一致）
DEFAULT_WEIGHTS: Dict[str, Tuple[float, float]] = {
    "balanced":        (0.7, 0.3),
    "innovate":        (0.3, 0.7),
    "harden":          (0.9, 0.1),
    "repair-only":     (1.0, 0.0),
    "early-stabilize": (0.8, 0.2),
    "steady-state":    (0.6, 0.4),
    "auto":            (0.7, 0.3),
}

# 调整幅度上限（避免权重剧烈波动）
MAX_ADJUSTMENT = 0.30  # 最多 ±30%
# 最小样本量（低于此数不调整）
MIN_SAMPLES_FOR_ADJUST = 5


class AdaptiveWeights:
    """根据历史表现动态调整策略权重

    调整规则：
    - success_rate > 0.7 → 权重 +（success_rate - 0.5）* MAX_ADJUSTMENT
    - success_rate < 0.3 → 权重 -（0.5 - success_rate）* MAX_ADJUSTMENT
    - 0.3 <= success_rate <= 0.7 → 不调整
    - 样本不足 → 保持默认
    - recent_trend 上升 → 额外 +5%，下降 → 额外 -5%

    调整后的 repair_ratio 会被 clamp 到 [0.1, 1.0]，
    innovate_ratio = 1 - repair_ratio。
    """

    def __init__(self, analyzer: ExperienceAnalyzer):
        self.analyzer = analyzer

    def adjusted_weights(self) -> Dict[str, Tuple[float, float]]:
        """返回调整后的所有策略权重"""
        stats_all = self.analyzer.analyze_all()
        result: Dict[str, Tuple[float, float]] = {}
        for strategy, default_ratio in DEFAULT_WEIGHTS.items():
            stats = stats_all.get(strategy)
            if stats is None or stats.attempts < MIN_SAMPLES_FOR_ADJUST:
                result[strategy] = default_ratio
                continue
            result[strategy] = self._adjust_one(strategy, default_ratio, stats)
        return result

    def adjusted_weight(self, strategy: str) -> Tuple[float, float]:
        """返回单个策略的调整后权重"""
        default = DEFAULT_WEIGHTS.get(strategy, (0.7, 0.3))
        stats = self.analyzer.analyze_strategy(strategy)
        if stats.attempts < MIN_SAMPLES_FOR_ADJUST:
            return default
        return self._adjust_one(strategy, default, stats)

    def _adjust_one(self, strategy: str,
                    default: Tuple[float, float],
                    stats: StrategyStats) -> Tuple[float, float]:
        """调整单个策略权重"""
        repair_ratio, _ = default

        # 基于成功率的调整
        if stats.success_rate > 0.7:
            # 成功率高 → 增加该策略的修复比例（更稳妥地利用优势）
            adjustment = (stats.success_rate - 0.5) * MAX_ADJUSTMENT
        elif stats.success_rate < 0.3:
            # 成功率低 → 降低修复比例（鼓励尝试创新）
            adjustment = -(0.5 - stats.success_rate) * MAX_ADJUSTMENT
        else:
            adjustment = 0.0

        # 基于趋势的微调
        if stats.recent_trend > 0.1:
            adjustment += 0.05
        elif stats.recent_trend < -0.1:
            adjustment -= 0.05

        # 应用调整并 clamp
        new_repair = repair_ratio + adjustment
        new_repair = max(0.1, min(1.0, new_repair))
        new_innovate = 1.0 - new_repair
        return (round(new_repair, 4), round(new_innovate, 4))

    def adjustment_report(self) -> List[Dict[str, Any]]:
        """生成权重调整报告（用于调试/监控）"""
        stats_all = self.analyzer.analyze_all()
        report: List[Dict[str, Any]] = []
        for strategy, default in DEFAULT_WEIGHTS.items():
            stats = stats_all.get(strategy)
            if stats is None:
                report.append({
                    "strategy": strategy,
                    "default": default,
                    "adjusted": default,
                    "reason": "no_data",
                    "attempts": 0,
                })
                continue
            adjusted = self._adjust_one(strategy, default, stats)
            if stats.attempts < MIN_SAMPLES_FOR_ADJUST:
                reason = "insufficient_samples"
            elif adjusted == default:
                reason = "no_adjustment_needed"
            else:
                reason = "adjusted"
            report.append({
                "strategy": strategy,
                "default": default,
                "adjusted": adjusted,
                "reason": reason,
                "attempts": stats.attempts,
                "success_rate": round(stats.success_rate, 3),
                "avg_score": round(stats.avg_score, 3),
                "recent_trend": round(stats.recent_trend, 3),
            })
        return report


# ============================================================
# ExperienceLearner — 统一入口，整合 store + analyzer + weights
# ============================================================

class ExperienceLearner:
    """经验学习统一入口

    整合 ExperienceStore + ExperienceAnalyzer + AdaptiveWeights，
    提供 record / analyze / adjust_weights / report 四个核心方法。

    用法：
        learner = ExperienceLearner(logs_dir / "experience.jsonl")
        # 记录策略结果
        learner.record(StrategyOutcome(strategy="balanced", category="repair",
                                        score=0.8, retained=True, cycle=1))
        # 获取调整后权重
        weights = learner.adjusted_weights()
        # 生成报告
        report = learner.report()
    """

    def __init__(self, log_path: Path):
        self.store = ExperienceStore(log_path)
        self.analyzer = ExperienceAnalyzer(self.store)
        self.weights = AdaptiveWeights(self.analyzer)

    def record(self, outcome: StrategyOutcome) -> None:
        """记录一次策略执行结果"""
        self.store.record(outcome)

    def adjusted_weights(self) -> Dict[str, Tuple[float, float]]:
        """返回调整后的所有策略权重"""
        return self.weights.adjusted_weights()

    def adjusted_weight(self, strategy: str) -> Tuple[float, float]:
        """返回单个策略的调整后权重"""
        return self.weights.adjusted_weight(strategy)

    def analyze_all(self) -> Dict[str, StrategyStats]:
        """返回所有策略的统计信息"""
        return self.analyzer.analyze_all()

    def best_strategy(self, min_samples: int = 5) -> Optional[str]:
        """返回成功率最高的策略"""
        return self.analyzer.best_strategy(min_samples)

    def report(self) -> List[Dict[str, Any]]:
        """生成权重调整报告"""
        return self.weights.adjustment_report()

    def recent_outcomes(self, limit: int = 10) -> List[StrategyOutcome]:
        """返回最近的策略执行结果"""
        return self.store.recent(limit)
