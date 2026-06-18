"""测试 superclaw.memory — 反思/知识索引/进化历史/统一记忆入口"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from superclaw.memory import (
    SelfReflection,
    KnowledgeIndex,
    EvolutionHistory,
    MemoryStore,
)


# ============================================================
# SelfReflection — 四问反思
# ============================================================

def test_self_reflection_returns_four_questions(tmp_workspace):
    log_path = tmp_workspace / "reflection.json"
    sr = SelfReflection(log_path)
    state = {"phi": 0.5, "tier": 2, "fitness": 0.6, "mutations": 3, "knowledge": 2}
    result = sr.reflect(state)

    assert "timestamp" in result
    assert "state" in result
    assert result["state"]["phi"] == 0.5
    # 四问结果
    assert "gaps" in result
    assert "opportunities" in result
    assert "improvements" in result
    assert "problems" in result
    # 每个都是列表
    assert isinstance(result["gaps"], list)
    assert isinstance(result["opportunities"], list)
    assert isinstance(result["improvements"], list)
    assert isinstance(result["problems"], list)


def test_self_reflection_gaps_low_tier(tmp_workspace):
    sr = SelfReflection(tmp_workspace / "r.json")
    result = sr.reflect({"phi": 0.3, "tier": 1, "fitness": 0.4, "mutations": 2, "knowledge": 1})
    gaps = result["gaps"]
    # tier<5 / fitness<0.8 / mutations<10 / knowledge<5 都应触发 gap
    assert any("T5" in g or "Tier" in g or "tier" in g.lower() for g in gaps)
    assert any("适应度" in g for g in gaps)
    assert any("变异" in g for g in gaps)


def test_self_reflection_gaps_high_state(tmp_workspace):
    """高状态不应触发显著 gap，但应有兜底文案"""
    sr = SelfReflection(tmp_workspace / "r.json")
    result = sr.reflect({"phi": 1.5, "tier": 5, "fitness": 0.9, "mutations": 20, "knowledge": 10})
    # 兜底：暂无显著差距
    assert len(result["gaps"]) >= 1


def test_self_reflection_opportunities_high_phi(tmp_workspace):
    sr = SelfReflection(tmp_workspace / "r.json")
    result = sr.reflect({"phi": 1.5, "tier": 5, "fitness": 0.9, "mutations": 20, "knowledge": 10})
    opps = result["opportunities"]
    assert any("Φ>1" in o or "激进" in o for o in opps)


def test_self_reflection_problems_low_health(tmp_workspace):
    sr = SelfReflection(tmp_workspace / "r.json")
    result = sr.reflect({"phi": 0.5, "tier": 2, "fitness": 0.5, "health": 0, "balance": 0.1})
    probs = result["problems"]
    assert any("健康" in p for p in probs)
    assert any("平衡" in p for f in probs for p in [f]) or any("平衡" in p for p in probs)


def test_self_reflection_persists_history(tmp_workspace):
    log_path = tmp_workspace / "reflection.json"
    sr = SelfReflection(log_path)
    sr.reflect({"phi": 0.1, "tier": 1, "fitness": 0.2, "mutations": 0, "knowledge": 0})
    sr.reflect({"phi": 0.2, "tier": 1, "fitness": 0.3, "mutations": 1, "knowledge": 1})

    history = sr.history(limit=10)
    assert len(history) == 2
    assert history[0]["state"]["phi"] == 0.1
    assert history[1]["state"]["phi"] == 0.2


def test_self_reflection_history_empty(tmp_workspace):
    sr = SelfReflection(tmp_workspace / "nope.json")
    assert sr.history() == []


# ============================================================
# KnowledgeIndex — md 知识索引 + 检索
# ============================================================

def _make_md_files(root: Path):
    """在 root 下创建几个 .md 文件用于索引测试"""
    (root / "SOUL.md").write_text(
        "# 灵魂\n这是 superclaw 的灵魂定义，描述核心身份与价值观。\n",
        encoding="utf-8",
    )
    (root / "TOOLS.md").write_text(
        "# 工具列表\nsuperclaw 提供多种工具：memory, file, search。\n",
        encoding="utf-8",
    )
    (root / "memory").mkdir(parents=True, exist_ok=True)
    (root / "memory" / "2026-01-01.md").write_text(
        "# 今日日记\n今天做了进化实验，测试了 GEP 基因生成。\n",
        encoding="utf-8",
    )


def test_knowledge_index_build(tmp_workspace):
    _make_md_files(tmp_workspace)
    idx = KnowledgeIndex(tmp_workspace)
    # 至少索引到 3 个文件
    assert len(idx.index) >= 3
    titles = [e["title"] for e in idx.index]
    assert "灵魂" in titles
    assert "工具列表" in titles


def test_knowledge_index_categories(tmp_workspace):
    _make_md_files(tmp_workspace)
    idx = KnowledgeIndex(tmp_workspace)
    stats = idx.stats()
    assert stats["total"] >= 3
    assert stats.get("root", 0) >= 2
    assert stats.get("memory", 0) >= 1


def test_knowledge_index_search_keyword(tmp_workspace):
    _make_md_files(tmp_workspace)
    idx = KnowledgeIndex(tmp_workspace)
    # 搜索 "灵魂" 应命中 SOUL.md
    results = idx.search("灵魂")
    assert len(results) >= 1
    assert any("SOUL" in r["path"] or "灵魂" in r["title"] for r in results)


def test_knowledge_index_search_english(tmp_workspace):
    _make_md_files(tmp_workspace)
    idx = KnowledgeIndex(tmp_workspace)
    results = idx.search("tools")
    assert len(results) >= 1
    assert any("TOOLS" in r["path"] for r in results)


def test_knowledge_index_search_no_match(tmp_workspace):
    _make_md_files(tmp_workspace)
    idx = KnowledgeIndex(tmp_workspace)
    results = idx.search("zzznotexist12345")
    assert results == []


def test_knowledge_index_search_empty_query(tmp_workspace):
    _make_md_files(tmp_workspace)
    idx = KnowledgeIndex(tmp_workspace)
    assert idx.search("") == []
    assert idx.search("   ") == []


def test_knowledge_index_read(tmp_workspace):
    _make_md_files(tmp_workspace)
    idx = KnowledgeIndex(tmp_workspace)
    content = idx.read("SOUL.md")
    assert content is not None
    assert "灵魂" in content
    assert idx.read("nonexistent.md") is None


def test_knowledge_index_list_by_category(tmp_workspace):
    _make_md_files(tmp_workspace)
    idx = KnowledgeIndex(tmp_workspace)
    root_entries = idx.list_by_category("root")
    assert all(e["category"] == "root" for e in root_entries)
    assert len(root_entries) >= 2
    all_entries = idx.list_by_category(None)
    assert len(all_entries) == len(idx.index)


# ============================================================
# EvolutionHistory — 记录 + 查询
# ============================================================

def test_evolution_history_record_and_recent(tmp_workspace):
    log_path = tmp_workspace / "evo.jsonl"
    eh = EvolutionHistory(log_path)
    eh.record(cycle=1, phi=0.1, domain="repair", gene_id="g1",
              score=0.5, retained=True, tier=1)
    eh.record(cycle=2, phi=0.2, domain="optimize", gene_id="g2",
              score=0.7, retained=False, tier=1)

    recent = eh.recent()
    assert len(recent) == 2
    assert recent[0]["cycle"] == 1
    assert recent[1]["cycle"] == 2
    assert recent[0]["retained"] is True
    assert recent[1]["retained"] is False


def test_evolution_history_summary(tmp_workspace):
    eh = EvolutionHistory(tmp_workspace / "evo.jsonl")
    eh.record(cycle=1, phi=0.1, domain="repair", gene_id="g1", score=0.5, retained=True, tier=1)
    eh.record(cycle=2, phi=0.3, domain="repair", gene_id="g2", score=0.6, retained=True, tier=1)
    eh.record(cycle=3, phi=0.5, domain="optimize", gene_id="g3", score=0.4, retained=False, tier=2)

    summary = eh.summary()
    assert summary["total_cycles"] == 3
    assert summary["retained_genes"] == 2
    assert summary["retention_rate"] == round(2 / 3, 4)
    assert summary["phi_first"] == 0.1
    assert summary["phi_last"] == 0.5
    assert summary["phi_growth"] == round(0.5 - 0.1, 4)
    assert "repair" in summary["domains"]
    assert "optimize" in summary["domains"]


def test_evolution_history_empty_summary(tmp_workspace):
    eh = EvolutionHistory(tmp_workspace / "empty.jsonl")
    assert eh.recent() == []
    assert eh.summary() == {"total_cycles": 0}


def test_evolution_history_extra_fields(tmp_workspace):
    eh = EvolutionHistory(tmp_workspace / "evo.jsonl")
    eh.record(cycle=1, phi=0.1, domain="repair", gene_id="g1",
              score=0.5, retained=True, tier=1,
              extra={"custom": "value"})
    recent = eh.recent()
    assert recent[0]["custom"] == "value"


# ============================================================
# MemoryStore — 统一记忆入口
# ============================================================

def test_memory_store_init_creates_dirs(tmp_workspace):
    store = MemoryStore(root=tmp_workspace)
    assert (tmp_workspace / "memory").is_dir()
    assert (tmp_workspace / "apex-state").is_dir()
    assert (tmp_workspace / "logs").is_dir()


def test_memory_store_query_reflection_intent(tmp_workspace):
    """查询 '反思' / '什么没做' 应返回反思记录"""
    store = MemoryStore(root=tmp_workspace)
    # 先产生一条反思
    store.reflect_now({"phi": 0.4, "tier": 1, "fitness": 0.3,
                       "mutations": 1, "knowledge": 1})
    result = store.query("什么没做")
    assert "反思" in result
    assert "Φ" in result or "phi" in result.lower() or "0.4" in result


def test_memory_store_query_reflection_empty(tmp_workspace):
    store = MemoryStore(root=tmp_workspace)
    result = store.query("反思")
    assert "暂无" in result


def test_memory_store_query_evolution_intent(tmp_workspace):
    """查询 '进化历史' 应返回进化记录"""
    store = MemoryStore(root=tmp_workspace)
    store.evolution.record(cycle=1, phi=0.1, domain="repair",
                           gene_id="g1", score=0.5, retained=True, tier=1)
    result = store.query("进化历史")
    assert "进化" in result
    assert "repair" in result


def test_memory_store_query_evolution_empty(tmp_workspace):
    store = MemoryStore(root=tmp_workspace)
    result = store.query("上次进化")
    assert "暂无" in result


def test_memory_store_query_status_intent(tmp_workspace):
    store = MemoryStore(root=tmp_workspace)
    result = store.query("当前状态")
    assert "状态" in result or "记忆系统" in result


def test_memory_store_query_list_intent(tmp_workspace):
    _make_md_files(tmp_workspace)
    store = MemoryStore(root=tmp_workspace)
    result = store.query("列出所有")
    assert "知识库" in result


def test_memory_store_query_search_intent(tmp_workspace):
    """默认意图 → 知识检索"""
    _make_md_files(tmp_workspace)
    store = MemoryStore(root=tmp_workspace)
    result = store.query("灵魂")
    # 应检索到 SOUL.md
    assert "SOUL" in result or "灵魂" in result


def test_memory_store_query_search_no_match(tmp_workspace):
    _make_md_files(tmp_workspace)
    store = MemoryStore(root=tmp_workspace)
    result = store.query("zzznotexist999")
    assert "未找到" in result


def test_memory_store_read_file(tmp_workspace):
    _make_md_files(tmp_workspace)
    store = MemoryStore(root=tmp_workspace)
    content = store.read_file("SOUL.md")
    assert "灵魂" in content


def test_memory_store_read_file_nonexistent(tmp_workspace):
    store = MemoryStore(root=tmp_workspace)
    content = store.read_file("nope.md")
    assert "不存在" in content


def test_memory_store_read_file_truncates_long(tmp_workspace):
    """超长文件应被截断"""
    long_content = "# 长文件\n" + ("a" * 5000)
    (tmp_workspace / "BIG.md").write_text(long_content, encoding="utf-8")
    store = MemoryStore(root=tmp_workspace)
    result = store.read_file("BIG.md")
    assert "已截断" in result


def test_memory_store_reflect_now(tmp_workspace):
    store = MemoryStore(root=tmp_workspace)
    result = store.reflect_now({"phi": 0.5, "tier": 2, "fitness": 0.6,
                                "mutations": 3, "knowledge": 2})
    assert "反思" in result
    # 再次查询应能命中
    assert "暂无" not in store.query("反思")
