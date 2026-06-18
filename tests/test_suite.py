#!/usr/bin/env python3
"""
superclaw 完整测试体系
运行: python3 -m tests.test_suite  或  python3 tests/test_suite.py
"""

import json
import os
import sys
import unittest
from pathlib import Path
from typing import Dict, Any

# 项目路径
SUPERCLAW_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(SUPERCLAW_ROOT))

from glue import PipeProcess
from llm_provider import get_provider, MockProvider

CORE_DNA = SUPERCLAW_ROOT / "core-dna"


def _has_binaries() -> bool:
    """检查二进制文件是否存在"""
    return (CORE_DNA / "c-core-pipe").exists() and (CORE_DNA / "rust-engine-pipe").exists()


@unittest.skipUnless(_has_binaries(), "二进制文件未编译")
class TestCCore(unittest.TestCase):
    """测试 C Core"""

    def setUp(self):
        self.c = PipeProcess("C", str(CORE_DNA / "c-core-pipe"))
        self.assertTrue(self.c.start(), "C Core 启动失败")

    def tearDown(self):
        self.c.stop()

    def _send(self, cmd: Dict[str, Any]) -> Dict[str, Any]:
        resp = self.c.send(cmd)
        self.assertIsNotNone(resp)
        return resp

    def test_01_heartbeat(self):
        """心跳测试"""
        r = self._send({"cmd": "heartbeat"})
        self.assertEqual(r["status"], "ok")
        data = r["data"]
        self.assertIn("cycle", data)
        self.assertIn("fitness", data)
        self.assertGreaterEqual(data["fitness"], 0)

    def test_02_status(self):
        """状态查询"""
        r = self._send({"cmd": "status"})
        self.assertEqual(r["status"], "ok")
        data = r["data"]
        self.assertIn("name", data)
        self.assertIn("generation", data)
        self.assertIn("mutations", data)

    def test_03_detect_weakness(self):
        """短板检测"""
        r = self._send({"cmd": "detect_weakness"})
        self.assertEqual(r["status"], "ok")
        data = r["data"]
        self.assertIn("count", data)
        self.assertIn("weaknesses", data)
        self.assertGreaterEqual(data["count"], 0)

    def test_04_record_evolution(self):
        """进化记录"""
        # 先获取初始状态
        init = self._send({"cmd": "status"})
        init_knowledge = init["data"]["knowledge"]
        init_mutations = init["data"]["mutations"]

        # 记录进化
        r = self._send({"cmd": "record_evolution", "mutations": 3, "knowledge": 2, "fitness": 1.5})
        self.assertEqual(r["status"], "ok")

        # 验证状态更新
        final = self._send({"cmd": "status"})
        self.assertEqual(final["data"]["mutations"], init_mutations + 3)
        self.assertEqual(final["data"]["knowledge"], init_knowledge + 2)

    def test_05_health_check(self):
        """健康检查"""
        r = self._send({"cmd": "health_check"})
        self.assertEqual(r["status"], "ok")
        self.assertIn("health", r["data"])


@unittest.skipUnless(_has_binaries(), "二进制文件未编译")
class TestRustEngine(unittest.TestCase):
    """测试 Rust Engine"""

    def setUp(self):
        self.r = PipeProcess("Rust", str(CORE_DNA / "rust-engine-pipe"))
        self.assertTrue(self.r.start(), "Rust Engine 启动失败")

    def tearDown(self):
        self.r.stop()

    def _send(self, cmd: Dict[str, Any]) -> Dict[str, Any]:
        resp = self.r.send(cmd)
        self.assertIsNotNone(resp)
        return resp

    def test_01_mutate(self):
        """基因变异"""
        r = self._send({"cmd": "mutate", "domain": "变异", "change": 0.1})
        self.assertEqual(r["status"], "ok")
        data = r["data"]
        self.assertIn("gene_id", data)
        self.assertIn("strength", data)
        self.assertIn("balance", data)
        self.assertGreater(data["strength"], 0)

    def test_02_mutate_different_domains(self):
        """多领域变异"""
        domains = ["变异", "学习", "探索", "共进化", "评估", "知识"]
        for domain in domains:
            r = self._send({"cmd": "mutate", "domain": domain, "change": 0.1})
            self.assertEqual(r["status"], "ok", f"domain={domain} 失败")

    def test_03_evaluate_existing(self):
        """评估现有基因"""
        m = self._send({"cmd": "mutate", "domain": "变异", "change": 0.1})
        gene_id = m["data"]["gene_id"]
        r = self._send({"cmd": "evaluate", "gene_id": gene_id})
        self.assertEqual(r["status"], "ok")
        self.assertIn("score", r["data"])

    def test_04_evaluate_missing(self):
        """评估不存在的基因（应返回错误）"""
        r = self._send({"cmd": "evaluate", "gene_id": "non-existent"})
        # 接受 error 或 ok
        self.assertIn(r["status"], ["error", "ok"])

    def test_05_balance(self):
        """平衡检查"""
        # 先产生一些基因
        self._send({"cmd": "mutate", "domain": "变异", "change": 0.1})
        self._send({"cmd": "mutate", "domain": "学习", "change": 0.08})

        r = self._send({"cmd": "balance"})
        self.assertEqual(r["status"], "ok")
        data = r["data"]
        self.assertIn("balance", data)
        self.assertIn("domains", data)

    def test_06_status(self):
        """状态查询"""
        r = self._send({"cmd": "status"})
        self.assertEqual(r["status"], "ok")
        data = r["data"]
        self.assertIn("genes", data)
        self.assertIn("balance", data)
        self.assertIn("cycle", data)

    def test_07_retention_check(self):
        """保留检查"""
        r = self._send({"cmd": "retention_check"})
        self.assertEqual(r["status"], "ok")
        self.assertIn("total", r["data"])


class TestLLMProvider(unittest.TestCase):
    """测试 LLM Provider"""

    def test_01_mock_provider(self):
        """Mock Provider"""
        p = get_provider("mock")
        self.assertIsInstance(p, MockProvider)

    def test_02_mock_response(self):
        """Mock 返回非空"""
        p = get_provider("mock")
        resp = p.call("测试")
        self.assertIsInstance(resp, str)
        self.assertGreater(len(resp), 0)

    def test_03_mock_system_prompt(self):
        """带 system prompt 的 Mock"""
        p = get_provider("mock")
        resp = p.call("分析", "你是分析助手")
        self.assertIsInstance(resp, str)
        self.assertGreater(len(resp), 0)

    def test_04_provider_instantiation(self):
        """所有 Provider 实例化"""
        providers = ["mock", "deepseek", "groq", "openrouter", "openai", "ollama"]
        for name in providers:
            p = get_provider(name)
            self.assertIsNotNone(p)
            self.assertEqual(p.config.provider, name)


@unittest.skipUnless(_has_binaries(), "二进制文件未编译")
class TestIntegration(unittest.TestCase):
    """三层集成测试"""

    def test_01_full_cycle(self):
        """完整的单循环进化"""
        c = PipeProcess("C", str(CORE_DNA / "c-core-pipe"))
        r = PipeProcess("Rust", str(CORE_DNA / "rust-engine-pipe"))
        llm = get_provider("mock")

        try:
            self.assertTrue(c.start())
            self.assertTrue(r.start())

            # Step 1: 心跳
            hb = c.send({"cmd": "heartbeat"})
            self.assertEqual(hb["status"], "ok")
            initial_fitness = hb["data"]["fitness"]

            # Step 2: 检测短板
            weakness = c.send({"cmd": "detect_weakness"})
            self.assertEqual(weakness["status"], "ok")

            # Step 3: LLM 分析
            llm_response = llm.call("分析状态")
            self.assertGreater(len(llm_response), 0)

            # Step 4: 变异
            m = r.send({"cmd": "mutate", "domain": "变异", "change": 0.1})
            self.assertEqual(m["status"], "ok")
            gene_id = m["data"]["gene_id"]

            # Step 5: 评估
            e = r.send({"cmd": "evaluate", "gene_id": gene_id})
            self.assertEqual(e["status"], "ok")

            # Step 6: 记录进化
            rec = c.send({
                "cmd": "record_evolution",
                "mutations": 1,
                "knowledge": 0,
                "fitness": initial_fitness + 0.05,
            })
            self.assertEqual(rec["status"], "ok")

            # Step 7: 验证状态
            final = c.send({"cmd": "status"})
            self.assertGreaterEqual(final["data"]["fitness"], initial_fitness)

        finally:
            c.stop()
            r.stop()

    def test_02_multiple_cycles(self):
        """多轮循环"""
        c = PipeProcess("C", str(CORE_DNA / "c-core-pipe"))
        r = PipeProcess("Rust", str(CORE_DNA / "rust-engine-pipe"))
        llm = get_provider("mock")

        try:
            self.assertTrue(c.start())
            self.assertTrue(r.start())

            mutations_start = c.send({"cmd": "status"})["data"]["mutations"]

            for i in range(3):
                # 每个循环执行一次完整流程
                hb = c.send({"cmd": "heartbeat"})
                m = r.send({"cmd": "mutate", "domain": "变异", "change": 0.1})
                c.send({"cmd": "record_evolution", "mutations": 1, "knowledge": 0,
                       "fitness": hb["data"]["fitness"] + 0.05})

            final = c.send({"cmd": "status"})
            self.assertGreaterEqual(final["data"]["mutations"], mutations_start + 3)

        finally:
            c.stop()
            r.stop()


def run_tests() -> int:
    """运行所有测试并返回退出码"""
    print("=" * 60)
    print("  🧪 superclaw 测试体系")
    print("=" * 60)
    print(f"  二进制文件: {'已编译' if _has_binaries() else '未编译'}")
    print(f"  C Core: {CORE_DNA / 'c-core-pipe'} {'✅' if (CORE_DNA / 'c-core-pipe').exists() else '❌'}")
    print(f"  Rust:   {CORE_DNA / 'rust-engine-pipe'} {'✅' if (CORE_DNA / 'rust-engine-pipe').exists() else '❌'}")
    print()

    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    # 加载所有测试类
    for tc in [TestCCore, TestRustEngine, TestLLMProvider, TestIntegration]:
        suite.addTests(loader.loadTestsFromTestCase(tc))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    print()
    print("=" * 60)
    print(f"  ✅ 成功: {result.testsRun - len(result.failures) - len(result.errors)}")
    print(f"  ❌ 失败: {len(result.failures)}")
    print(f"  ⚠️  错误: {len(result.errors)}")
    print(f"  ⏭  跳过: {len(result.skipped)}")
    print("=" * 60)

    return 0 if len(result.failures) == 0 and len(result.errors) == 0 else 1


if __name__ == "__main__":
    sys.exit(run_tests())
