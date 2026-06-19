"""core_bridge 真集成测试 — 验证 Python 真调 C/Rust 二进制

这些测试需要 gcc / cargo 可用，会真编译并启动二进制。
无编译器时 pytest.skip，不 fail。
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from superclaw.core_bridge import (  # noqa: E402
    CCoreBridge, RustEngineBridge,
    ensure_c_core, ensure_rust_engine,
    get_c_core, get_rust_engine, shutdown_bridges,
)


def _has_gcc() -> bool:
    return shutil.which("gcc") is not None


def _has_cargo() -> bool:
    return shutil.which("cargo") is not None


pytestmark = pytest.mark.skipif(
    not (_has_gcc() or _has_cargo()),
    reason="无 gcc/cargo，跳过 C/Rust 真集成测试",
)


# ============================================================
# C Core 桥接测试
# ============================================================

@pytest.mark.skipif(not _has_gcc(), reason="无 gcc")
class TestCCoreBridge:
    """C 核心桥接 — 真调 c-core-pipe 二进制"""

    def test_ensure_c_core_compiles(self):
        """ensure_c_core 能编译出二进制"""
        assert ensure_c_core()

    def test_heartbeat_keep_alive(self):
        """持久化模式心跳"""
        bridge = CCoreBridge(keep_alive=True)
        try:
            resp = bridge.heartbeat()
            assert resp["status"] == "ok"
            assert resp["cmd"] == "heartbeat"
            assert resp["data"]["cycle"] >= 1
            assert "fitness" in resp["data"]
        finally:
            bridge.close()

    def test_heartbeat_one_shot(self):
        """一次性模式心跳"""
        bridge = CCoreBridge(keep_alive=False)
        resp = bridge.heartbeat()
        assert resp["status"] == "ok"
        assert resp["data"]["cycle"] >= 1

    def test_status(self):
        """状态查询"""
        bridge = CCoreBridge(keep_alive=True)
        try:
            resp = bridge.status()
            assert resp["status"] == "ok"
            assert resp["data"]["name"] == "MiMoClaw"
            assert "generation" in resp["data"]
            assert "fitness" in resp["data"]
        finally:
            bridge.close()

    def test_detect_weakness_initial(self):
        """初始短板检测 — 应该报告多个弱点（skills/knowledge 低）"""
        bridge = CCoreBridge(keep_alive=True)
        try:
            resp = bridge.detect_weakness()
            assert resp["status"] == "ok"
            assert resp["data"]["count"] >= 1
            weaknesses = resp["data"]["weaknesses"]
            assert isinstance(weaknesses, str)
        finally:
            bridge.close()

    def test_health_check(self):
        """健康检查"""
        bridge = CCoreBridge(keep_alive=True)
        try:
            resp = bridge.health_check()
            assert resp["status"] == "ok"
            assert "health" in resp["data"]
            assert "issues" in resp["data"]
        finally:
            bridge.close()

    def test_record_evolution(self):
        """记录进化 — mutations/knowledge 累加"""
        bridge = CCoreBridge(keep_alive=True)
        try:
            before = bridge.status()["data"]
            resp = bridge.record_evolution(mutations=2, knowledge=3, fitness=1.5)
            assert resp["status"] == "ok"
            assert resp["data"]["total_mutations"] == before["mutations"] + 2
            assert resp["data"]["total_knowledge"] == before["knowledge"] + 3
            assert resp["data"]["fitness"] == 1.5
        finally:
            bridge.close()

    def test_multiple_commands_same_process(self):
        """持久化模式 — 同一进程连续多条命令"""
        bridge = CCoreBridge(keep_alive=True)
        try:
            # 连续 5 次心跳
            cycles = []
            for _ in range(5):
                resp = bridge.heartbeat()
                assert resp["status"] == "ok"
                cycles.append(resp["data"]["cycle"])
            # cycle 应该递增
            assert cycles == sorted(cycles)
            assert cycles[-1] > cycles[0]
        finally:
            bridge.close()

    def test_unknown_command_returns_error(self):
        """未知命令返回 error（不崩溃）"""
        bridge = CCoreBridge(keep_alive=True)
        try:
            resp = bridge.send({"cmd": "nonexistent_cmd"})
            assert resp["status"] == "error"
        finally:
            bridge.close()


# ============================================================
# Rust Engine 桥接测试
# ============================================================

@pytest.mark.skipif(not _has_cargo(), reason="无 cargo")
class TestRustEngineBridge:
    """Rust 引擎桥接 — 真调 rust-engine-pipe 二进制"""

    def test_ensure_rust_engine_compiles(self):
        """ensure_rust_engine 能编译出二进制"""
        assert ensure_rust_engine()

    def test_status_initial(self):
        """初始状态 — 0 基因"""
        bridge = RustEngineBridge(keep_alive=True)
        try:
            resp = bridge.status()
            assert resp["status"] == "ok"
            assert resp["data"]["genes"] == 0
            assert resp["data"]["balance"] == 0.0
        finally:
            bridge.close()

    def test_mutate_creates_gene(self):
        """变异创建基因"""
        bridge = RustEngineBridge(keep_alive=True)
        try:
            resp = bridge.mutate(domain="math", change=0.1)
            assert resp["status"] == "ok"
            assert "gene_id" in resp["data"]
            assert resp["data"]["domain"] == "math"
            assert resp["data"]["total_genes"] >= 1
        finally:
            bridge.close()

    def test_evaluate_gene(self):
        """评估基因得分"""
        bridge = RustEngineBridge(keep_alive=True)
        try:
            mutated = bridge.mutate(domain="logic", change=0.2)
            gene_id = mutated["data"]["gene_id"]
            resp = bridge.evaluate(gene_id)
            assert resp["status"] == "ok"
            assert "score" in resp["data"]
            assert "strength" in resp["data"]
        finally:
            bridge.close()

    def test_evaluate_nonexistent_gene(self):
        """评估不存在的基因返回 error"""
        bridge = RustEngineBridge(keep_alive=True)
        try:
            resp = bridge.evaluate("gene-nonexistent")
            assert resp["status"] == "error"
        finally:
            bridge.close()

    def test_retain_increases_use_count(self):
        """保留基因增加 use_count"""
        bridge = RustEngineBridge(keep_alive=True)
        try:
            mutated = bridge.mutate(domain="memory", change=0.05)
            gene_id = mutated["data"]["gene_id"]
            r1 = bridge.retain(gene_id)
            r2 = bridge.retain(gene_id)
            assert r1["data"]["use_count"] == 1
            assert r2["data"]["use_count"] == 2
        finally:
            bridge.close()

    def test_balance_after_mutations(self):
        """多 domain 变异后查询平衡"""
        bridge = RustEngineBridge(keep_alive=True)
        try:
            bridge.mutate(domain="math", change=0.1)
            bridge.mutate(domain="logic", change=0.1)
            bridge.mutate(domain="memory", change=0.1)
            resp = bridge.balance()
            assert resp["status"] == "ok"
            assert resp["data"]["gene_count"] >= 3
            assert "domains" in resp["data"]
        finally:
            bridge.close()

    def test_retention_check(self):
        """保留检查 — 统计强/弱基因"""
        bridge = RustEngineBridge(keep_alive=True)
        try:
            bridge.mutate(domain="x", change=0.5)
            resp = bridge.retention_check()
            assert resp["status"] == "ok"
            assert resp["data"]["total"] >= 1
            assert resp["data"]["strong"] + resp["data"]["weak"] == resp["data"]["total"]
        finally:
            bridge.close()

    def test_mutate_rejects_huge_change(self):
        """变异幅度过大被拒绝（mutation_rate*10=1.0 为上限）"""
        bridge = RustEngineBridge(keep_alive=True)
        try:
            # change=0.5 在限制内（< 1.0），应成功
            ok_resp = bridge.mutate(domain="x", change=0.5)
            assert ok_resp["status"] == "ok"
            # change=2.0 超过限制 1.0，应被拒
            resp = bridge.mutate(domain="x", change=2.0)
            assert resp["status"] == "error"
        finally:
            bridge.close()


# ============================================================
# 单例与生命周期
# ============================================================

class TestBridgeLifecycle:
    """桥接单例与生命周期管理"""

    def test_get_c_core_singleton(self):
        """get_c_core 返回同一实例"""
        b1 = get_c_core(keep_alive=False)
        b2 = get_c_core(keep_alive=False)
        assert b1 is b2

    def test_get_rust_engine_singleton(self):
        """get_rust_engine 返回同一实例"""
        if not _has_cargo():
            pytest.skip("无 cargo")
        b1 = get_rust_engine(keep_alive=False)
        b2 = get_rust_engine(keep_alive=False)
        assert b1 is b2

    def test_shutdown_bridges(self):
        """shutdown_bridges 清理单例"""
        if _has_gcc():
            get_c_core(keep_alive=False)
        if _has_cargo():
            get_rust_engine(keep_alive=False)
        shutdown_bridges()
        # 再获取应该是新实例
        if _has_gcc():
            b = get_c_core(keep_alive=False)
            assert b is not None

    def test_close_idempotent(self):
        """close 可重复调用"""
        if not _has_gcc():
            pytest.skip("无 gcc")
        bridge = CCoreBridge(keep_alive=True)
        bridge.close()
        bridge.close()  # 不抛异常


# ============================================================
# 协议正确性 — 验证响应是合法 JSON 且字段齐全
# ============================================================

@pytest.mark.skipif(not _has_gcc(), reason="无 gcc")
class TestCProtocolContract:
    """C 核心响应协议契约测试"""

    def test_heartbeat_response_schema(self):
        bridge = CCoreBridge(keep_alive=False)
        resp = bridge.heartbeat()
        assert set(resp.keys()) >= {"status", "cmd", "data"}
        assert set(resp["data"].keys()) >= {
            "cycle", "generation", "fitness",
            "balance", "health", "mutations", "knowledge",
        }

    def test_status_response_schema(self):
        bridge = CCoreBridge(keep_alive=False)
        resp = bridge.status()
        assert set(resp["data"].keys()) >= {
            "name", "generation", "fitness", "cycle",
            "balance", "health", "mutations", "knowledge", "repairs",
        }


@pytest.mark.skipif(not _has_cargo(), reason="无 cargo")
class TestRustProtocolContract:
    """Rust 引擎响应协议契约测试"""

    def test_status_response_schema(self):
        bridge = RustEngineBridge(keep_alive=False)
        resp = bridge.status()
        assert set(resp.keys()) >= {"status", "cmd", "data"}
        assert set(resp["data"].keys()) >= {
            "genes", "balance", "cycle",
            "mutations", "retentions", "forgets",
        }

    def test_mutate_response_schema(self):
        bridge = RustEngineBridge(keep_alive=False)
        resp = bridge.mutate(domain="test", change=0.1)
        assert set(resp["data"].keys()) >= {
            "gene_id", "domain", "strength", "balance", "total_genes",
        }
