"""
superclaw 内在动机/好奇心机制 — 让系统主动探索未知领域

设计理念：
当前 GEP engine 的策略选择是固定比例 + random.random()，explore 只是无信号时的
随机兜底。本模块引入"内在动机"（intrinsic motivation），让 superclaw 能：
- 评估信号/任务/领域是否"新颖"（NoveltyScorer）
- 跟踪对重复任务的"厌倦度"（BoredomTracker）
- 整合 novelty + boredom 生成探索奖励（CuriosityDrive）
- 主动发现探索目标并转成 GEP engine 能理解的 Signal（CuriosityDrivenExplorer）

集成到 StrategyManager / GEPEngine 后，系统从"被动响应信号"变为"主动探索未知"。
"""
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

from .gep_schema import Signal


# ============================================================
# NoveltyScorer — 评估信号/任务/领域的新颖度
# ============================================================

class NoveltyScorer:
    """评估一个信号/任务/领域是否"新颖"

    持久化到 JSON：{signal: {item: count}, domain: {item: count}, task: {item: count}}

    评分规则：
    - 从未见过的 item → 1.0（完全新颖）
    - 见过 1 次 → 0.8
    - 见过 N 次（N>=2）→ 1.0 / (1.0 + log(N+1))（对数衰减）
    """

    def __init__(self, history_path: Path):
        self.history_path: Path = Path(history_path)
        self._counts: Dict[str, Dict[str, int]] = {
            "signal": {},
            "domain": {},
            "task": {},
        }
        self._load()

    def _load(self) -> None:
        """从 JSON 加载历史记录（文件不存在/损坏则保持空）"""
        if not self.history_path.exists():
            return
        try:
            data = json.loads(self.history_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, IOError):
            return
        if not isinstance(data, dict):
            return
        for item_type in self._counts:
            bucket = data.get(item_type)
            if isinstance(bucket, dict):
                self._counts[item_type] = {
                    str(k): int(v) for k, v in bucket.items()
                }

    def _save(self) -> None:
        """持久化到 JSON"""
        try:
            self.history_path.parent.mkdir(parents=True, exist_ok=True)
            self.history_path.write_text(
                json.dumps(self._counts, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except IOError:
            pass

    def score(self, item: str, item_type: str = "signal") -> float:
        """返回 0-1 的新颖度评分"""
        counts = self._counts.get(item_type, {})
        n = counts.get(item, 0)
        if n == 0:
            return 1.0
        if n == 1:
            return 0.8
        return 1.0 / (1.0 + math.log(n + 1))

    def record(self, item: str, item_type: str = "signal") -> None:
        """记录遇到过的 item（计数 +1，自动持久化）"""
        if item_type not in self._counts:
            self._counts[item_type] = {}
        bucket = self._counts[item_type]
        bucket[item] = bucket.get(item, 0) + 1
        self._save()

    def novelty_bonus(self, items: List[str]) -> float:
        """一组 item 的整体新颖度（取 max，空列表 → 0.0）"""
        if not items:
            return 0.0
        return max(self.score(it, "signal") for it in items)


# ============================================================
# BoredomTracker — 跟踪对重复任务的"厌倦度"
# ============================================================

class BoredomTracker:
    """跟踪对重复任务的"厌倦度"（内存状态）

    boredom_level(task_type):
    - 最近 10 次都是同一类 → 0.9（很无聊）
    - 最近 10 次都是不同类 → 0.0（不无聊）
    """

    def __init__(self):
        self._history: List[str] = []

    def record(self, task_type: str) -> None:
        """记录执行了某类任务"""
        self._history.append(task_type)
        # 限制历史长度避免无限增长
        if len(self._history) > 100:
            self._history = self._history[-100:]

    def boredom_level(self, task_type: str) -> float:
        """返回 0-1 厌倦度（基于最近 10 次中该类型的占比）"""
        recent = self._history[-10:]
        if not recent:
            return 0.0
        count = sum(1 for t in recent if t == task_type)
        return 0.9 * count / len(recent)

    def max_recent_boredom(self) -> float:
        """返回最近历史中所有任务类型的最大厌倦度"""
        recent = self._history[-10:]
        if not recent:
            return 0.0
        return max(self.boredom_level(t) for t in set(recent))

    def suggest_alternative(self, recent_tasks: List[str]) -> str:
        """建议换个领域（返回最近最少做的任务类型）

        在候选任务中选最近历史中出现次数最少的；并列时选最久没做的。
        """
        if not recent_tasks:
            return ""
        recent = self._history[-10:]
        counts: Dict[str, int] = {t: 0 for t in recent_tasks}
        last_seen: Dict[str, int] = {t: -1 for t in recent_tasks}
        for i, t in enumerate(recent):
            if t in counts:
                counts[t] += 1
                last_seen[t] = i
        return min(recent_tasks, key=lambda t: (counts[t], last_seen[t]))


# ============================================================
# CuriosityDrive — 整合 novelty + boredom，生成探索奖励
# ============================================================

class CuriosityDrive:
    """整合 novelty + boredom，生成探索奖励

    intrinsic_reward = novelty_score * 0.6 + (1 - boredom_level) * 0.4
    - 高 novelty + 低 boredom → 高奖励（值得探索）
    - 低 novelty + 高 boredom → 低奖励（无聊且重复）
    """

    def __init__(self, novelty_scorer: NoveltyScorer, boredom: BoredomTracker):
        self.novelty_scorer = novelty_scorer
        self.boredom = boredom

    def intrinsic_reward(self, item: str, item_type: str = "signal") -> float:
        """计算内在奖励（0-1）"""
        novelty = self.novelty_scorer.score(item, item_type)
        boredom = self.boredom.boredom_level(item_type)
        return novelty * 0.6 + (1.0 - boredom) * 0.4

    def should_explore(self, current_signals: List[str]) -> bool:
        """是否应该探索

        - 当前信号整体新颖度 > 0.6 → True（值得探索）
        - boredom > 0.7 → True（太无聊了，换换）
        """
        if current_signals:
            if self.novelty_scorer.novelty_bonus(current_signals) > 0.6:
                return True
        if self.boredom.max_recent_boredom() > 0.7:
            return True
        return False

    def suggest_exploration_target(self, known_domains: List[str],
                                    all_possible_domains: List[str]) -> str:
        """建议探索目标

        - 从 all_possible_domains 中选 known_domains 没覆盖的、novelty 最高的
        - 如果都覆盖了，选 boredom 最低的
        """
        known_set = set(known_domains)
        gaps = [d for d in all_possible_domains if d not in known_set]
        if gaps:
            return max(gaps, key=lambda d: self.novelty_scorer.score(d, "domain"))
        if not all_possible_domains:
            return ""
        return min(
            all_possible_domains,
            key=lambda d: self.boredom.boredom_level(d),
        )


# ============================================================
# ExplorationGoal — 探索目标 dataclass
# ============================================================

@dataclass
class ExplorationGoal:
    """探索目标

    Attributes:
        target_domain: 目标领域
        reason: 探索原因（"novelty" / "boredom" / "gap"）
        novelty_score: 新颖度评分 0-1
        expected_reward: 预期内在奖励 0-1
        suggested_action: 建议行动（"github_search" / "code_generate" / "skill_load"）
    """
    target_domain: str
    reason: str
    novelty_score: float
    expected_reward: float
    suggested_action: str


# ============================================================
# CuriosityDrivenExplorer — 主动探索引擎
# ============================================================

# 默认候选能力领域（LLM 不可用时兜底）
_DEFAULT_CANDIDATE_DOMAINS: List[str] = [
    "weather_query", "fetch_url", "parse_json",
    "image_recognition", "speech_to_text", "translation",
    "data_analysis", "web_scraping", "email_send", "calendar",
]


class CuriosityDrivenExplorer:
    """主动探索引擎

    分析当前能力清单 vs 可能的能力领域，用 curiosity 评分排序，返回 top 3 探索目标。
    """

    def __init__(self, curiosity: CuriosityDrive, capability_registry: Any,
                 llm_router: Any = None):
        self.curiosity = curiosity
        self.capability_registry = capability_registry
        self.llm_router = llm_router

    def discover_targets(self, current_state: Dict) -> List[ExplorationGoal]:
        """发现探索目标

        Args:
            current_state: {"known_domains": [...], "all_possible_domains": [...]}

        Returns:
            按 expected_reward 降序的 top 3 ExplorationGoal
        """
        known_domains: List[str] = list(current_state.get("known_domains", []))
        all_possible = current_state.get("all_possible_domains")
        if not all_possible:
            all_possible = self._suggest_domains_via_llm(known_domains)
        if not all_possible:
            return []

        known_set = set(known_domains)
        gaps = [d for d in all_possible if d not in known_set]

        goals: List[ExplorationGoal] = []
        if gaps:
            # 有未覆盖领域 → gap 类型
            for domain in gaps:
                novelty = self.curiosity.novelty_scorer.score(domain, "domain")
                reward = self.curiosity.intrinsic_reward(domain, "domain")
                goals.append(ExplorationGoal(
                    target_domain=domain,
                    reason="gap",
                    novelty_score=novelty,
                    expected_reward=reward,
                    suggested_action=self._suggest_action(domain),
                ))
        else:
            # 全覆盖 → 选 boredom 最低 / novelty 高的
            for domain in all_possible:
                boredom = self.curiosity.boredom.boredom_level(domain)
                novelty = self.curiosity.novelty_scorer.score(domain, "domain")
                reward = self.curiosity.intrinsic_reward(domain, "domain")
                reason = "boredom" if boredom < 0.5 else "novelty"
                goals.append(ExplorationGoal(
                    target_domain=domain,
                    reason=reason,
                    novelty_score=novelty,
                    expected_reward=reward,
                    suggested_action=self._suggest_action(domain),
                ))

        goals.sort(key=lambda g: g.expected_reward, reverse=True)
        return goals[:3]

    def generate_curiosity_signal(self, target: ExplorationGoal) -> Signal:
        """把探索目标转成 GEP engine 能理解的 Signal"""
        return Signal(
            signal_type="curiosity",
            source="curiosity:explorer",
            severity="info",
            pattern=(
                f"探索目标: {target.target_domain} "
                f"(原因: {target.reason}, 新颖度: {target.novelty_score:.2f})"
            ),
            context=(
                f"expected_reward={target.expected_reward:.2f}, "
                f"action={target.suggested_action}"
            ),
        )

    def _suggest_domains_via_llm(self, known_domains: List[str]) -> List[str]:
        """用 LLM 生成可能值得探索的领域（失败/无 LLM → 默认候选集）"""
        if self.llm_router is None:
            return list(_DEFAULT_CANDIDATE_DOMAINS)
        try:
            prompt = (
                f"已知能力: {known_domains}\n\n"
                "请列出 5 个可能值得探索的新能力领域，每行一个，只输出领域名。"
            )
            result = self.llm_router.complete(
                [{"role": "user", "content": prompt}],
                complexity="low",
            )
            if getattr(result, "error", None) or not getattr(result, "content", ""):
                return list(_DEFAULT_CANDIDATE_DOMAINS)
            domains: List[str] = []
            for line in result.content.strip().splitlines():
                cleaned = line.strip().lstrip("-*0123456789. ").strip()
                if cleaned:
                    domains.append(cleaned)
            return domains[:5] if domains else list(_DEFAULT_CANDIDATE_DOMAINS)
        except Exception:
            return list(_DEFAULT_CANDIDATE_DOMAINS)

    def _suggest_action(self, domain: str) -> str:
        """根据领域名推断建议行动"""
        lowered = domain.lower()
        if any(kw in lowered for kw in ("skill", "load")):
            return "skill_load"
        if any(kw in lowered for kw in ("search", "github", "fetch", "scrape")):
            return "github_search"
        return "code_generate"
