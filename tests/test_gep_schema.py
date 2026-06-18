"""测试 superclaw.gep_schema — GEP 基因/胶囊/进化事件/基因库"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from superclaw.gep_schema import (
    Gene,
    Capsule,
    EvolutionEvent,
    Signal,
    GeneLibrary,
    VALID_CATEGORIES,
    VALID_OUTCOME_STATUSES,
    VALID_STRATEGIES,
    SCHEMA_VERSION,
)


# ============================================================
# 常量
# ============================================================

def test_valid_categories_contains_four_domains():
    assert VALID_CATEGORIES == ["repair", "optimize", "innovate", "explore"]


def test_valid_outcome_statuses():
    assert VALID_OUTCOME_STATUSES == ["success", "failed"]


def test_valid_strategies_contains_balanced_and_auto():
    assert "balanced" in VALID_STRATEGIES
    assert "auto" in VALID_STRATEGIES
    assert "repair-only" in VALID_STRATEGIES
    assert len(VALID_STRATEGIES) >= 5


# ============================================================
# Gene
# ============================================================

def test_gene_creation_defaults():
    gene = Gene()
    assert gene.type == "Gene"
    assert gene.category == "innovate"  # 默认
    assert gene.id.startswith("gene-")
    assert gene.schema_version == SCHEMA_VERSION
    assert gene.signals_match == []
    assert gene.strategy == []


def test_gene_category_invalid_is_corrected():
    """无效 category 应被修正为 innovate（不抛异常）"""
    gene = Gene(category="not-a-real-category")
    assert gene.category == "innovate"


def test_gene_category_valid_preserved():
    for cat in VALID_CATEGORIES:
        gene = Gene(category=cat)
        assert gene.category == cat


def test_gene_id_auto_computed_sha256():
    """相同内容 → 相同 id（内容寻址）"""
    g1 = Gene(category="repair", signals_match=["error"], strategy=["fix"], summary="s1")
    g2 = Gene(category="repair", signals_match=["error"], strategy=["fix"], summary="s1")
    g3 = Gene(category="repair", signals_match=["error"], strategy=["fix"], summary="s2")
    assert g1.id == g2.id
    assert g1.id != g3.id
    assert g1.id.startswith("gene-")
    # SHA-256 前 16 位 → "gene-" + 16 chars = 21 chars
    assert len(g1.id) == len("gene-") + 16


def test_gene_id_explicit_not_overwritten():
    """显式传入 id 时不被覆盖"""
    gene = Gene(id="gene-custom-123")
    assert gene.id == "gene-custom-123"


def test_gene_validate_success():
    gene = Gene(category="repair", signals_match=["err"], strategy=["step1"])
    assert gene.validate() is True


def test_gene_validate_invalid_category_raises():
    gene = Gene(category="innovate")
    gene.category = "bogus"  # 绕过 __post_init__ 修正
    with pytest.raises(ValueError):
        gene.validate()


def test_gene_validate_invalid_type_raises():
    gene = Gene()
    gene.type = "NotGene"
    with pytest.raises(ValueError):
        gene.validate()


def test_gene_to_dict_and_from_dict_roundtrip():
    gene = Gene(
        category="optimize",
        signals_match=["slow"],
        strategy=["optimize_loop"],
        summary="优化循环",
        epigenetic_marks=["mark1"],
    )
    d = gene.to_dict()
    assert d["type"] == "Gene"
    assert d["category"] == "optimize"
    assert d["summary"] == "优化循环"

    gene2 = Gene.from_dict(d)
    assert gene2.id == gene.id
    assert gene2.category == gene.category
    assert gene2.signals_match == gene.signals_match
    assert gene2.summary == gene.summary


def test_gene_arrays_are_copies():
    """__post_init__ 确保数组是副本，修改不影响原始"""
    signals = ["a"]
    gene = Gene(signals_match=signals)
    gene.signals_match.append("b")
    assert signals == ["a"]  # 原始列表不受影响


# ============================================================
# Capsule
# ============================================================

def test_capsule_creation_defaults():
    cap = Capsule()
    assert cap.type == "Capsule"
    assert cap.id.startswith("cap-")
    assert cap.outcome["status"] == "failed"  # 默认
    assert cap.asset_id == cap.id


def test_capsule_outcome_status_invalid_corrected():
    """无效 outcome.status 被修正为 failed"""
    cap = Capsule(outcome={"status": "weird", "score": 5})
    assert cap.outcome["status"] == "failed"


def test_capsule_outcome_status_valid_preserved():
    cap = Capsule(outcome={"status": "success", "score": 0.9})
    assert cap.outcome["status"] == "success"


def test_capsule_validate_success():
    cap = Capsule(
        trigger=["t1"],
        outcome={"status": "success", "score": 0.8},
    )
    assert cap.validate() is True


def test_capsule_validate_invalid_status_raises():
    cap = Capsule()
    cap.outcome = {"status": "nope"}
    with pytest.raises(ValueError):
        cap.validate()


def test_capsule_id_content_addressed():
    c1 = Capsule(trigger=["t"], gene="g1", summary="s", outcome={"status": "success"})
    c2 = Capsule(trigger=["t"], gene="g1", summary="s", outcome={"status": "success"})
    c3 = Capsule(trigger=["t"], gene="g1", summary="s", outcome={"status": "failed"})
    assert c1.id == c2.id
    assert c1.id != c3.id


def test_capsule_to_dict_from_dict_roundtrip():
    cap = Capsule(
        trigger=["deploy"],
        gene="gene-abc",
        summary="部署流程",
        outcome={"status": "success", "score": 0.95},
        confidence=0.9,
    )
    d = cap.to_dict()
    cap2 = Capsule.from_dict(d)
    assert cap2.id == cap.id
    assert cap2.trigger == cap.trigger
    assert cap2.outcome == cap.outcome
    assert cap2.confidence == cap.confidence


# ============================================================
# EvolutionEvent — 不可篡改
# ============================================================

def test_evolution_event_creation():
    evt = EvolutionEvent(
        event_type="repair",
        gene_id="gene-123",
        strategy="repair-only",
        summary="修复了 bug",
        phi_before=0.5,
        phi_after=0.6,
        success=True,
    )
    assert evt.event_id.startswith("evt-")
    assert evt.timestamp != ""
    assert evt.hash != ""


def test_evolution_event_hash_immutable():
    """hash 字段反映内容；篡改字段后 verify() 返回 False"""
    evt = EvolutionEvent(
        event_type="innovation",
        gene_id="gene-1",
        phi_before=0.3,
        phi_after=0.4,
        success=True,
    )
    original_hash = evt.hash
    assert evt.verify() is True

    # 篡改字段
    evt.phi_after = 0.99
    assert evt.hash == original_hash  # hash 字段本身不变
    assert evt.verify() is False  # 但重新计算不匹配 → 被篡改


def test_evolution_event_hash_deterministic():
    """相同关键字段 → 相同 hash（timestamp 也相同的情况）"""
    e1 = EvolutionEvent(
        event_type="repair",
        gene_id="g1",
        timestamp="2026-01-01T00:00:00",
        phi_before=0.1,
        phi_after=0.2,
        success=True,
    )
    e2 = EvolutionEvent(
        event_type="repair",
        gene_id="g1",
        timestamp="2026-01-01T00:00:00",
        phi_before=0.1,
        phi_after=0.2,
        success=True,
    )
    assert e1.hash == e2.hash


def test_evolution_event_to_dict_contains_hash():
    evt = EvolutionEvent(event_type="solidify", gene_id="g2")
    d = evt.to_dict()
    assert "hash" in d
    assert d["hash"] == evt.hash
    assert d["event_id"] == evt.event_id


# ============================================================
# Signal
# ============================================================

def test_signal_creation_defaults():
    sig = Signal()
    assert sig.signal_type == ""
    assert sig.severity == "low"
    assert sig.timestamp != ""


def test_signal_with_fields():
    sig = Signal(
        signal_type="error",
        source="test.log",
        severity="critical",
        pattern="NullPointerException",
        context="在处理用户请求时",
    )
    assert sig.signal_type == "error"
    assert sig.severity == "critical"
    assert sig.pattern == "NullPointerException"


# ============================================================
# GeneLibrary — 持久化（用 tmp 目录）
# ============================================================

def test_gene_library_load_empty(tmp_workspace):
    lib = GeneLibrary(tmp_workspace)
    assert lib.load_genes() == []
    assert lib.load_capsules() == []
    assert lib.load_events() == []


def test_gene_library_upsert_and_load(tmp_workspace):
    lib = GeneLibrary(tmp_workspace)
    gene = Gene(
        category="repair",
        signals_match=["error:NullPointer"],
        strategy=["add null check"],
        summary="修复空指针",
    )
    assert lib.upsert_gene(gene) is True

    loaded = lib.load_genes()
    assert len(loaded) == 1
    assert loaded[0].id == gene.id
    assert loaded[0].category == "repair"


def test_gene_library_upsert_dedup_by_id(tmp_workspace):
    """相同 id 的 gene upsert 应替换而非追加"""
    lib = GeneLibrary(tmp_workspace)
    gene = Gene(category="repair", signals_match=["err"], strategy=["s"], summary="v1")
    lib.upsert_gene(gene)

    # 修改 summary 但保持内容寻址 id 相同 → 实际上 id 会变
    # 用相同内容的 gene 验证去重
    gene_dup = Gene(category="repair", signals_match=["err"], strategy=["s"], summary="v1")
    assert gene_dup.id == gene.id
    lib.upsert_gene(gene_dup)

    assert len(lib.load_genes()) == 1


def test_gene_library_find_gene(tmp_workspace):
    lib = GeneLibrary(tmp_workspace)
    gene = Gene(category="optimize", signals_match=["slow"], strategy=["cache"], summary="加缓存")
    lib.upsert_gene(gene)

    found = lib.find_gene(gene.id)
    assert found is not None
    assert found.id == gene.id
    assert found.summary == "加缓存"

    assert lib.find_gene("gene-nonexistent") is None


def test_gene_library_find_by_signal(tmp_workspace):
    lib = GeneLibrary(tmp_workspace)
    g1 = Gene(category="repair", signals_match=["error:NullPointer", "error:IO"],
              strategy=["s1"], summary="g1")
    g2 = Gene(category="optimize", signals_match=["slow_response"],
              strategy=["s2"], summary="g2")
    g3 = Gene(category="innovate", signals_match=["new_feature"],
              strategy=["s3"], summary="g3")
    for g in [g1, g2, g3]:
        lib.upsert_gene(g)

    # 子串匹配（大小写不敏感）
    results = lib.find_by_signal("error")
    assert len(results) == 1
    assert results[0].id == g1.id

    results = lib.find_by_signal("SLOW")
    assert len(results) == 1
    assert results[0].id == g2.id

    assert lib.find_by_signal("nonexistent_pattern") == []


def test_gene_library_upsert_capsule(tmp_workspace):
    lib = GeneLibrary(tmp_workspace)
    cap = Capsule(
        trigger=["deploy"],
        gene="gene-1",
        summary="部署成功",
        outcome={"status": "success", "score": 0.9},
    )
    assert lib.upsert_capsule(cap) is True
    loaded = lib.load_capsules()
    assert len(loaded) == 1
    assert loaded[0].id == cap.id


def test_gene_library_append_and_load_events(tmp_workspace):
    lib = GeneLibrary(tmp_workspace)
    evt1 = EvolutionEvent(event_type="repair", gene_id="g1", success=True)
    evt2 = EvolutionEvent(event_type="innovation", gene_id="g2", success=False)
    assert lib.append_event(evt1) is True
    assert lib.append_event(evt2) is True

    events = lib.load_events()
    assert len(events) == 2
    assert events[0]["event_type"] == "repair"
    assert events[1]["event_type"] == "innovation"
    # 每条都有 hash
    assert "hash" in events[0]


def test_gene_library_stats(tmp_workspace):
    lib = GeneLibrary(tmp_workspace)
    lib.upsert_gene(Gene(category="repair", signals_match=["e"], strategy=["s"], summary="r"))
    lib.upsert_gene(Gene(category="optimize", signals_match=["e"], strategy=["s"], summary="o"))
    lib.upsert_capsule(Capsule(trigger=["t"], outcome={"status": "success"}))
    lib.upsert_capsule(Capsule(trigger=["t2"], outcome={"status": "failed"}))
    lib.append_event(EvolutionEvent(event_type="repair", gene_id="g"))

    stats = lib.stats()
    assert stats["total_genes"] == 2
    assert stats["gene_categories"]["repair"] == 1
    assert stats["gene_categories"]["optimize"] == 1
    assert stats["total_capsules"] == 2
    assert stats["successful_capsules"] == 1
    assert stats["total_events"] == 1


def test_gene_library_load_genes_corrupt_file_returns_empty(tmp_workspace):
    """JSON 损坏时 load_genes 返回空列表而非抛异常"""
    lib = GeneLibrary(tmp_workspace)
    lib.genes_file.write_text("{not valid json", encoding="utf-8")
    assert lib.load_genes() == []
