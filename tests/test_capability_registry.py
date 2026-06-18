"""测试 superclaw.capability_registry — 能力清单注册表 + SelfReflection 集成"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from superclaw.capability_registry import (
    Capability,
    CapabilityGap,
    CapabilityRegistry,
    DEFAULT_CAPABILITIES,
    analyze_gaps,
)
from superclaw.memory import SelfReflection


# ============================================================
# Capability — 创建 / 序列化
# ============================================================

def test_capability_creation_defaults():
    cap = Capability(name="file_read")
    assert cap.name == "file_read"
    assert cap.description == ""
    assert cap.category == "tool"
    assert cap.source == "manual"
    assert cap.enabled is True
    assert cap.dependencies == []
    assert cap.health == 1.0
    assert cap.last_used is None
    assert cap.call_count == 0
    assert cap.fail_count == 0


def test_capability_creation_full():
    cap = Capability(
        name="feishu_channel",
        description="飞书渠道",
        category="channel",
        source="builtin",
        enabled=False,
        dependencies=["lark-oapi", "console_channel"],
        health=0.5,
        last_used="2026-06-18T10:00:00",
        call_count=10,
        fail_count=5,
    )
    assert cap.category == "channel"
    assert cap.enabled is False
    assert cap.dependencies == ["lark-oapi", "console_channel"]
    assert cap.health == 0.5
    assert cap.call_count == 10
    assert cap.fail_count == 5


def test_capability_to_dict_from_dict_roundtrip():
    cap = Capability(
        name="web_get",
        description="获取 URL 内容",
        category="network",
        source="builtin",
        enabled=True,
        dependencies=["urllib"],
        health=0.8,
        last_used="2026-06-18T12:00:00",
        call_count=20,
        fail_count=4,
    )
    d = cap.to_dict()
    assert d["name"] == "web_get"
    assert d["category"] == "network"
    assert d["dependencies"] == ["urllib"]
    assert d["health"] == 0.8
    assert d["call_count"] == 20

    restored = Capability.from_dict(d)
    assert restored.name == cap.name
    assert restored.description == cap.description
    assert restored.category == cap.category
    assert restored.source == cap.source
    assert restored.enabled == cap.enabled
    assert restored.dependencies == cap.dependencies
    assert restored.health == cap.health
    assert restored.last_used == cap.last_used
    assert restored.call_count == cap.call_count
    assert restored.fail_count == cap.fail_count


def test_capability_from_dict_tolerates_missing_fields():
    """反序列化容忍缺失字段，给默认值"""
    cap = Capability.from_dict({"name": "minimal"})
    assert cap.name == "minimal"
    assert cap.description == ""
    assert cap.category == "tool"
    assert cap.source == "manual"
    assert cap.enabled is True
    assert cap.health == 1.0
    assert cap.call_count == 0


# ============================================================
# CapabilityRegistry — register / unregister / get / list
# ============================================================

def test_register_and_get(tmp_workspace):
    reg = CapabilityRegistry(tmp_workspace / "caps.json")
    cap = Capability(name="file_read", category="io", description="读文件")
    reg.register(cap)
    got = reg.get("file_read")
    assert got is not None
    assert got.description == "读文件"
    assert reg.get("not_exist") is None


def test_register_overwrites_same_name(tmp_workspace):
    reg = CapabilityRegistry(tmp_workspace / "caps.json")
    reg.register(Capability(name="x", description="v1"))
    reg.register(Capability(name="x", description="v2"))
    assert len(reg.list_all()) == 1
    assert reg.get("x").description == "v2"


def test_unregister(tmp_workspace):
    reg = CapabilityRegistry(tmp_workspace / "caps.json")
    reg.register(Capability(name="a"))
    reg.register(Capability(name="b"))
    reg.unregister("a")
    assert reg.get("a") is None
    assert reg.get("b") is not None
    # 注销不存在的应静默
    reg.unregister("nope")
    assert len(reg.list_all()) == 1


def test_list_all(tmp_workspace):
    reg = CapabilityRegistry(tmp_workspace / "caps.json")
    reg.register(Capability(name="a", category="io"))
    reg.register(Capability(name="b", category="llm"))
    reg.register(Capability(name="c", category="io"))
    all_caps = reg.list_all()
    assert len(all_caps) == 3
    names = {c.name for c in all_caps}
    assert names == {"a", "b", "c"}


def test_list_by_category(tmp_workspace):
    reg = CapabilityRegistry(tmp_workspace / "caps.json")
    reg.register(Capability(name="a", category="io"))
    reg.register(Capability(name="b", category="llm"))
    reg.register(Capability(name="c", category="io"))
    io_caps = reg.list_by_category("io")
    assert len(io_caps) == 2
    assert all(c.category == "io" for c in io_caps)
    assert reg.list_by_category("network") == []


def test_list_enabled(tmp_workspace):
    reg = CapabilityRegistry(tmp_workspace / "caps.json")
    reg.register(Capability(name="on", enabled=True))
    reg.register(Capability(name="off", enabled=False))
    enabled = reg.list_enabled()
    assert len(enabled) == 1
    assert enabled[0].name == "on"


def test_register_defaults(tmp_workspace):
    reg = CapabilityRegistry(tmp_workspace / "caps.json")
    reg.register_defaults()
    names = {c.name for c in reg.list_all()}
    # DEFAULT_CAPABILITIES 的所有名字都应被注册
    expected = {c.name for c in DEFAULT_CAPABILITIES}
    assert names == expected


# ============================================================
# detect_gaps — 正确识别缺失
# ============================================================

def test_detect_gaps_finds_missing(tmp_workspace):
    reg = CapabilityRegistry(tmp_workspace / "caps.json")
    reg.register(Capability(name="file_read"))
    reg.register(Capability(name="file_write"))
    required = ["file_read", "file_write", "shell", "web_get"]
    gaps = reg.detect_gaps(required)
    assert gaps == ["shell", "web_get"]


def test_detect_gaps_no_missing(tmp_workspace):
    reg = CapabilityRegistry(tmp_workspace / "caps.json")
    reg.register(Capability(name="a"))
    reg.register(Capability(name="b"))
    assert reg.detect_gaps(["a", "b"]) == []


def test_detect_gaps_empty_required(tmp_workspace):
    reg = CapabilityRegistry(tmp_workspace / "caps.json")
    reg.register(Capability(name="a"))
    assert reg.detect_gaps([]) == []


def test_detect_gaps_dedup_and_preserves_order(tmp_workspace):
    reg = CapabilityRegistry(tmp_workspace / "caps.json")
    reg.register(Capability(name="a"))
    gaps = reg.detect_gaps(["z", "y", "z", "a"])
    assert gaps == ["z", "y"]


def test_detect_gaps_treats_disabled_as_registered(tmp_workspace):
    """已注册但禁用的能力不算'缺失'（它存在，只是未启用）"""
    reg = CapabilityRegistry(tmp_workspace / "caps.json")
    reg.register(Capability(name="feishu", enabled=False))
    gaps = reg.detect_gaps(["feishu"])
    assert gaps == []


# ============================================================
# record_call — 更新 health
# ============================================================

def test_record_call_updates_health_success(tmp_workspace):
    reg = CapabilityRegistry(tmp_workspace / "caps.json")
    reg.register(Capability(name="web_get", health=1.0))
    reg.record_call("web_get", success=True)
    cap = reg.get("web_get")
    assert cap.call_count == 1
    assert cap.fail_count == 0
    assert cap.health == 1.0
    assert cap.last_used is not None


def test_record_call_updates_health_mixed(tmp_workspace):
    reg = CapabilityRegistry(tmp_workspace / "caps.json")
    reg.register(Capability(name="web_get", health=1.0))
    reg.record_call("web_get", success=True)   # 1/1 = 1.0
    reg.record_call("web_get", success=False)  # 1/2 = 0.5
    reg.record_call("web_get", success=False)  # 1/3 ≈ 0.333
    cap = reg.get("web_get")
    assert cap.call_count == 3
    assert cap.fail_count == 2
    assert cap.health == pytest.approx(1 / 3)


def test_record_call_unknown_capability_silent(tmp_workspace):
    """记录未注册能力的调用应静默忽略，不抛错"""
    reg = CapabilityRegistry(tmp_workspace / "caps.json")
    reg.record_call("not_registered", success=True)
    assert reg.get("not_registered") is None


def test_record_call_sets_last_used_iso(tmp_workspace):
    reg = CapabilityRegistry(tmp_workspace / "caps.json")
    reg.register(Capability(name="x"))
    reg.record_call("x", success=True)
    last = reg.get("x").last_used
    assert last is not None
    # 应是合法 ISO 格式（能解析回来）
    from datetime import datetime
    datetime.fromisoformat(last)


# ============================================================
# analyze_gaps — 结构化缺口
# ============================================================

def test_analyze_gaps_returns_structured(tmp_workspace):
    reg = CapabilityRegistry(tmp_workspace / "caps.json")
    reg.register(Capability(name="file_read", category="io"))
    reg.register(Capability(name="file_write", category="io"))
    # required 含缺失：web_get（builtin, network）、feishu_channel（builtin, channel）、custom_thing（未知）
    required = ["file_read", "file_write", "web_get", "feishu_channel", "custom_thing"]
    gaps = analyze_gaps(reg, required)
    assert len(gaps) == 3
    assert all(isinstance(g, CapabilityGap) for g in gaps)
    names = {g.missing_capability for g in gaps}
    assert names == {"web_get", "feishu_channel", "custom_thing"}


def test_analyze_gaps_builtin_uses_manual_action(tmp_workspace):
    reg = CapabilityRegistry(tmp_workspace / "caps.json")
    # 注册一个 io 能力，让 web_get（network builtin）缺失
    reg.register(Capability(name="file_read", category="io"))
    gaps = analyze_gaps(reg, ["file_read", "web_get"])
    assert len(gaps) == 1
    g = gaps[0]
    assert g.missing_capability == "web_get"
    # builtin 缺失 → manual（重新注册内置）
    assert g.suggested_action == "manual"
    # network 类别 → important
    assert g.severity == "important"
    # manual 不需要 search_query
    assert g.search_query == ""


def test_analyze_gaps_unknown_uses_github_search(tmp_workspace):
    reg = CapabilityRegistry(tmp_workspace / "caps.json")
    gaps = analyze_gaps(reg, ["custom_widget"])
    assert len(gaps) == 1
    g = gaps[0]
    assert g.missing_capability == "custom_widget"
    assert g.suggested_action == "github_search"
    assert g.search_query != ""
    assert "custom widget" in g.search_query or "custom_widget" in g.search_query


def test_analyze_gaps_severity_ordering(tmp_workspace):
    """返回按 severity 从重到轻排序：critical → important → nice-to-have"""
    reg = CapabilityRegistry(tmp_workspace / "caps.json")
    # llm_complete（builtin, llm → critical, manual）
    # image_api（未知，含 'api' → important, github_search）
    # random_thing（未知，无关键词 → nice-to-have, github_search）
    gaps = analyze_gaps(reg, ["llm_complete", "image_api", "random_thing"])
    severities = [g.severity for g in gaps]
    assert severities[0] == "critical"
    assert severities[-1] == "nice-to-have"
    # critical 应排在 important 之前
    assert severities.index("critical") < severities.index("important")


def test_analyze_gaps_no_gaps_returns_empty(tmp_workspace):
    reg = CapabilityRegistry(tmp_workspace / "caps.json")
    reg.register(Capability(name="a"))
    reg.register(Capability(name="b"))
    assert analyze_gaps(reg, ["a", "b"]) == []


def test_analyze_gaps_critical_for_unknown_llm_keyword(tmp_workspace):
    """未知能力名含 'llm' 关键词 → critical"""
    reg = CapabilityRegistry(tmp_workspace / "caps.json")
    gaps = analyze_gaps(reg, ["my_llm_helper"])
    assert gaps[0].severity == "critical"


# ============================================================
# 持久化 save / load 往返
# ============================================================

def test_save_load_roundtrip(tmp_workspace):
    reg = CapabilityRegistry(tmp_workspace / "caps.json")
    reg.register(Capability(
        name="web_get", description="获取 URL",
        category="network", source="builtin",
        dependencies=["urllib"], health=0.75,
        call_count=10, fail_count=2,
    ))
    reg.register(Capability(
        name="file_read", category="io",
        last_used="2026-06-18T10:00:00",
    ))
    reg.save()
    assert (tmp_workspace / "caps.json").exists()

    # 新实例从同一文件载入
    reg2 = CapabilityRegistry(tmp_workspace / "caps.json")
    assert len(reg2.list_all()) == 2
    web = reg2.get("web_get")
    assert web is not None
    assert web.description == "获取 URL"
    assert web.category == "network"
    assert web.dependencies == ["urllib"]
    assert web.health == 0.75
    assert web.call_count == 10
    assert web.fail_count == 2
    fr = reg2.get("file_read")
    assert fr is not None
    assert fr.last_used == "2026-06-18T10:00:00"


def test_load_nonexistent_file_is_empty(tmp_workspace):
    reg = CapabilityRegistry(tmp_workspace / "nope.json")
    assert reg.list_all() == []


def test_load_corrupt_file_is_empty(tmp_workspace):
    path = tmp_workspace / "bad.json"
    path.write_text("{not valid json", encoding="utf-8")
    reg = CapabilityRegistry(path)
    assert reg.list_all() == []


def test_save_creates_parent_dirs(tmp_workspace):
    nested = tmp_workspace / "nested" / "deep" / "caps.json"
    reg = CapabilityRegistry(nested)
    reg.register(Capability(name="x"))
    reg.save()
    assert nested.exists()


# ============================================================
# DEFAULT_CAPABILITIES 完整性
# ============================================================

def test_default_capabilities_contains_all_expected_names():
    names = {c.name for c in DEFAULT_CAPABILITIES}
    expected = {
        # io
        "file_read", "file_write", "shell",
        # network
        "web_get",
        # llm
        "llm_complete",
        # evolution
        "gep_cycle", "apex_reflect", "memory_query",
        # channel
        "console_channel", "feishu_channel",
        # tool
        "tool_registry", "skill_loader",
    }
    assert expected.issubset(names), f"缺失: {expected - names}"


def test_default_capabilities_names_unique():
    names = [c.name for c in DEFAULT_CAPABILITIES]
    assert len(names) == len(set(names)), "DEFAULT_CAPABILITIES 有重名"


def test_default_capabilities_categories_valid():
    valid = {"io", "network", "llm", "evolution", "security", "channel", "tool"}
    for cap in DEFAULT_CAPABILITIES:
        assert cap.category in valid, f"{cap.name} 类别非法: {cap.category}"


def test_default_capabilities_sources_are_builtin():
    for cap in DEFAULT_CAPABILITIES:
        assert cap.source == "builtin", f"{cap.name} source 应为 builtin"


def test_default_capabilities_have_descriptions():
    for cap in DEFAULT_CAPABILITIES:
        assert cap.description, f"{cap.name} 缺少 description"
        assert isinstance(cap.description, str)


def test_default_capabilities_have_dependencies_lists():
    for cap in DEFAULT_CAPABILITIES:
        assert isinstance(cap.dependencies, list)


def test_default_capabilities_health_initialized():
    for cap in DEFAULT_CAPABILITIES:
        assert 0.0 <= cap.health <= 1.0


# ============================================================
# 集成测试：SelfReflection 用 capability_registry 输出真实缺口
# ============================================================

def test_self_reflection_with_capability_registry_outputs_real_gaps(tmp_workspace):
    """提供 registry 时，gaps 应是真实能力缺口而非模板字符串"""
    reg = CapabilityRegistry(tmp_workspace / "caps.json")
    # 只注册 io 类能力，其余 DEFAULT_CAPABILITIES 缺失
    for cap in DEFAULT_CAPABILITIES:
        if cap.category == "io":
            reg.register(cap)

    sr = SelfReflection(tmp_workspace / "reflection.json")
    # 高状态：原模板逻辑会返回"暂无显著差距"；这里应被 registry 路径覆盖
    result = sr.reflect(
        {"phi": 1.5, "tier": 5, "fitness": 0.95, "mutations": 20, "knowledge": 10},
        capability_registry=reg,
    )
    gaps = result["gaps"]
    assert len(gaps) > 0
    gaps_text = " ".join(gaps)
    # 应包含缺失的能力名（web_get / llm_complete / gep_cycle 等）
    assert "web_get" in gaps_text or "llm_complete" in gaps_text
    # 应使用"缺少能力"前缀（来自 _capability_gaps）
    assert any("缺少能力" in g for g in gaps)
    # 不应出现模板字符串
    assert "适应度" not in gaps_text
    assert "变异" not in gaps_text
    assert "暂无显著差距" not in gaps_text


def test_self_reflection_with_complete_registry_no_gaps(tmp_workspace):
    """registry 完整时，gaps 应是'能力清单完整'兜底"""
    reg = CapabilityRegistry(tmp_workspace / "caps.json")
    reg.register_defaults()

    sr = SelfReflection(tmp_workspace / "reflection.json")
    result = sr.reflect(
        {"phi": 0.5, "tier": 2, "fitness": 0.6, "mutations": 3, "knowledge": 2},
        capability_registry=reg,
    )
    gaps = result["gaps"]
    assert len(gaps) == 1
    assert "能力清单完整" in gaps[0]


def test_self_reflection_with_custom_task_requirements(tmp_workspace):
    """可显式传 task_requirements 缩小检测范围"""
    reg = CapabilityRegistry(tmp_workspace / "caps.json")
    reg.register(Capability(name="file_read"))

    sr = SelfReflection(tmp_workspace / "reflection.json")
    result = sr.reflect(
        {"phi": 0.5, "tier": 2, "fitness": 0.6, "mutations": 3, "knowledge": 2},
        capability_registry=reg,
        task_requirements=["file_read", "shell"],
    )
    gaps = result["gaps"]
    assert len(gaps) == 1
    assert "shell" in gaps[0]


def test_self_reflection_backward_compatible_without_registry(tmp_workspace):
    """无 registry 时走原逻辑（向后兼容）"""
    sr = SelfReflection(tmp_workspace / "reflection.json")
    result = sr.reflect(
        {"phi": 0.3, "tier": 1, "fitness": 0.4, "mutations": 2, "knowledge": 1},
    )
    gaps = result["gaps"]
    # 原模板逻辑应触发
    gaps_text = " ".join(gaps)
    assert "适应度" in gaps_text or "变异" in gaps_text or "Tier" in gaps_text
    # 不应出现 registry 路径的"缺少能力"前缀
    assert "缺少能力" not in gaps_text
