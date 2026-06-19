"""
superclaw 从用户反馈学习 — 把用户反馈转化为进化信号

设计理念：
当前 GEP engine 的信号来源只有反思日志和进化历史（都是系统内部产生），
用户的真实反馈（"这个回答错了"、"希望能加个 xxx 功能"、"报错了"）没有被
纳入进化循环。本模块引入"从用户反馈学习"：

- Feedback: 结构化用户反馈（type/content/sentiment/target）
- FeedbackStore: 持久化反馈（JSONL），支持 record/recent/query
- FeedbackAnalyzer: 分析反馈，统计正负面比例、高频问题、改进建议
- FeedbackSignalConverter: 把反馈转成 GEP Signal
  - negative/bug → error 信号（驱动修复）
  - suggestion → feature 信号（驱动创新）
  - positive → performance 信号（正面强化）
- FeedbackDetector: 从用户消息中自动检测反馈类型和情感

集成到 SignalExtractor / GEPEngine 后，用户反馈成为进化信号的真实来源，
系统从"只听自己"变为"也听用户"。
"""
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .gep_schema import Signal


# ============================================================
# Feedback — 结构化用户反馈
# ============================================================

# 反馈类型
FEEDBACK_POSITIVE = "positive"      # 正面反馈
FEEDBACK_NEGATIVE = "negative"      # 负面反馈
FEEDBACK_SUGGESTION = "suggestion"  # 改进建议
FEEDBACK_BUG = "bug"                # bug 报告

VALID_TYPES = {FEEDBACK_POSITIVE, FEEDBACK_NEGATIVE, FEEDBACK_SUGGESTION, FEEDBACK_BUG}


@dataclass
class Feedback:
    """单条用户反馈

    Attributes:
        feedback_type: positive/negative/suggestion/bug
        content: 反馈内容
        sentiment_score: 情感分 -1.0（极负面）到 +1.0（极正面），0 中性
        timestamp: ISO 时间戳
        session_id: 会话 ID
        target: 反馈针对的对象（工具名/能力名/空=整体）
    """
    feedback_type: str
    content: str
    sentiment_score: float = 0.0
    timestamp: str = ""
    session_id: str = ""
    target: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "feedback_type": self.feedback_type,
            "content": self.content,
            "sentiment_score": round(self.sentiment_score, 4),
            "timestamp": self.timestamp,
            "session_id": self.session_id,
            "target": self.target,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Feedback":
        return cls(
            feedback_type=str(d.get("feedback_type", "negative")),
            content=str(d.get("content", "")),
            sentiment_score=float(d.get("sentiment_score", 0.0)),
            timestamp=str(d.get("timestamp", "")),
            session_id=str(d.get("session_id", "")),
            target=str(d.get("target", "")),
        )


# ============================================================
# FeedbackDetector — 从用户消息中自动检测反馈
# ============================================================

# 反馈检测关键词（按优先级：bug > negative > suggestion > positive）
_BUG_KEYWORDS = [
    "bug", "崩溃", "crash", "异常", "exception", "traceback",
    "报错", "error", "失败", "fail", "无法", "不能工作",
]
_NEGATIVE_KEYWORDS = [
    "错了", "不对", "不正确", "不行", "糟糕", "坏", "差",
    "没有用", "没用", "失望", "不好", "有问题", "错了",
]
_SUGGESTION_KEYWORDS = [
    "建议", "希望", "能否", "能不能", "可以", "应该",
    "如果", "就好了", "想要", "需要", "缺少", "缺",
]
_POSITIVE_KEYWORDS = [
    "很好", "不错", "棒", "赞", "谢谢", "完美", "厉害",
    "好的", "对了", "正确", "有用", "感谢", "太好了",
]

# 否定前缀 — 出现在关键词前 N 字符内表示否定该关键词
# 例如 "not a bug"、"不是 bug"、"没有问题"、"不希望"
# 英文否定词需要前后看空格/词边界，中文直接前缀匹配
_NEGATION_WORDS_EN = ["not", "no", "without", "isn't", "wasn't",
                      "aren't", "weren't", "don't", "doesn't",
                      "didn't", "won't", "can't", "cannot", "never"]
# 中文否定词（直接前缀匹配，不需要空格）
_NEGATION_WORDS_ZH = ["不", "没", "无", "非", "别", "勿", "未"]
# 否定窗口：关键词前多少字符内检测否定词
_NEGATION_WINDOW = 12


def _is_negated(message_lower: str, kw_lower: str, kw_pos: int) -> bool:
    """检测关键词是否被否定

    在关键词前 _NEGATION_WINDOW 字符内查找否定词。
    英文否定词需词边界，中文直接子串匹配。

    Args:
        message_lower: 已小写的完整消息
        kw_lower: 已小写的关键词
        kw_pos: 关键词在消息中的起始位置

    Returns:
        True 如果关键词被否定
    """
    window_start = max(0, kw_pos - _NEGATION_WINDOW)
    window = message_lower[window_start:kw_pos]
    # 中文否定词直接子串匹配
    for neg in _NEGATION_WORDS_ZH:
        if neg in window:
            return True
    # 英文否定词需词边界（前后是空格或非字母）
    for neg in _NEGATION_WORDS_EN:
        idx = window.rfind(neg)
        if idx >= 0:
            # 检查否定词后是否紧跟非字母字符（词边界）
            after_idx = idx + len(neg)
            if after_idx >= len(window) or not window[after_idx].isalpha():
                return True
    return False


def _find_keyword(message_lower: str, keywords: List[str]) -> Optional[tuple]:
    """查找消息中第一个未被否定的关键词

    Returns:
        (keyword, position) 或 None
    """
    for kw in keywords:
        pos = message_lower.find(kw)
        while pos >= 0:
            if not _is_negated(message_lower, kw, pos):
                return (kw, pos)
            # 被否定，找下一个出现位置
            pos = message_lower.find(kw, pos + 1)
    return None


class FeedbackDetector:
    """从用户消息中自动检测反馈类型和情感

    检测规则（按优先级）：
    1. 包含未被否定的 bug 关键词 → bug 类型，sentiment=-0.8
    2. 包含未被否定的 negative 关键词 → negative 类型，sentiment=-0.6
    3. 包含未被否定的 suggestion 关键词 → suggestion 类型，sentiment=0.0
    4. 包含未被否定的 positive 关键词 → positive 类型，sentiment=+0.8
    5. 都不匹配或全被否定 → None（不是反馈）

    否定检测：
    - "not a bug" / "不是 bug" / "没有问题" → 关键词被否定，不触发该类型
    - "不希望" → suggestion 被否定，降级处理
    - 窗口内多个否定词只算一次否定
    """

    def detect(self, message: str) -> Optional[Feedback]:
        """检测用户消息是否包含反馈，返回 Feedback 或 None"""
        if not message or not message.strip():
            return None

        lowered = message.lower()

        # 按优先级检测（带否定检测）
        if _find_keyword(lowered, _BUG_KEYWORDS):
            return Feedback(
                feedback_type=FEEDBACK_BUG,
                content=message.strip(),
                sentiment_score=-0.8,
            )

        if _find_keyword(lowered, _NEGATIVE_KEYWORDS):
            return Feedback(
                feedback_type=FEEDBACK_NEGATIVE,
                content=message.strip(),
                sentiment_score=-0.6,
            )

        if _find_keyword(lowered, _SUGGESTION_KEYWORDS):
            return Feedback(
                feedback_type=FEEDBACK_SUGGESTION,
                content=message.strip(),
                sentiment_score=0.0,
            )

        if _find_keyword(lowered, _POSITIVE_KEYWORDS):
            return Feedback(
                feedback_type=FEEDBACK_POSITIVE,
                content=message.strip(),
                sentiment_score=0.8,
            )

        return None

    def detect_with_context(self, message: str,
                            session_id: str = "",
                            target: str = "") -> Optional[Feedback]:
        """检测反馈并填充上下文信息"""
        fb = self.detect(message)
        if fb is None:
            return None
        fb.session_id = session_id
        fb.target = target
        return fb


# ============================================================
# FeedbackStore — 持久化用户反馈
# ============================================================

class FeedbackStore:
    """持久化用户反馈（JSONL 格式）"""

    def __init__(self, log_path: Path):
        self.log_path: Path = Path(log_path)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, feedback: Feedback) -> None:
        """记录一条用户反馈"""
        if not feedback.timestamp:
            feedback.timestamp = datetime.now().isoformat()
        try:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(feedback.to_dict(), ensure_ascii=False) + "\n")
        except IOError:
            pass

    def recent(self, limit: int = 50) -> List[Feedback]:
        """读取最近的反馈"""
        if not self.log_path.exists():
            return []
        try:
            lines = self.log_path.read_text(encoding="utf-8").strip().splitlines()
            records: List[Feedback] = []
            for line in lines[-limit:]:
                try:
                    records.append(Feedback.from_dict(json.loads(line)))
                except (json.JSONDecodeError, KeyError, ValueError):
                    continue
            return records
        except IOError:
            return []

    def query_by_type(self, feedback_type: str,
                      limit: int = 100) -> List[Feedback]:
        """按类型查询反馈"""
        return [f for f in self.recent(limit) if f.feedback_type == feedback_type]

    def query_by_target(self, target: str,
                        limit: int = 100) -> List[Feedback]:
        """按目标查询反馈"""
        return [f for f in self.recent(limit) if f.target == target]

    def query_negative(self, limit: int = 100) -> List[Feedback]:
        """查询所有负面反馈（negative + bug）"""
        return [
            f for f in self.recent(limit)
            if f.feedback_type in (FEEDBACK_NEGATIVE, FEEDBACK_BUG)
        ]


# ============================================================
# FeedbackStats — 反馈统计信息
# ============================================================

@dataclass
class FeedbackStats:
    """反馈统计信息

    Attributes:
        total: 总反馈数
        positive: 正面反馈数
        negative: 负面反馈数（含 bug）
        suggestion: 建议数
        bug: bug 报告数
        avg_sentiment: 平均情感分
        top_targets: 反馈最多的目标（top 5）
    """
    total: int
    positive: int
    negative: int
    suggestion: int
    bug: int
    avg_sentiment: float
    top_targets: List[str]


# ============================================================
# FeedbackAnalyzer — 分析反馈
# ============================================================

class FeedbackAnalyzer:
    """分析用户反馈，输出统计信息"""

    def __init__(self, store: FeedbackStore):
        self.store = store

    def stats(self) -> FeedbackStats:
        """生成反馈统计"""
        records = self.store.recent(1000)
        if not records:
            return FeedbackStats(
                total=0, positive=0, negative=0,
                suggestion=0, bug=0, avg_sentiment=0.0,
                top_targets=[],
            )

        positive = sum(1 for f in records if f.feedback_type == FEEDBACK_POSITIVE)
        negative = sum(1 for f in records if f.feedback_type == FEEDBACK_NEGATIVE)
        suggestion = sum(1 for f in records if f.feedback_type == FEEDBACK_SUGGESTION)
        bug = sum(1 for f in records if f.feedback_type == FEEDBACK_BUG)
        avg_sentiment = sum(f.sentiment_score for f in records) / len(records)

        # 统计 target 频率
        target_counts: Dict[str, int] = {}
        for f in records:
            if f.target:
                target_counts[f.target] = target_counts.get(f.target, 0) + 1
        top_targets = sorted(target_counts, key=lambda t: -target_counts[t])[:5]

        return FeedbackStats(
            total=len(records),
            positive=positive,
            negative=negative,
            suggestion=suggestion,
            bug=bug,
            avg_sentiment=round(avg_sentiment, 3),
            top_targets=top_targets,
        )

    def critical_issues(self, limit: int = 5) -> List[Feedback]:
        """返回最需要关注的问题（bug + 负面，按 sentiment 升序）"""
        negative = self.store.query_negative(limit * 2)
        negative.sort(key=lambda f: f.sentiment_score)
        return negative[:limit]

    def top_suggestions(self, limit: int = 5) -> List[Feedback]:
        """返回最值得实现的建议"""
        suggestions = self.store.query_by_type(FEEDBACK_SUGGESTION, limit * 2)
        return suggestions[-limit:] if len(suggestions) > limit else suggestions

    def satisfaction_rate(self) -> float:
        """满意度 = positive / total（无数据返回 0）"""
        records = self.store.recent(1000)
        if not records:
            return 0.0
        positive = sum(1 for f in records if f.feedback_type == FEEDBACK_POSITIVE)
        return positive / len(records)


# ============================================================
# FeedbackSignalConverter — 把反馈转成 GEP Signal
# ============================================================

class FeedbackSignalConverter:
    """把用户反馈转成 GEP engine 能理解的 Signal

    转换规则：
    - bug → Signal(signal_type="error", severity="critical")
    - negative → Signal(signal_type="error", severity="high")
    - suggestion → Signal(signal_type="feature", severity="medium")
    - positive → Signal(signal_type="performance", severity="low")
    """

    def convert(self, feedback: Feedback) -> Signal:
        """把单条反馈转成 Signal"""
        if feedback.feedback_type == FEEDBACK_BUG:
            return Signal(
                signal_type="error",
                source="feedback:bug",
                severity="critical",
                pattern=f"用户报告 bug: {feedback.content[:100]}",
                context=f"target={feedback.target}, sentiment={feedback.sentiment_score}",
            )

        if feedback.feedback_type == FEEDBACK_NEGATIVE:
            return Signal(
                signal_type="error",
                source="feedback:negative",
                severity="high",
                pattern=f"用户负面反馈: {feedback.content[:100]}",
                context=f"target={feedback.target}, sentiment={feedback.sentiment_score}",
            )

        if feedback.feedback_type == FEEDBACK_SUGGESTION:
            return Signal(
                signal_type="feature",
                source="feedback:suggestion",
                severity="medium",
                pattern=f"用户建议: {feedback.content[:100]}",
                context=f"target={feedback.target}",
            )

        # positive
        return Signal(
            signal_type="performance",
            source="feedback:positive",
            severity="low",
            pattern=f"用户正面反馈: {feedback.content[:100]}",
            context=f"target={feedback.target}",
        )

    def convert_batch(self, feedbacks: List[Feedback]) -> List[Signal]:
        """批量转换反馈为 Signal"""
        return [self.convert(f) for f in feedbacks]


# ============================================================
# FeedbackLearner — 统一入口
# ============================================================

class FeedbackLearner:
    """从用户反馈学习统一入口

    整合 FeedbackDetector + FeedbackStore + FeedbackAnalyzer +
    FeedbackSignalConverter，提供 detect_and_record / analyze /
    to_signals / report 四个核心方法。

    用法：
        learner = FeedbackLearner(logs_dir / "feedback.jsonl")
        # 自动检测并记录用户反馈
        learner.detect_and_record("这个回答错了", session_id="s1")
        # 分析反馈
        stats = learner.analyze()
        # 转成进化信号
        signals = learner.to_signals(limit=5)
    """

    def __init__(self, log_path: Path):
        self.detector = FeedbackDetector()
        self.store = FeedbackStore(log_path)
        self.analyzer = FeedbackAnalyzer(self.store)
        self.converter = FeedbackSignalConverter()

    def detect_and_record(self, message: str,
                          session_id: str = "",
                          target: str = "") -> Optional[Feedback]:
        """检测用户消息是否包含反馈，是则记录并返回 Feedback"""
        fb = self.detector.detect_with_context(message, session_id, target)
        if fb is not None:
            self.store.record(fb)
        return fb

    def record(self, feedback: Feedback) -> None:
        """直接记录一条反馈（不经过自动检测）"""
        self.store.record(feedback)

    def analyze(self) -> FeedbackStats:
        """返回反馈统计"""
        return self.analyzer.stats()

    def to_signals(self, limit: int = 10) -> List[Signal]:
        """把最近的负面反馈和建议转成进化信号

        优先级：bug > negative > suggestion（positive 不转信号）
        """
        signals: List[Signal] = []

        # bug 信号（最高优先级）
        bugs = self.store.query_by_type(FEEDBACK_BUG, limit)
        signals.extend(self.converter.convert_batch(bugs))

        # 负面信号
        negatives = self.store.query_by_type(FEEDBACK_NEGATIVE, limit)
        signals.extend(self.converter.convert_batch(negatives))

        # 建议信号
        suggestions = self.store.query_by_type(FEEDBACK_SUGGESTION, limit)
        signals.extend(self.converter.convert_batch(suggestions))

        return signals[:limit]

    def critical_signals(self, limit: int = 5) -> List[Signal]:
        """只返回 critical/high 严重度的信号（bug + negative）"""
        critical_feedback = self.analyzer.critical_issues(limit)
        return self.converter.convert_batch(critical_feedback)

    def report(self) -> Dict[str, Any]:
        """生成反馈分析报告"""
        stats = self.analyze()
        return {
            "total": stats.total,
            "positive": stats.positive,
            "negative": stats.negative,
            "suggestion": stats.suggestion,
            "bug": stats.bug,
            "avg_sentiment": stats.avg_sentiment,
            "satisfaction_rate": round(self.analyzer.satisfaction_rate(), 3),
            "top_targets": stats.top_targets,
            "critical_count": len(self.analyzer.critical_issues()),
        }

    def recent_feedbacks(self, limit: int = 10) -> List[Feedback]:
        """返回最近的反馈"""
        return self.store.recent(limit)
