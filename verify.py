#!/usr/bin/env python3
"""
superclaw 全量验证脚本
验证所有组件：C Core / Rust Engine / Python Glue / APEX 框架
"""
import json
import subprocess
import sys
from pathlib import Path

SUPERCLAW_ROOT = Path(__file__).parent.resolve()
CORE_DNA = SUPERCLAW_ROOT / "core-dna"
C_CORE_BIN = CORE_DNA / "c-core-pipe"
RUST_BIN = CORE_DNA / "rust-engine-pipe"

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


def test_c_core_compile():
    """测试 C Core 编译"""
    print("\n[Test 1] C Core 编译")
    src = CORE_DNA / "main_pipe.c"
    if not src.exists():
        fail("main_pipe.c 存在", f"{src} 不存在")
        return
    ok("main_pipe.c 存在")

    result = subprocess.run(
        ["gcc", "-O2", "-Wall", "-Wextra", "-std=c11",
         "-D_POSIX_C_SOURCE=200809L", "-o", str(C_CORE_BIN),
         str(src), "-lm"],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        ok("gcc 编译成功")
    else:
        fail("gcc 编译", result.stderr[:200])

    if C_CORE_BIN.exists():
        ok("二进制生成", f"{C_CORE_BIN.stat().st_size} bytes")
    else:
        fail("二进制生成", "文件不存在")


def test_c_core_pipe():
    """测试 C Core 管道通信"""
    print("\n[Test 2] C Core 管道通信")
    if not C_CORE_BIN.exists():
        fail("C Core", "二进制不存在，跳过")
        return

    cmds = [
        '{"cmd":"heartbeat"}',
        '{"cmd":"detect_weakness"}',
        '{"cmd":"record_evolution","mutations":5,"knowledge":3,"fitness":1.2}',
        '{"cmd":"status"}',
        '{"cmd":"health_check"}',
    ]
    stdin_data = "\n".join(cmds) + "\n"

    try:
        result = subprocess.run(
            [str(C_CORE_BIN)],
            input=stdin_data, capture_output=True, text=True, timeout=5
        )
        lines = [l.strip() for l in result.stdout.strip().split("\n") if l.strip()]

        # 第一行是 ready
        if lines and json.loads(lines[0]).get("status") == "ready":
            ok("ready 消息")
        else:
            fail("ready 消息", lines[0] if lines else "无输出")
            return

        # 验证每个命令的响应
        responses = lines[1:]  # 去掉 ready
        expected_cmds = ["heartbeat", "detect_weakness", "record_evolution", "status", "health_check"]

        for i, expected in enumerate(expected_cmds):
            if i >= len(responses):
                fail(f"命令 {expected}", "无响应")
                continue
            try:
                resp = json.loads(responses[i])
                if resp.get("status") == "ok" and resp.get("cmd") == expected:
                    ok(f"命令 {expected}")
                else:
                    fail(f"命令 {expected}", f"status={resp.get('status')}, cmd={resp.get('cmd')}")
            except json.JSONDecodeError as e:
                fail(f"命令 {expected}", f"JSON 解析失败: {e}")

        # 验证 record_evolution 的数值正确性
        if len(responses) >= 3:
            try:
                rec = json.loads(responses[2])
                data = rec.get("data", {})
                if data.get("total_mutations") == 5 and data.get("total_knowledge") == 3:
                    ok("record_evolution 数值正确")
                else:
                    fail("record_evolution 数值", f"mutations={data.get('total_mutations')}, knowledge={data.get('total_knowledge')}")
            except Exception as e:
                fail("record_evolution 数值", str(e))

    except subprocess.TimeoutExpired:
        fail("C Core 管道", "超时")
    except Exception as e:
        fail("C Core 管道", str(e))


def test_rust_compile():
    """测试 Rust Engine 编译"""
    print("\n[Test 3] Rust Engine 编译")
    cargo_dir = CORE_DNA / "rust_pipe"
    if not (cargo_dir / "Cargo.toml").exists():
        fail("Cargo.toml", "不存在")
        return

    result = subprocess.run(
        ["cargo", "build", "--release"],
        cwd=str(cargo_dir), capture_output=True, text=True
    )
    if result.returncode == 0:
        ok("cargo build 成功")
    else:
        fail("cargo build", result.stderr[:200])
        return

    # 复制到 core-dna/
    src_bin = cargo_dir / "target" / "release" / "rust-engine-pipe"
    if src_bin.exists():
        import shutil
        shutil.copy(str(src_bin), str(RUST_BIN))
        ok("二进制复制", f"{RUST_BIN.stat().st_size} bytes")
    else:
        fail("二进制", "rust-engine-pipe 未生成")


def test_rust_pipe():
    """测试 Rust Engine 管道通信"""
    print("\n[Test 4] Rust Engine 管道通信")
    if not RUST_BIN.exists():
        fail("Rust Engine", "二进制不存在，跳过")
        return

    cmds = [
        '{"cmd":"mutate","domain":"变异","change":0.1}',
        '{"cmd":"mutate","domain":"学习","change":0.08}',
        '{"cmd":"balance"}',
        '{"cmd":"status"}',
        '{"cmd":"retention_check"}',
    ]
    stdin_data = "\n".join(cmds) + "\n"

    try:
        result = subprocess.run(
            [str(RUST_BIN)],
            input=stdin_data, capture_output=True, text=True, timeout=5
        )
        lines = [l.strip() for l in result.stdout.strip().split("\n") if l.strip()]

        if lines and json.loads(lines[0]).get("status") == "ready":
            ok("ready 消息")
        else:
            fail("ready 消息", lines[0] if lines else "无输出")
            return

        responses = lines[1:]
        expected_cmds = ["mutate", "mutate", "balance", "status", "retention_check"]

        for i, expected in enumerate(expected_cmds):
            if i >= len(responses):
                fail(f"命令 {expected}", "无响应")
                continue
            try:
                resp = json.loads(responses[i])
                if resp.get("status") == "ok" and resp.get("cmd") == expected:
                    ok(f"命令 {expected}")
                else:
                    fail(f"命令 {expected}", f"status={resp.get('status')}")
            except json.JSONDecodeError as e:
                fail(f"命令 {expected}", f"JSON 解析失败: {e}")

        # 验证 mutate 返回的 gene_id
        if len(responses) >= 1:
            try:
                m = json.loads(responses[0])
                gene_id = m.get("data", {}).get("gene_id", "")
                if gene_id.startswith("gene-"):
                    ok(f"gene_id 格式正确: {gene_id}")
                else:
                    fail("gene_id 格式", gene_id)
            except Exception as e:
                fail("gene_id", str(e))

        # 验证 balance 的 domains
        if len(responses) >= 3:
            try:
                b = json.loads(responses[2])
                domains = b.get("data", {}).get("domains", {})
                if "变异" in domains and "学习" in domains:
                    ok("balance domains 正确", json.dumps(domains, ensure_ascii=False))
                else:
                    fail("balance domains", json.dumps(domains, ensure_ascii=False))
            except Exception as e:
                fail("balance domains", str(e))

    except subprocess.TimeoutExpired:
        fail("Rust 管道", "超时")
    except Exception as e:
        fail("Rust 管道", str(e))


def test_apex_state():
    """测试 APEX 状态计算"""
    print("\n[Test 5] APEX 框架")
    sys.path.insert(0, str(SUPERCLAW_ROOT))
    try:
        from glue import ApexState
        apex = ApexState()

        # 初始状态
        if apex.phi == 0.0:
            ok("初始 Φ=0")
        else:
            fail("初始 Φ", f"应为 0, 实际 {apex.phi}")

        # 更新状态
        apex.update(
            c_status={"fitness": 1.5, "health": 2},
            r_status={"balance": 0.8, "domains": {"变异": 1.0, "知识": 1.0}},
            mutations_delta=2
        )

        # Φ = (1.5 * 0.21 * 0.8 * 2) / 1.0 = 0.504
        expected_phi = (1.5 * 0.21 * 0.8 * 2) / 1.0
        if abs(apex.phi - expected_phi) < 0.001:
            ok(f"Φ 计算正确: {apex.phi:.6f}")
        else:
            fail("Φ 计算", f"期望 {expected_phi}, 实际 {apex.phi}")

        # Tier
        if apex.tier_level() == 2:  # 0.504 < 1.0 → T2
            ok(f"Tier 正确: T{apex.tier_level()}")
        else:
            fail("Tier", f"期望 T2, 实际 T{apex.tier_level()}")

        # 保存
        apex.save()
        state_file = SUPERCLAW_ROOT / "apex-state" / "apex-state.json"
        if state_file.exists():
            ok("APEX 状态持久化")
        else:
            fail("APEX 持久化", "文件未生成")

    except ImportError as e:
        fail("APEX 导入", str(e))
    except Exception as e:
        fail("APEX 测试", str(e))


def test_glue_integration():
    """测试 Glue 层端到端集成"""
    print("\n[Test 6] Glue 层端到端集成")
    if not C_CORE_BIN.exists() or not RUST_BIN.exists():
        fail("Glue 集成", "二进制不存在，跳过")
        return

    try:
        from glue import EvolutionLoop, LLMProvider
        llm = LLMProvider("mock")
        loop = EvolutionLoop(llm, max_cycles=2, verbose=False)

        if not loop.start():
            fail("Glue 启动", "启动失败")
            return
        ok("Glue 启动成功")

        # 运行 2 个循环
        for i in range(2):
            result = loop.run_cycle()
            if result.get("cycle") == i + 1:
                ok(f"循环 #{i+1} 完成")
            else:
                fail(f"循环 #{i+1}", "cycle 号不匹配")

            # 验证每个步骤
            steps = result.get("steps", {})
            required = ["heartbeat", "detect_weakness", "rust_status", "balance",
                       "apex", "llm_decision", "mutate"]
            missing = [s for s in required if s not in steps]
            if not missing:
                ok(f"循环 #{i+1} 所有步骤完整")
            else:
                fail(f"循环 #{i+1} 步骤", f"缺少: {missing}")

        # 验证 APEX 指标有变化
        if loop.apex.phi > 0:
            ok(f"APEX Φ > 0: {loop.apex.phi:.6f}")
        else:
            fail("APEX Φ", f"仍为 0: {loop.apex.phi}")

        loop.c_core.stop()
        loop.rust_engine.stop()

    except Exception as e:
        fail("Glue 集成", str(e))
        import traceback
        traceback.print_exc()


def test_llm_provider():
    """测试 LLM Provider"""
    print("\n[Test 7] LLM Provider")
    try:
        from glue import LLMProvider

        # Mock provider
        llm = LLMProvider("mock")
        resp = llm.call("测试", system="你是助手")
        if resp and len(resp) > 0:
            try:
                decision = json.loads(resp)
                if "domain" in decision and "change" in decision:
                    ok(f"Mock LLM 返回有效决策: {decision}")
                else:
                    fail("Mock LLM 决策", f"缺少字段: {decision}")
            except json.JSONDecodeError:
                fail("Mock LLM JSON", resp[:100])
        else:
            fail("Mock LLM", "空响应")

        # 验证未配置 API key 的 provider
        llm2 = LLMProvider("deepseek", api_key="")
        resp2 = llm2.call("测试")
        if "未配置" in resp2:
            ok("未配置 API key 正确报错")
        else:
            fail("API key 报错", resp2[:100])

    except Exception as e:
        fail("LLM Provider", str(e))


def main():
    print("=" * 60)
    print("  🧪 superclaw 全量验证")
    print("=" * 60)

    test_c_core_compile()
    test_c_core_pipe()
    test_rust_compile()
    test_rust_pipe()
    test_apex_state()
    test_llm_provider()
    test_glue_integration()

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
