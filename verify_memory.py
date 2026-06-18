#!/usr/bin/env python3
"""
superclaw 记忆系统 + md 融合 + skill 兼容 全量验证
"""
import sys
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


def test_memory_store():
    """测试 MemoryStore 基础功能"""
    print("\n[Test 1] MemoryStore 基础")
    try:
        from superclaw.memory import MemoryStore
        store = MemoryStore(SUPERCLAW_ROOT)

        # 知识库统计
        stats = store.knowledge.stats()
        if stats["total"] > 0:
            ok(f"知识库索引 {stats['total']} 个 md 文件",
               f"root={stats.get('root',0)}, memory={stats.get('memory',0)}, skills={stats.get('skills',0)}")
        else:
            fail("知识库索引", "0 个文件")

        # 必须包含核心 md
        paths = [e["path"] for e in store.knowledge.index]
        for must in ["SOUL.md", "AGENTS.md", "MEMORY.md"]:
            if any(p.endswith(must) for p in paths):
                ok(f"包含 {must}")
            else:
                fail(f"包含 {must}", "未找到")

        # skills 必须被索引
        skill_entries = store.knowledge.list_by_category("skills")
        if len(skill_entries) >= 2:
            ok(f"skills 被索引: {len(skill_entries)} 个")
        else:
            fail("skills 索引", f"只有 {len(skill_entries)} 个")

    except Exception as e:
        fail("MemoryStore", str(e))
        import traceback
        traceback.print_exc()


def test_knowledge_search():
    """测试 md 知识检索"""
    print("\n[Test 2] md 知识检索")
    try:
        from superclaw.memory import MemoryStore
        store = MemoryStore(SUPERCLAW_ROOT)

        # 检索 "进化"
        results = store.knowledge.search("进化", limit=3)
        if len(results) > 0:
            ok(f"检索 '进化' 返回 {len(results)} 条")
        else:
            fail("检索 '进化'", "无结果")

        # 检索 "skill"
        results = store.knowledge.search("skill", limit=3)
        if len(results) > 0:
            ok(f"检索 'skill' 返回 {len(results)} 条")
        else:
            fail("检索 'skill'", "无结果")

        # 检索 "SOUL" 应该匹配 SOUL.md
        results = store.knowledge.search("灵魂 身份", limit=5)
        paths = [r["path"] for r in results]
        if any("SOUL" in p for p in paths):
            ok("检索 '灵魂 身份' 命中 SOUL.md")
        else:
            fail("检索 '灵魂 身份'", f"未命中 SOUL.md, 返回: {paths}")

        # 读取文件
        content = store.read_file("SOUL.md")
        if content and "MiMoClaw" in content:
            ok("read_file 读取 SOUL.md 成功")
        else:
            fail("read_file", f"内容异常: {content[:50] if content else 'None'}")

    except Exception as e:
        fail("知识检索", str(e))


def test_self_reflection():
    """测试 APEX 四问反思"""
    print("\n[Test 3] APEX 四问反思")
    try:
        from superclaw.memory import SelfReflection
        reflect = SelfReflection(SUPERCLAW_ROOT / "apex-state" / "test-reflection.json")

        # 执行反思
        state = {"phi": 0.5, "tier": 2, "fitness": 0.6, "mutations": 3, "knowledge": 2}
        result = reflect.reflect(state)

        # 验证四问都有
        for key in ["gaps", "opportunities", "improvements", "problems"]:
            if key in result and len(result[key]) > 0:
                ok(f"反思 {key}: {len(result[key])} 条")
            else:
                fail(f"反思 {key}", "为空")

        # 验证持久化
        history = reflect.history(5)
        if len(history) >= 1:
            ok(f"反思历史持久化: {len(history)} 条")
        else:
            fail("反思持久化", "无历史")

        # 清理测试文件
        test_file = SUPERCLAW_ROOT / "apex-state" / "test-reflection.json"
        if test_file.exists():
            test_file.unlink()

    except Exception as e:
        fail("四问反思", str(e))


def test_evolution_history():
    """测试进化历史记录"""
    print("\n[Test 4] 进化历史记录")
    try:
        from superclaw.memory import EvolutionHistory
        hist = EvolutionHistory(SUPERCLAW_ROOT / "logs" / "test-evolution.jsonl")

        # 记录几条
        hist.record(1, 0.5, "变异", "gene-1-1", 0.8, True, 2)
        hist.record(2, 0.6, "知识", "gene-2-1", 0.7, True, 2)
        hist.record(3, 0.7, "探索", "gene-3-1", 0.3, False, 3)

        recent = hist.recent(5)
        if len(recent) == 3:
            ok("记录 3 条进化历史")
        else:
            fail("进化历史", f"期望 3 条, 实际 {len(recent)}")

        # 摘要
        summary = hist.summary()
        if summary["total_cycles"] == 3:
            ok(f"摘要: {summary['total_cycles']} 循环, 保留率 {summary['retention_rate']:.1%}")
        else:
            fail("摘要", str(summary))

        # 清理
        test_file = SUPERCLAW_ROOT / "logs" / "test-evolution.jsonl"
        if test_file.exists():
            test_file.unlink()

    except Exception as e:
        fail("进化历史", str(e))


def test_natural_language_query():
    """测试自然语言查询"""
    print("\n[Test 5] 自然语言查询")
    try:
        from superclaw.memory import MemoryStore
        store = MemoryStore(SUPERCLAW_ROOT)

        # 查询 "什么没做" → 应触发反思
        result = store.query("什么没做")
        if "反思" in result or "没做" in result:
            ok("查询 '什么没做' → 返回反思")
        else:
            fail("查询 '什么没做'", result[:80])

        # 查询 "进化历史" → 应返回进化记录
        result = store.query("进化历史")
        if "进化" in result:
            ok("查询 '进化历史' → 返回进化记录")
        else:
            fail("查询 '进化历史'", result[:80])

        # 查询 "状态" → 应返回系统状态
        result = store.query("状态")
        if "记忆系统" in result or "知识库" in result:
            ok("查询 '状态' → 返回系统状态")
        else:
            fail("查询 '状态'", result[:80])

        # 查询 "列出所有" → 应列出知识库
        result = store.query("列出所有")
        if "知识库" in result or "root" in result:
            ok("查询 '列出所有' → 列出知识库")
        else:
            fail("查询 '列出所有'", result[:80])

        # 查询 "查找 进化" → 应检索 md
        result = store.query("查找 进化")
        if "检索" in result or "进化" in result:
            ok("查询 '查找 进化' → 检索 md 知识")
        else:
            fail("查询 '查找 进化'", result[:80])

    except Exception as e:
        fail("自然语言查询", str(e))


def test_skills_scan():
    """测试 skills 自动扫描"""
    print("\n[Test 6] skills 自动扫描")
    try:
        from superclaw.tools import scan_skills
        skills_dir = SUPERCLAW_ROOT / "skills"
        skills = scan_skills(skills_dir)

        if len(skills) >= 2:
            ok(f"扫描到 {len(skills)} 个 skill")
        else:
            fail("skills 扫描", f"只有 {len(skills)} 个")
            return

        # 验证 think.md 和 shell.md
        titles = [s["title"] for s in skills]
        if any("Think" in t or "思考" in t for t in titles):
            ok("包含 Think Skill")
        else:
            fail("Think Skill", f"未找到: {titles}")

        if any("Shell" in t or "命令" in t for t in titles):
            ok("包含 Shell Skill")
        else:
            fail("Shell Skill", f"未找到: {titles}")

        # 验证触发词提取
        for s in skills:
            if s["triggers"]:
                ok(f"{s['title']} 触发词: {s['triggers'][:3]}")
                break

    except Exception as e:
        fail("skills 扫描", str(e))


def test_agent_memory_integration():
    """测试 Agent 集成 memory 工具"""
    print("\n[Test 7] Agent + memory 工具集成")
    try:
        from superclaw import Agent, load_config
        cfg = load_config()
        cfg.llm.provider = "mock"
        cfg.workspace = str(SUPERCLAW_ROOT)
        agent = Agent(cfg=cfg)

        # 验证 skills 自动加载
        if len(agent.loaded_skills) >= 2:
            ok(f"Agent 自动加载 {len(agent.loaded_skills)} 个 skill")
        else:
            fail("Agent skills 自动加载", f"只有 {len(agent.loaded_skills)} 个")

        # 验证 memory 工具存在
        if agent.tools.has("memory"):
            ok("memory 工具已注册")
        else:
            fail("memory 工具", "未注册")
            return

        if agent.tools.has("memory_read"):
            ok("memory_read 工具已注册")
        else:
            fail("memory_read 工具", "未注册")

        # 调用 memory 工具
        result = agent.tools.call("memory", query="状态")
        if not result.error and "知识库" in result.content:
            ok("memory 工具调用成功")
        else:
            fail("memory 工具调用", result.content[:80])

        # 调用 memory_read 工具
        result = agent.tools.call("memory_read", path="SOUL.md")
        if not result.error and "MiMoClaw" in result.content:
            ok("memory_read 工具调用成功")
        else:
            fail("memory_read 工具", result.content[:80])

    except Exception as e:
        fail("Agent 集成", str(e))
        import traceback
        traceback.print_exc()


def test_agent_natural_language():
    """测试 Agent 通过自然语言使用记忆"""
    print("\n[Test 8] Agent 自然语言使用记忆")
    try:
        from superclaw import Agent, load_config
        cfg = load_config()
        cfg.llm.provider = "mock"
        cfg.workspace = str(SUPERCLAW_ROOT)
        agent = Agent(cfg=cfg)

        # 运行一个查询（mock provider 不会真正调用工具，但工具链必须可用）
        result = agent.run("帮我查一下什么没做", session_key="test-mem", verbose=False)
        if result.content and len(result.content) > 0:
            ok(f"Agent 运行返回内容: {result.content[:60]}...")
        else:
            fail("Agent 运行", "空内容")

        # 验证会话保存
        # 清理测试会话
        agent.sessions.clear("test-mem")

    except Exception as e:
        fail("Agent 自然语言", str(e))


def main():
    print("=" * 60)
    print("  🧪 superclaw 记忆系统 + md 融合 + skill 兼容 验证")
    print("=" * 60)

    test_memory_store()
    test_knowledge_search()
    test_self_reflection()
    test_evolution_history()
    test_natural_language_query()
    test_skills_scan()
    test_agent_memory_integration()
    test_agent_natural_language()

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
