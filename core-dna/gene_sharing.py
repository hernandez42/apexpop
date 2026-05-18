#!/usr/bin/env python3
"""
基因共享协议（GEP）— 完整实现
支持：PublishGenome / PullGenome / MergeGenome / ScoreReport
支持：本地私有基因库 + 团队公共基因库
"""

import json
import os
import time
import copy
import hashlib
from pathlib import Path
from typing import Optional, List, Dict, Any

# ===================== 路径配置 =====================

WORKSPACE = Path("/home/.openclaw/workspace")
CORE_DIR = WORKSPACE / "core-dna"
PRIVATE_STORE = CORE_DIR / "genome_private.json"   # 本地私有基因库
PUBLIC_STORE = CORE_DIR / "genome_public.json"      # 团队公共基因库
SCORE_LOG = CORE_DIR / "genome_scores.jsonl"        # 评分日志（追加写入）

# ===================== 底层存储 =====================

def _load_json(path: Path) -> list:
    """加载 JSON 文件，不存在或格式错误返回空列表"""
    if not path.exists():
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, IOError):
        return []

def _save_json(path: Path, data: list):
    """保存 JSON 文件，自动备份旧版本"""
    if path.exists():
        backup = path.with_suffix(".bak")
        try:
            backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        except IOError:
            pass
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def _append_score_log(entry: dict):
    """追加评分记录到 JSONL 日志"""
    with open(SCORE_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

def _genome_id(genome: dict) -> str:
    """生成 Genome 的唯一 ID（基于内容哈希）"""
    content = json.dumps(genome, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(content.encode()).hexdigest()[:16]

# ===================== 核心协议 =====================

class GeneSharingProtocol:
    """GEP 基因共享协议 — 管理私有和公共基因库"""

    def __init__(self, private_path: Path = PRIVATE_STORE, public_path: Path = PUBLIC_STORE):
        self.private_path = private_path
        self.public_path = public_path

    # ---- PublishGenome: 发布到私有或公共库 ----

    def publish(self, genome: dict, visibility: str = "private",
                tags: Optional[List[str]] = None) -> dict:
        """
        发布 Genome
        visibility: "private" | "public"
        tags: 标签列表，便于分类检索
        """
        item = copy.deepcopy(genome)
        item["id"] = _genome_id(item)
        item["published_at"] = int(time.time())
        item["visibility"] = visibility
        item["tags"] = tags or []
        item["use_count"] = 0
        item["score_history"] = []

        # 写入对应库
        if visibility == "public":
            pool = _load_json(self.public_path)
            # 去重：同 ID 不重复添加
            pool = [g for g in pool if g.get("id") != item["id"]]
            pool.append(item)
            _save_json(self.public_path, pool)
        else:
            pool = _load_json(self.private_path)
            pool = [g for g in pool if g.get("id") != item["id"]]
            pool.append(item)
            _save_json(self.private_path, pool)

        return item

    # ---- PullGenome: 拉取基因 ----

    def pull(self, source: str = "private", model: Optional[str] = None,
             tags: Optional[List[str]] = None, top_k: int = 5,
             min_score: float = 0.0) -> List[dict]:
        """
        拉取基因列表
        source: "private" | "public" | "all"
        model: 按模型过滤
        tags: 按标签过滤
        min_score: 最低分数
        """
        pool = []
        if source in ("private", "all"):
            pool.extend(_load_json(self.private_path))
        if source in ("public", "all"):
            pool.extend(_load_json(self.public_path))

        # 过滤
        if model:
            pool = [g for g in pool if g.get("model") == model]
        if tags:
            pool = [g for g in pool if any(t in g.get("tags", []) for t in tags)]
        pool = [g for g in pool if g.get("score", 0) >= min_score]

        # 按分数排序
        pool.sort(key=lambda x: x.get("score", 0), reverse=True)
        return pool[:top_k]

    def pull_by_id(self, genome_id: str, source: str = "all") -> Optional[dict]:
        """按 ID 精确拉取"""
        pool = []
        if source in ("private", "all"):
            pool.extend(_load_json(self.private_path))
        if source in ("public", "all"):
            pool.extend(_load_json(self.public_path))
        for g in pool:
            if g.get("id") == genome_id:
                return g
        return None

    # ---- MergeGenome: 合并两个基因 ----

    def merge(self, base: dict, other: dict, strategy: str = "auto") -> dict:
        """
        合并两个 Genome
        strategy:
          "auto"     — 分数高的策略优先
          "blend"    — 交叉混合（工具/技能取并集）
          "prefer_high" — 高分者完全覆盖
        """
        base_score = base.get("score", 0)
        other_score = other.get("score", 0)

        if strategy == "prefer_high":
            winner = base if base_score >= other_score else other
            return copy.deepcopy(winner)

        # auto / blend 策略：交叉合并
        if strategy == "auto":
            high = base if base_score >= other_score else other
            low = other if base_score >= other_score else base
        else:
            high, low = base, other

        merged = {
            "prompt_strategy": high.get("prompt_strategy", low.get("prompt_strategy", "")),
            "toolchain": sorted(set(
                base.get("toolchain", []) + other.get("toolchain", [])
            )),
            "skills": sorted(set(
                base.get("skills", []) + other.get("skills", [])
            )),
            "model": high.get("model", low.get("model", "")),
            "score": max(base_score, other_score),
            "tags": sorted(set(
                base.get("tags", []) + other.get("tags", [])
            )),
            "merged_from": [base.get("id", "?"), other.get("id", "?")],
        }
        return merged

    def merge_pool(self, count: int = 3) -> Optional[dict]:
        """从公共库中取 top N 合并成一个超级基因"""
        pool = self.pull(source="public", top_k=count)
        if len(pool) < 2:
            return None
        result = pool[0]
        for g in pool[1:]:
            result = self.merge(result, g)
        return result

    # ---- ScoreReport: 评分报告 ----

    def score_report(self, genome_id: str, success: bool,
                     latency_ms: int = 0, user_feedback: float = 0.5,
                     source: str = "all") -> dict:
        """
        提交评分报告
        自动更新基因分数、记录日志
        """
        # 查找基因
        genome = self.pull_by_id(genome_id, source)
        if not genome:
            return {"ok": False, "error": f"Genome {genome_id} not found"}

        # 计算新分数（指数加权）
        success_score = 60.0 if success else 10.0
        latency_score = max(0, 20.0 - latency_ms / 100.0)
        feedback_score = min(20.0, user_feedback * 20.0)
        raw_score = success_score + latency_score + feedback_score

        # 加权平均（历史权重 0.7，新评分 0.3）
        old_score = genome.get("score", 50.0)
        new_score = old_score * 0.7 + raw_score * 0.3
        genome["score"] = round(new_score, 2)
        genome["use_count"] = genome.get("use_count", 0) + 1
        genome.setdefault("score_history", []).append(round(raw_score, 2))

        # 更新到库中
        if genome.get("visibility") == "public":
            pool = _load_json(self.public_path)
        else:
            pool = _load_json(self.private_path)
        for i, g in enumerate(pool):
            if g.get("id") == genome_id:
                pool[i] = genome
                break
        target = self.public_path if genome.get("visibility") == "public" else self.private_path
        _save_json(target, pool)

        # 记录日志
        log_entry = {
            "genome_id": genome_id,
            "success": success,
            "latency_ms": latency_ms,
            "user_feedback": user_feedback,
            "raw_score": round(raw_score, 2),
            "new_score": round(new_score, 2),
            "timestamp": int(time.time()),
        }
        _append_score_log(log_entry)

        return {
            "ok": True,
            "genome_id": genome_id,
            "old_score": round(old_score, 2),
            "new_score": round(new_score, 2),
            "use_count": genome["use_count"],
        }

    # ---- 统计与管理 ----

    def stats(self) -> dict:
        """获取基因库统计信息"""
        private = _load_json(self.private_path)
        public = _load_json(self.public_path)
        return {
            "private_count": len(private),
            "public_count": len(public),
            "private_avg_score": round(
                sum(g.get("score", 0) for g in private) / max(len(private), 1), 2
            ),
            "public_avg_score": round(
                sum(g.get("score", 0) for g in public) / max(len(public), 1), 2
            ),
        }

    def full_report(self) -> dict:
        """生成完整的评分报告"""
        private = _load_json(self.private_path)
        public = _load_json(self.public_path)
        all_genomes = private + public

        if not all_genomes:
            return {"ok": True, "message": "基因库为空"}

        scores = [g.get("score", 0) for g in all_genomes]
        use_counts = [g.get("use_count", 0) for g in all_genomes]

        # 按分数分布统计
        score_dist = {"excellent": 0, "good": 0, "average": 0, "poor": 0, "bad": 0}
        for s in scores:
            if s >= 90:
                score_dist["excellent"] += 1
            elif s >= 70:
                score_dist["good"] += 1
            elif s >= 50:
                score_dist["average"] += 1
            elif s >= 30:
                score_dist["poor"] += 1
            else:
                score_dist["bad"] += 1

        # 找出最佳和最差基因
        sorted_genomes = sorted(all_genomes, key=lambda x: x.get("score", 0), reverse=True)
        top_3 = [{"id": g.get("id", "?")[:8], "score": g.get("score", 0),
                   "model": g.get("model", "?")} for g in sorted_genomes[:3]]
        bottom_3 = [{"id": g.get("id", "?")[:8], "score": g.get("score", 0),
                      "model": g.get("model", "?")} for g in sorted_genomes[-3:]]

        # 标签统计
        tag_counts = self.list_tags()
        top_tags = sorted(tag_counts.items(), key=lambda x: -x[1])[:5]

        return {
            "ok": True,
            "total_genomes": len(all_genomes),
            "total_private": len(private),
            "total_public": len(public),
            "avg_score": round(sum(scores) / len(scores), 2),
            "max_score": round(max(scores), 2),
            "min_score": round(min(scores), 2),
            "total_uses": sum(use_counts),
            "avg_use_count": round(sum(use_counts) / len(use_counts), 2),
            "score_distribution": score_dist,
            "top_3_genomes": top_3,
            "bottom_3_genomes": bottom_3,
            "top_tags": top_tags,
        }

    def cleanup(self, min_score: float = 10.0) -> int:
        """清理低分基因"""
        removed = 0
        for store in [self.private_path, self.public_path]:
            pool = _load_json(store)
            before = len(pool)
            pool = [g for g in pool if g.get("score", 0) >= min_score]
            removed += before - len(pool)
            _save_json(store, pool)
        return removed

    def cleanup_detailed(self, min_score: float = 10.0) -> dict:
        """清理低分基因并返回详细报告"""
        report = {"private_removed": 0, "public_removed": 0, "total_removed": 0, "remaining": 0}

        for store, key in [(self.private_path, "private"), (self.public_path, "public")]:
            pool = _load_json(store)
            before = len(pool)
            kept = [g for g in pool if g.get("score", 0) >= min_score]
            removed = before - len(kept)
            report[f"{key}_removed"] = removed
            report["total_removed"] += removed
            _save_json(store, kept)

        # 统计剩余
        remaining_private = len(_load_json(self.private_path))
        remaining_public = len(_load_json(self.public_path))
        report["remaining"] = remaining_private + remaining_public

        return report

    def list_tags(self) -> Dict[str, int]:
        """列出所有标签及其使用次数"""
        tag_counts: Dict[str, int] = {}
        for store in [self.private_path, self.public_path]:
            for g in _load_json(store):
                for tag in g.get("tags", []):
                    tag_counts[tag] = tag_counts.get(tag, 0) + 1
        return tag_counts


# ===================== CLI 入口 =====================

def main():
    import sys

    gep = GeneSharingProtocol()

    if len(sys.argv) < 2:
        print("GEP 基因共享协议")
        print("用法: python3 gene_sharing.py <command> [args]")
        print("  publish  <model>  — 发布测试基因")
        print("  pull     [model]  — 拉取基因")
        print("  merge             — 合并测试")
        print("  score    <id>     — 评分测试")
        print("  stats             — 查看统计")
        print("  report            — 生成完整报告")
        print("  cleanup           — 清理低分基因")
        print("  cleanup-detail    — 清理并返回详细报告")
        print("  tags              — 查看标签")
        return

    cmd = sys.argv[1]

    if cmd == "publish":
        model = sys.argv[2] if len(sys.argv) > 2 else "test-model"
        g = {
            "prompt_strategy": "chain-of-thought",
            "toolchain": ["search", "python"],
            "skills": ["reasoning", "coding"],
            "model": model,
            "score": 85.0,
        }
        result = gep.publish(g, visibility="public", tags=["test", "cot"])
        print(json.dumps(result, ensure_ascii=False, indent=2))

    elif cmd == "pull":
        model = sys.argv[2] if len(sys.argv) > 2 else None
        results = gep.pull(source="all", model=model, top_k=3)
        for r in results:
            print(f"  [{r.get('id','?')[:8]}] {r.get('model','?')} score={r.get('score',0):.1f}")

    elif cmd == "merge":
        g1 = {"prompt_strategy": "concise", "toolchain": ["search"], "skills": ["reasoning"], "model": "gpt-4", "score": 80}
        g2 = {"prompt_strategy": "react", "toolchain": ["python", "shell"], "skills": ["coding"], "model": "claude", "score": 90}
        merged = gep.merge(g1, g2)
        print("合并结果:", json.dumps(merged, ensure_ascii=False, indent=2))

    elif cmd == "score":
        gid = sys.argv[2] if len(sys.argv) > 2 else "test"
        result = gep.score_report(gid, success=True, latency_ms=500, user_feedback=0.8)
        print(json.dumps(result, ensure_ascii=False, indent=2))

    elif cmd == "stats":
        print(json.dumps(gep.stats(), ensure_ascii=False, indent=2))

    elif cmd == "report":
        report = gep.full_report()
        print(json.dumps(report, ensure_ascii=False, indent=2))

    elif cmd == "cleanup":
        removed = gep.cleanup()
        print(f"清理了 {removed} 个低分基因")

    elif cmd == "cleanup-detail":
        report = gep.cleanup_detailed()
        print(json.dumps(report, ensure_ascii=False, indent=2))

    elif cmd == "tags":
        tags = gep.list_tags()
        for tag, count in sorted(tags.items(), key=lambda x: -x[1]):
            print(f"  {tag}: {count}")

    else:
        print(f"未知命令: {cmd}")


if __name__ == "__main__":
    main()
