#!/usr/bin/env python3
"""
superclaw GEP + APEX + LLM + 记忆系统 全量端到端验证
"""
import sys
import tempfile
from pathlib import Path

SUPERCLAW_ROOT = Path(__file__).parent.resolve()
sys.path.insert(0, str(SUPERCLAW_ROOT))

passed = 0
failed = 0
errors = []


def ok(name, detail=""):
    global passed
    passed += 1
    print(f"  ✓ {name}" + (f" — {detail}" if detail else ""))


def fail(name, detail=""):
    global failed
    failed += 1
    errors.append(f"{name}: {detail}")
    print(f"  ✗ {name} — {detail}")


def test_gene_schema():
    """测试 Gene schema（对照 Evolver gene.js）"""
    print("\n[Test 1] Gene Schema")
    try:
        from superclaw.gep_schema import Gene, VALID_CATEGORIES, SCHEMA_VERSION

        # 创建 Gene
        gene = Gene(
            category="repair",
            signals_match=["错误: 模块A 崩溃"],
            strategy=["重启模块", "检查依赖"],
            summary="修复模块A崩溃",
        )

        if gene.id.startswith("gene-"):
            ok(f"Gene ID 自动生成: {gene.id}")
        else:
            fail("Gene ID", gene.id)

        if gene.category == "repair":
            ok("Gene category 正确")
        else:
            fail("category", gene.category)

        if gene.schema_version == SCHEMA_VERSION:
            ok(f"Schema 版本: {SCHEMA_VERSION}")
        else:
            fail("schema_version", gene.schema_version)

        # 验证
        if gene.validate():
            ok("Gene validate() 通过")

        # 无效 category 应被规范化
        gene2 = Gene(category="invalid_category")
        if gene2.category == "innovate":
            ok("无效 category 规范化为 innovate")
        else:
            fail("category 规范化", gene2.category)

        # 序列化/反序列化
        d = gene.to_dict()
        gene3 = Gene.from_dict(d)
        if gene3.id == gene.id and gene3.category == gene.category:
            ok("Gene 序列化/反序列化一致")
        else:
            fail("序列化", f"{gene3.id} != {gene.id}")

        # VALID_CATEGORIES 对照 Evolver
        if VALID_CATEGORIES == ["repair", "optimize", "innovate", "explore"]:
            ok(f"VALID_CATEGORIES 对照 Evolver: {VALID_CATEGORIES}")
        else:
            fail("VALID_CATEGORIES", str(VALID_CATEGORIES))

    except Exception as e:
        fail("Gene Schema", str(e))
        import traceback
        traceback.print_exc()


def test_capsule_schema():
    """测试 Capsule schema（对照 Evolver capsule.js）"""
    print("\n[Test 2] Capsule Schema")
    try:
        from superclaw.gep_schema import Capsule, VALID_OUTCOME_STATUSES

        capsule = Capsule(
            trigger=["错误: 模块A 崩溃"],
            gene="gene-abc123",
            summary="成功修复模块A",
            confidence=0.85,
            outcome={"status": "success", "score": 0.9},
            source_type="generated",
        )

        if capsule.id.startswith("cap-"):
            ok(f"Capsule ID 自动生成: {capsule.id}")
        else:
            fail("Capsule ID", capsule.id)

        if capsule.outcome["status"] == "success":
            ok("outcome.status 正确")
        else:
            fail("outcome", str(capsule.outcome))

        if capsule.validate():
            ok("Capsule validate() 通过")

        # 无效 outcome.status 应被规范化
        cap2 = Capsule(outcome={"status": "invalid", "score": 0})
        if cap2.outcome["status"] == "failed":
            ok("无效 outcome.status 规范化为 failed")
        else:
            fail("outcome 规范化", cap2.outcome["status"])

        # VALID_OUTCOME_STATUSES 对照 Evolver
        if VALID_OUTCOME_STATUSES == ["success", "failed"]:
            ok(f"VALID_OUTCOME_STATUSES 对照 Evolver: {VALID_OUTCOME_STATUSES}")
        else:
            fail("VALID_OUTCOME_STATUSES", str(VALID_OUTCOME_STATUSES))

        # 序列化
        d = capsule.to_dict()
        cap3 = Capsule.from_dict(d)
        if cap3.id == capsule.id:
            ok("Capsule 序列化/反序列化一致")
        else:
            fail("序列化", f"{cap3.id} != {capsule.id}")

    except Exception as e:
        fail("Capsule Schema", str(e))


def test_evolution_event():
    """测试 EvolutionEvent（不可篡改日志）"""
    print("\n[Test 3] EvolutionEvent")
    try:
        from superclaw.gep_schema import EvolutionEvent

        event = EvolutionEvent(
            event_type="repair",
            gene_id="gene-abc123",
            capsule_id="cap-def456",
            strategy="balanced",
            trigger_signal="错误: 模块A 崩溃",
            summary="修复模块A",
            phi_before=0.5,
            phi_after=0.6,
            tier_before=2,
            tier_after=2,
            success=True,
        )

        if event.event_id.startswith("evt-"):
            ok(f"Event ID 自动生成: {event.event_id}")
        else:
            fail("Event ID", event.event_id)

        if event.hash and len(event.hash) == 64:
            ok(f"SHA-256 哈希: {event.hash[:16]}...")
        else:
            fail("hash", str(event.hash))

        # 验证完整性
        if event.verify():
            ok("Event 完整性验证通过")
        else:
            fail("verify", "哈希不匹配")

        # 篡改检测
        event.phi_after = 999.0
        if not event.verify():
            ok("篡改检测: 修改 phi_after 后验证失败")
        else:
            fail("篡改检测", "应该检测到篡改")

    except Exception as e:
        fail("EvolutionEvent", str(e))


def test_gene_library():
    """测试 GeneLibrary 持久化"""
    print("\n[Test 4] GeneLibrary 持久化")
    try:
        from superclaw.gep_schema import GeneLibrary, Gene, Capsule

        with tempfile.TemporaryDirectory() as tmpdir:
            lib = GeneLibrary(Path(tmpdir))

            # 初始为空
            if len(lib.load_genes()) == 0:
                ok("初始 Gene 库为空")
            else:
                fail("初始", "非空")

            # 添加 Gene
            gene = Gene(category="repair", summary="测试 Gene")
            if lib.upsert_gene(gene):
                ok("upsert_gene 成功")

            if len(lib.load_genes()) == 1:
                ok("Gene 库有 1 个 Gene")
            else:
                fail("Gene 数量", str(len(lib.load_genes())))

            # 查找
            found = lib.find_gene(gene.id)
            if found and found.id == gene.id:
                ok("find_gene 成功")
            else:
                fail("find_gene", "未找到")

            # 按信号查找
            gene2 = Gene(
                category="optimize",
                signals_match=["性能: 启动慢"],
                summary="优化启动"
            )
            lib.upsert_gene(gene2)
            matches = lib.find_by_signal("启动慢")
            if len(matches) >= 1:
                ok(f"find_by_signal 找到 {len(matches)} 个匹配")
            else:
                fail("find_by_signal", "无匹配")

            # 添加 Capsule
            capsule = Capsule(
                gene=gene.id,
                outcome={"status": "success", "score": 0.8},
                summary="测试 Capsule"
            )
            if lib.upsert_capsule(capsule):
                ok("upsert_capsule 成功")

            # 统计
            stats = lib.stats()
            if stats["total_genes"] == 2 and stats["total_capsules"] == 1:
                ok(f"统计: {stats['total_genes']} genes, {stats['total_capsules']} capsules")
            else:
                fail("统计", str(stats))

            # 事件日志
            from superclaw.gep_schema import EvolutionEvent
            event = EvolutionEvent(event_type="repair", success=True)
            lib.append_event(event)
            events = lib.load_events(10)
            if len(events) == 1:
                ok("事件日志追加成功")
            else:
                fail("事件日志", str(len(events)))

    except Exception as e:
        fail("GeneLibrary", str(e))
        import traceback
        traceback.print_exc()


def test_strategy_manager():
    """测试策略管理器（70/30 法则）"""
    print("\n[Test 5] StrategyManager 策略管理")
    try:
        from superclaw.gep_engine import StrategyManager
        from superclaw.gep_schema import Signal, VALID_STRATEGIES

        # 验证策略列表对照 Evolver
        expected = ["balanced", "innovate", "harden", "repair-only",
                    "early-stabilize", "steady-state", "auto"]
        if VALID_STRATEGIES == expected:
            ok(f"策略列表对照 Evolver: {len(VALID_STRATEGIES)} 种")
        else:
            fail("策略列表", str(VALID_STRATEGIES))

        # balanced 策略
        sm = StrategyManager("balanced")
        signals = [Signal(signal_type="error", pattern="测试错误")]

        # 运行多次，验证 repair 和 innovate 都可能出现
        categories = set()
        for _ in range(50):
            cat = sm.select_category(signals)
            categories.add(cat)

        if "repair" in categories:
            ok("balanced 策略会产生 repair")
        else:
            fail("balanced repair", str(categories))

        # repair-only 策略
        sm2 = StrategyManager("repair-only")
        cat = sm2.select_category(signals)
        if cat == "repair":
            ok("repair-only 策略只产生 repair")
        else:
            fail("repair-only", cat)

        # should_solidify
        from superclaw.gep_schema import Capsule
        good_capsule = Capsule(outcome={"status": "success", "score": 0.8})
        bad_capsule = Capsule(outcome={"status": "failed", "score": 0.3})

        if sm.should_solidify(good_capsule) and not sm.should_solidify(bad_capsule):
            ok("should_solidify 判断正确")
        else:
            fail("should_solidify", "判断错误")

    except Exception as e:
        fail("StrategyManager", str(e))


def test_signal_extractor():
    """测试信号提取器（对照 analyzer.js）"""
    print("\n[Test 6] SignalExtractor 信号提取")
    try:
        from superclaw.gep_engine import SignalExtractor
        from superclaw.memory import MemoryStore
        from superclaw.llm_router import LLMRouter

        with tempfile.TemporaryDirectory() as tmpdir:
            memory = MemoryStore(Path(tmpdir))
            llm = LLMRouter()
            llm.add_provider("mock", priority=1)

            extractor = SignalExtractor(memory, llm)

            # 先制造一些反思记录
            memory.reflection.reflect({
                "phi": 0.3, "tier": 1, "fitness": 0.4,
                "mutations": 1, "knowledge": 0,
            })

            signals = extractor.scan()

            if len(signals) > 0:
                ok(f"提取到 {len(signals)} 个信号")
            else:
                fail("信号提取", "0 个信号")

            # 验证信号类型
            types = set(s.signal_type for s in signals)
            if "feature" in types or "error" in types:
                ok(f"信号类型: {types}")
            else:
                fail("信号类型", str(types))

            # 验证信号来源
            sources = set(s.source for s in signals)
            if any("reflection" in s for s in sources):
                ok(f"信号来源包含反思: {sources}")
            else:
                fail("信号来源", str(sources))

    except Exception as e:
        fail("SignalExtractor", str(e))
        import traceback
        traceback.print_exc()


def test_gep_engine_e2e():
    """测试 GEP 引擎端到端（10步进化循环）"""
    print("\n[Test 7] GEPEngine 端到端 10 步循环")
    try:
        from superclaw.gep_engine import GEPEngine
        from superclaw.memory import MemoryStore
        from superclaw.llm_router import LLMRouter

        with tempfile.TemporaryDirectory() as tmpdir:
            memory = MemoryStore(Path(tmpdir))
            llm = LLMRouter()
            llm.add_provider("mock", priority=1)

            engine = GEPEngine(
                memory=memory, llm=llm,
                strategy="balanced",
                workspace=Path(tmpdir),
            )

            # 先制造一些反思记录（让信号提取有数据）
            memory.reflection.reflect({
                "phi": 0.3, "tier": 1, "fitness": 0.4,
                "mutations": 1, "knowledge": 0,
            })

            # 运行一个循环
            result = engine.run_cycle()

            # 验证 10 步都执行了
            steps = result.get("steps", {})
            expected_steps = [
                "1_scan_logs", "2_extract_signals", "3_select_gene",
                "4_generate_prompt", "5_execute_modify", "6_validate",
                "7_solidify", "8_publish", "9_log_event", "10_monitor",
            ]

            for step_name in expected_steps:
                if step_name in steps:
                    ok(f"Step {step_name} 执行")
                else:
                    fail(f"Step {step_name}", "缺失")

            # 验证循环状态
            if result.get("status") in ("success", "failed", "no_signals"):
                ok(f"循环状态: {result['status']}")
            else:
                fail("循环状态", str(result.get("status")))

            # 验证 LLM 被调用
            step5 = steps.get("5_execute_modify", {})
            if step5.get("provider") == "mock":
                ok("LLM (mock) 被调用")
            else:
                fail("LLM 调用", str(step5))

            # 验证事件被记录
            step9 = steps.get("9_log_event", {})
            if step9.get("event_id"):
                ok(f"EvolutionEvent 记录: {step9['event_id']}")
            else:
                fail("Event 记录", "无 event_id")

            # 验证记忆系统被更新
            evo_history = memory.evolution.recent(5)
            if len(evo_history) > 0:
                ok(f"记忆系统进化历史更新: {len(evo_history)} 条")
            else:
                fail("记忆系统", "未更新")

    except Exception as e:
        fail("GEPEngine E2E", str(e))
        import traceback
        traceback.print_exc()


def test_gep_apex_integration():
    """测试 GEP 与 APEX 记忆系统的关联"""
    print("\n[Test 8] GEP ↔ APEX 记忆系统关联")
    try:
        from superclaw.gep_engine import GEPEngine
        from superclaw.memory import MemoryStore
        from superclaw.llm_router import LLMRouter

        with tempfile.TemporaryDirectory() as tmpdir:
            memory = MemoryStore(Path(tmpdir))
            llm = LLMRouter()
            llm.add_provider("mock", priority=1)

            engine = GEPEngine(
                memory=memory, llm=llm,
                strategy="balanced",
                workspace=Path(tmpdir),
            )

            # 制造初始反思
            memory.reflection.reflect({
                "phi": 0.3, "tier": 1, "fitness": 0.4,
                "mutations": 1, "knowledge": 0,
            })

            # 运行 3 个循环
            for i in range(3):
                engine.run_cycle()

            # 验证关联：
            # 1. Gene 库有 Gene
            genes = engine.library.load_genes()
            if len(genes) > 0:
                ok(f"Gene 库有 {len(genes)} 个 Gene")
            else:
                fail("Gene 库", "空")

            # 2. 进化历史有记录
            history = memory.evolution.recent(10)
            if len(history) >= 3:
                ok(f"进化历史有 {len(history)} 条记录")
            else:
                fail("进化历史", f"只有 {len(history)} 条")

            # 3. 反思日志有更新
            reflections = memory.reflection.history(10)
            if len(reflections) >= 4:  # 初始1 + 3循环
                ok(f"反思日志有 {len(reflections)} 条")
            else:
                fail("反思日志", f"只有 {len(reflections)} 条")

            # 4. 事件日志有记录
            events = engine.library.load_events(10)
            if len(events) >= 3:
                ok(f"事件日志有 {len(events)} 条")
            else:
                fail("事件日志", f"只有 {len(events)} 条")

            # 5. Gene 的 learning_history 被更新
            if genes:
                g = genes[0]
                if len(g.learning_history) > 0:
                    ok(f"Gene learning_history 有 {len(g.learning_history)} 条")
                else:
                    fail("learning_history", "空")

    except Exception as e:
        fail("GEP-APEX 关联", str(e))
        import traceback
        traceback.print_exc()


def test_llm_gep_integration():
    """测试 LLM Router 与 GEP 的融合"""
    print("\n[Test 9] LLM Router ↔ GEP 融合")
    try:
        from superclaw.gep_engine import GEPEngine
        from superclaw.memory import MemoryStore
        from superclaw.llm_router import LLMRouter

        with tempfile.TemporaryDirectory() as tmpdir:
            memory = MemoryStore(Path(tmpdir))

            # 测试故障转移：deepseek 无 key → fallback 到 mock
            llm = LLMRouter()
            llm.add_provider("deepseek", api_key="", model="deepseek-chat", priority=1)
            llm.add_provider("mock", priority=2)

            engine = GEPEngine(
                memory=memory, llm=llm,
                strategy="innovate",
                workspace=Path(tmpdir),
            )

            memory.reflection.reflect({
                "phi": 0.3, "tier": 1, "fitness": 0.4,
                "mutations": 1, "knowledge": 0,
            })

            result = engine.run_cycle()

            step5 = result.get("steps", {}).get("5_execute_modify", {})
            # 应该 fallback 到 mock
            if step5.get("provider") == "mock":
                ok("LLM 故障转移: deepseek 失败 → mock 兜底")
            else:
                fail("故障转移", f"provider={step5.get('provider')}")

            # 验证 LLM 状态
            status = llm.status()
            if "deepseek" in status["providers"]:
                ds = status["providers"]["deepseek"]
                if ds["failures"] > 0:
                    ok(f"deepseek 失败计数: {ds['failures']}")
                else:
                    fail("失败计数", "为 0")

    except Exception as e:
        fail("LLM-GEP 融合", str(e))


def test_multi_cycle_evolution():
    """测试多循环进化（验证系统持续进化）"""
    print("\n[Test 10] 多循环进化")
    try:
        from superclaw.gep_engine import GEPEngine
        from superclaw.memory import MemoryStore
        from superclaw.llm_router import LLMRouter

        with tempfile.TemporaryDirectory() as tmpdir:
            memory = MemoryStore(Path(tmpdir))
            llm = LLMRouter()
            llm.add_provider("mock", priority=1)

            engine = GEPEngine(
                memory=memory, llm=llm,
                strategy="balanced",
                workspace=Path(tmpdir),
            )

            memory.reflection.reflect({
                "phi": 0.3, "tier": 1, "fitness": 0.4,
                "mutations": 1, "knowledge": 0,
            })

            # 运行 5 个循环
            results = engine.run(cycles=5, verbose=False)

            if len(results) == 5:
                ok("完成 5 个进化循环")
            else:
                fail("循环数", f"{len(results)} != 5")

            # 验证系统状态增长
            final_stats = engine.library.stats()
            if final_stats["total_events"] >= 5:
                ok(f"事件日志: {final_stats['total_events']} 条")
            else:
                fail("事件日志", str(final_stats["total_events"]))

            # 验证进化历史增长
            evo_summary = memory.evolution.summary()
            if evo_summary["total_cycles"] >= 5:
                ok(f"进化历史: {evo_summary['total_cycles']} 循环")
            else:
                fail("进化历史", str(evo_summary["total_cycles"]))

    except Exception as e:
        fail("多循环进化", str(e))


def main():
    print("=" * 60)
    print("  🧪 superclaw GEP + APEX + LLM + 记忆系统 全量验证")
    print("=" * 60)

    test_gene_schema()
    test_capsule_schema()
    test_evolution_event()
    test_gene_library()
    test_strategy_manager()
    test_signal_extractor()
    test_gep_engine_e2e()
    test_gep_apex_integration()
    test_llm_gep_integration()
    test_multi_cycle_evolution()

    print(f"\n{'=' * 60}")
    print(f"  结果:  ✓ {passed} 通过  |  ✗ {failed} 失败")
    if errors:
        print("\n  失败详情:")
        for e in errors:
            print(f"    - {e}")
    print(f"{'=' * 60}")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
