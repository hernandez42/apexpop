"""
superclaw 记忆系统 — 融合 APEX mem 框架

来源融合：
- APEX self_reflect.py → 四问反思（什么没做/还能做什么/能否更好/有什么问题）
- APEX ccore.py → 五维状态（能力/学习/知识/协调/适应）+ Φ 值
- APEX fusion_loop.py → 进化历史记录
- superclaw memory/*.md → 日记/洞察/基因日志
- superclaw *.md 体系 → SOUL/AGENTS/MEMORY/TOOLS 等知识源

设计：
- MemoryStore: 统一存储（反思日志 + 进化历史 + md 知识索引）
- 自然语言检索：关键词匹配 + 相关性排序
- 被 Agent 作为 `memory` 工具调用
"""
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# ============================================================
# APEX 四问反思系统（来自 self_reflect.py）
# ============================================================

class SelfReflection:
    """APEX 四问反思 — 每个进化周期自问"""

    QUESTIONS = [
        "什么还没做？",        # gaps
        "还能做什么？",        # opportunities
        "是否能做得更好？",    # improvements
        "存在什么问题？",      # problems
    ]

    def __init__(self, log_path: Path):
        self.log_path = log_path
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def reflect(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """执行四问反思，返回结构化结果"""
        phi = state.get("phi", 0)
        tier = state.get("tier", 1)
        fitness = state.get("fitness", 0.5)
        mutations = state.get("mutations", 0)
        knowledge = state.get("knowledge", 0)

        gaps = self._gaps(phi, tier, fitness, mutations, knowledge)
        opportunities = self._opportunities(phi, mutations, knowledge)
        improvements = self._improvements(fitness, phi)
        problems = self._problems(state)

        reflection = {
            "timestamp": datetime.now().isoformat(),
            "state": {
                "phi": phi, "tier": tier, "fitness": fitness,
                "mutations": mutations, "knowledge": knowledge,
            },
            "gaps": gaps,
            "opportunities": opportunities,
            "improvements": improvements,
            "problems": problems,
        }

        self._save(reflection)
        return reflection

    def _gaps(self, phi, tier, fitness, mutations, knowledge) -> List[str]:
        gaps = []
        if tier < 5:
            gaps.append(f"当前 Tier=T{tier}，距离 T5(ASI) 还有 {5-tier} 层")
        if fitness < 0.8:
            gaps.append(f"适应度 {fitness:.2f} < 0.8，需要加强变异")
        if mutations < 10:
            gaps.append(f"变异次数 {mutations} < 10，进化深度不足")
        if knowledge < 5:
            gaps.append(f"知识量 {knowledge} < 5，知识储备薄弱")
        if not gaps:
            gaps.append("暂无显著差距，保持当前进化节奏")
        return gaps

    def _opportunities(self, phi, mutations, knowledge) -> List[str]:
        opps = []
        if phi > 1.0:
            opps.append("Φ>1，可以尝试更激进的进化策略")
        if mutations > 5:
            opps.append("已有足够变异基础，可以探索基因融合")
        if knowledge > 3:
            opps.append("知识储备足够，可以尝试跨领域共进化")
        opps.append("可以加载更多 skill 扩展能力边界")
        return opps

    def _improvements(self, fitness, phi) -> List[str]:
        imps = []
        if fitness < 0.6:
            imps.append("适应度偏低，可优化变异强度参数")
        if phi < 1.0:
            imps.append("Φ 值偏低，可加强短板修复优先级")
        imps.append("可以优化记忆检索的相关性排序算法")
        imps.append("可以增加 md 知识源的自动索引频率")
        return imps

    def _problems(self, state) -> List[str]:
        probs = []
        health = state.get("health", 0)
        if health < 1:
            probs.append("健康状态异常，需要检查 C Core")
        balance = state.get("balance", 0)
        if balance < 0.3:
            probs.append("基因组平衡度低，某些领域过度集中")
        probs.append("跨会话记忆依赖文件系统，非持久化向量存储")
        return probs

    def _save(self, reflection: Dict):
        """追加保存反思日志"""
        try:
            logs = []
            if self.log_path.exists():
                try:
                    logs = json.loads(self.log_path.read_text(encoding="utf-8"))
                    if not isinstance(logs, list):
                        logs = []
                except (json.JSONDecodeError, IOError):
                    logs = []
            logs.append(reflection)
            # 只保留最近 200 条
            if len(logs) > 200:
                logs = logs[-200:]
            self.log_path.write_text(
                json.dumps(logs, indent=2, ensure_ascii=False),
                encoding="utf-8"
            )
        except IOError:
            pass

    def history(self, limit: int = 5) -> List[Dict]:
        """读取最近的反思历史"""
        if not self.log_path.exists():
            return []
        try:
            logs = json.loads(self.log_path.read_text(encoding="utf-8"))
            if isinstance(logs, list):
                return logs[-limit:]
        except (json.JSONDecodeError, IOError):
            pass
        return []


# ============================================================
# MD 知识索引 — 把 superclaw 的 md 体系变成可检索知识
# ============================================================

class KnowledgeIndex:
    """索引 superclaw 的 md 文件，支持自然语言检索

    索引范围：
    - 项目根目录 *.md（SOUL/AGENTS/MEMORY/TOOLS/EVOLUTION 等）
    - memory/*.md（日记/洞察/日志）
    - skills/*.md（skill 定义）
    """

    def __init__(self, root: Path):
        self.root = root
        self.index: List[Dict[str, Any]] = []
        self._build_index()

    def _build_index(self):
        """构建 md 知识索引"""
        patterns = [
            ("root", "*.md"),
            ("memory", "memory/*.md"),
            ("skills", "skills/*.md"),
        ]
        for category, pattern in patterns:
            for path in self.root.glob(pattern):
                if path.is_file():
                    self._index_file(path, category)

    def _index_file(self, path: Path, category: str):
        """索引单个 md 文件"""
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
        except IOError:
            return

        # 提取标题（第一个 # 开头的行）
        title = path.stem
        for line in content.splitlines():
            if line.startswith("#"):
                title = line.lstrip("#").strip()
                break

        # 提取关键词（简单分词）
        keywords = self._extract_keywords(content)

        self.index.append({
            "path": str(path.relative_to(self.root)),
            "category": category,
            "title": title,
            "keywords": keywords,
            "size": len(content),
            "preview": content[:200].replace("\n", " "),
        })

    def _extract_keywords(self, content: str) -> List[str]:
        """提取关键词（简单实现：高频中文词 + 英文单词）"""
        # 英文单词
        en_words = re.findall(r'[a-zA-Z_][a-zA-Z0-9_]{2,}', content.lower())
        # 中文 2-4 字词（简单切分）
        cn_words = re.findall(r'[\u4e00-\u9fa5]{2,4}', content)

        # 统计频率
        freq: Dict[str, int] = {}
        for w in en_words + cn_words:
            freq[w] = freq.get(w, 0) + 1

        # 取 top 20
        sorted_words = sorted(freq.items(), key=lambda x: -x[1])
        return [w for w, _ in sorted_words[:20]]

    def search(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        """自然语言检索 — 关键词匹配 + 相关性排序"""
        if not query.strip():
            return []

        query_words = self._extract_keywords(query)
        # 额外：对中文查询做单字拆分（处理"灵魂 身份"这类低频组合）
        cn_chars = re.findall(r'[\u4e00-\u9fa5]', query)
        query_chars = [c for c in cn_chars if c not in "的了吗呢吧是在有和与"]

        if not query_words and not query_chars:
            query_words = list(query)

        scored: List[Tuple[float, Dict]] = []
        for entry in self.index:
            score = 0.0
            entry_keywords = entry["keywords"]
            entry_title = entry["title"].lower()
            entry_path = entry["path"].lower()

            # 标题匹配权重最高
            for qw in query_words:
                if qw in entry_title:
                    score += 3.0
                if qw in entry_keywords:
                    score += 2.0
                if qw in entry_path:
                    score += 1.0

            # 单字匹配（权重低，但能命中低频词）
            # 读取内容做子串匹配（仅对 top 候选，避免性能问题）
            for qc in query_chars:
                if qc in entry_title:
                    score += 1.5
                if qc in entry["preview"]:
                    score += 0.5

            if score > 0:
                scored.append((score, entry))

        # 排序
        scored.sort(key=lambda x: -x[0])
        return [e for _, e in scored[:limit]]

    def list_by_category(self, category: Optional[str] = None) -> List[Dict]:
        """按类别列出"""
        if category:
            return [e for e in self.index if e["category"] == category]
        return self.index

    def read(self, path: str) -> Optional[str]:
        """读取指定 md 文件内容"""
        full = self.root / path
        if not full.exists():
            return None
        try:
            return full.read_text(encoding="utf-8", errors="ignore")
        except IOError:
            return None

    def stats(self) -> Dict[str, int]:
        """统计信息"""
        cats: Dict[str, int] = {}
        for e in self.index:
            cats[e["category"]] = cats.get(e["category"], 0) + 1
        cats["total"] = len(self.index)
        return cats


# ============================================================
# 进化历史记录（来自 fusion_loop.py）
# ============================================================

class EvolutionHistory:
    """进化历史 — 记录每次循环的关键指标"""

    def __init__(self, log_path: Path):
        self.log_path = log_path
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, cycle: int, phi: float, domain: str,
               gene_id: str, score: float, retained: bool,
               tier: int, extra: Optional[Dict] = None):
        """记录一次进化循环"""
        entry = {
            "cycle": cycle,
            "timestamp": datetime.now().isoformat(),
            "phi": round(phi, 6),
            "tier": tier,
            "domain": domain,
            "gene_id": gene_id,
            "score": round(score, 4),
            "retained": retained,
        }
        if extra:
            entry.update(extra)

        try:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except IOError:
            pass

    def recent(self, limit: int = 10) -> List[Dict]:
        """读取最近的进化记录"""
        if not self.log_path.exists():
            return []
        try:
            lines = self.log_path.read_text(encoding="utf-8").strip().splitlines()
            records = []
            for line in lines[-limit:]:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
            return records
        except IOError:
            return []

    def summary(self) -> Dict[str, Any]:
        """进化摘要"""
        records = self.recent(1000)
        if not records:
            return {"total_cycles": 0}

        retained = sum(1 for r in records if r.get("retained"))
        domains: Dict[str, int] = {}
        for r in records:
            d = r.get("domain", "unknown")
            domains[d] = domains.get(d, 0) + 1

        phi_values = [r.get("phi", 0) for r in records]
        return {
            "total_cycles": len(records),
            "retained_genes": retained,
            "retention_rate": round(retained / len(records), 4) if records else 0,
            "domains": domains,
            "phi_first": phi_values[0] if phi_values else 0,
            "phi_last": phi_values[-1] if phi_values else 0,
            "phi_growth": round(phi_values[-1] - phi_values[0], 4) if len(phi_values) >= 2 else 0,
        }


# ============================================================
# MemoryStore — 统一记忆系统入口
# ============================================================

class MemoryStore:
    """superclaw 统一记忆系统

    融合：
    - SelfReflection: APEX 四问反思
    - KnowledgeIndex: md 知识检索
    - EvolutionHistory: 进化历史
    - SessionMemory: 会话短期记忆（来自 session.py）
    """

    def __init__(self, root: Optional[Path] = None):
        self.root = root or Path(__file__).parent.parent.resolve()
        self.memory_dir = self.root / "memory"
        self.apex_dir = self.root / "apex-state"
        self.logs_dir = self.root / "logs"
        for d in [self.memory_dir, self.apex_dir, self.logs_dir]:
            d.mkdir(parents=True, exist_ok=True)

        self.reflection = SelfReflection(self.apex_dir / "reflection-log.json")
        self.knowledge = KnowledgeIndex(self.root)
        self.evolution = EvolutionHistory(self.logs_dir / "evolution-history.jsonl")

    def query(self, natural_language: str) -> str:
        """自然语言查询记忆系统 — Agent 的 memory 工具入口

        支持的查询意图：
        - "反思" / "什么没做" / "问题" → 返回最近反思
        - "进化历史" / "上次进化" → 返回进化记录
        - "查找 xxx" / "关于 xxx" → 检索 md 知识
        - "状态" / "当前" → 返回系统状态摘要
        - 其他 → 综合检索
        """
        q = natural_language.lower().strip()

        # 意图识别
        if any(kw in q for kw in ["反思", "什么没做", "还能做", "更好", "问题", "reflect"]):
            return self._format_reflections()

        if any(kw in q for kw in ["进化历史", "上次进化", "进化记录", "evolution", "history"]):
            return self._format_evolution()

        if any(kw in q for kw in ["状态", "当前", "status", "summary", "摘要"]):
            return self._format_status()

        if any(kw in q for kw in ["列出", "所有", "list", "目录", "有哪些"]):
            return self._format_knowledge_list()

        # 默认：知识检索
        return self._format_search(natural_language)

    def _format_reflections(self, limit: int = 3) -> str:
        """格式化反思记录"""
        history = self.reflection.history(limit)
        if not history:
            return "暂无反思记录。运行进化循环后会自动生成。"

        lines = [f"📋 最近 {len(history)} 次反思:\n"]
        for r in history:
            lines.append(f"⏰ {r['timestamp']}")
            lines.append(f"  状态: Φ={r['state']['phi']:.4f}, T{r['state']['tier']}, "
                        f"fitness={r['state']['fitness']:.3f}")
            lines.append(f"  [什么没做] {', '.join(r['gaps'][:2])}")
            lines.append(f"  [还能做] {', '.join(r['opportunities'][:2])}")
            lines.append(f"  [能更好] {', '.join(r['improvements'][:2])}")
            lines.append(f"  [有问题] {', '.join(r['problems'][:2])}")
            lines.append("")
        return "\n".join(lines)

    def _format_evolution(self, limit: int = 10) -> str:
        """格式化进化历史"""
        records = self.evolution.recent(limit)
        if not records:
            return "暂无进化历史。运行 glue.py 后会自动记录。"

        lines = [f"🧬 最近 {len(records)} 次进化:\n"]
        for r in records:
            retained_mark = "✓" if r.get("retained") else "✗"
            lines.append(
                f"  #{r['cycle']} Φ={r['phi']:.4f} T{r['tier']} "
                f"{r['domain']} score={r['score']:.3f} {retained_mark}"
            )

        summary = self.evolution.summary()
        lines.append(f"\n📊 摘要: {summary['total_cycles']} 循环, "
                    f"保留率 {summary['retention_rate']:.1%}, "
                    f"Φ 增长 {summary['phi_growth']:+.4f}")
        return "\n".join(lines)

    def _format_status(self) -> str:
        """格式化系统状态"""
        stats = self.knowledge.stats()
        evo_summary = self.evolution.summary()
        reflections = self.reflection.history(1)

        lines = ["📊 superclaw 记忆系统状态:\n"]
        lines.append(f"  知识库: {stats['total']} 个 md 文件")
        for cat, count in stats.items():
            if cat != "total" and count > 0:
                lines.append(f"    - {cat}: {count}")
        lines.append(f"  进化历史: {evo_summary.get('total_cycles', 0)} 循环")
        lines.append(f"  反思记录: {len(reflections)} 条")
        if reflections:
            last = reflections[-1]
            lines.append(f"  最近反思: {last['timestamp']}")
            lines.append(f"    Φ={last['state']['phi']:.4f}, T{last['state']['tier']}")
        return "\n".join(lines)

    def _format_knowledge_list(self) -> str:
        """列出所有知识源"""
        lines = [f"📚 知识库 ({self.knowledge.stats()['total']} 个文件):\n"]
        for cat in ["root", "memory", "skills"]:
            entries = self.knowledge.list_by_category(cat)
            if entries:
                lines.append(f"  [{cat}] ({len(entries)}):")
                for e in entries[:10]:
                    lines.append(f"    - {e['title']} ({e['path']})")
                if len(entries) > 10:
                    lines.append(f"    ... 还有 {len(entries)-10} 个")
        return "\n".join(lines)

    def _format_search(self, query: str, limit: int = 5) -> str:
        """格式化检索结果"""
        results = self.knowledge.search(query, limit)
        if not results:
            return f"未找到与 '{query}' 相关的知识。试试 '列出所有' 查看知识库。"

        lines = [f"🔍 检索 '{query}' — 找到 {len(results)} 条:\n"]
        for i, r in enumerate(results, 1):
            lines.append(f"  {i}. [{r['category']}] {r['title']}")
            lines.append(f"     路径: {r['path']}")
            lines.append(f"     预览: {r['preview'][:100]}...")
            lines.append("")
        lines.append("提示: 用 memory_read 工具读取完整内容。")
        return "\n".join(lines)

    def read_file(self, path: str) -> str:
        """读取指定知识文件"""
        content = self.knowledge.read(path)
        if content is None:
            return f"文件不存在: {path}"
        # 截断过长内容
        if len(content) > 4000:
            content = content[:2000] + "\n\n...[已截断]...\n\n" + content[-1000:]
        return content

    def reflect_now(self, state: Dict[str, Any]) -> str:
        """立即执行反思（供 Agent 调用）"""
        self.reflection.reflect(state)
        return self._format_reflections(1)


# ============================================================
# 测试
# ============================================================

if __name__ == "__main__":
    store = MemoryStore()

    print("=" * 60)
    print("  superclaw 记忆系统测试")
    print("=" * 60)

    # 知识库统计
    print("\n[1] 知识库统计:")
    print(store._format_status())

    # 检索测试
    print("\n[2] 检索 '进化':")
    print(store._format_search("进化"))

    print("\n[3] 检索 'skill':")
    print(store._format_search("skill"))

    # 反思测试
    print("\n[4] 执行反思:")
    test_state = {"phi": 0.5, "tier": 2, "fitness": 0.6, "mutations": 3, "knowledge": 2}
    print(store.reflect_now(test_state))

    # 自然语言查询
    print("\n[5] 自然语言查询 '什么没做':")
    print(store.query("什么没做"))

    print("\n[6] 自然语言查询 '进化历史':")
    print(store.query("进化历史"))

    print("\n[7] 自然语言查询 '列出所有':")
    print(store.query("列出所有"))
