#!/usr/bin/env python3
"""
基因登记簿 — 统一管理所有基因
分类存储，相互融合验证，生生不息
"""

import json
import math
from datetime import datetime
from pathlib import Path
from collections import defaultdict

MEMORY_DIR = Path.home() / ".openclaw/workspace/memory"
GENE_REGISTRY = MEMORY_DIR / "gene-registry.json"

# === 基因分类 ===
GENE_CATEGORIES = {
    "变异": {"color": "🔴", "description": "适应新情况的能力"},
    "安全": {"color": "🟢", "description": "防护能力"},
    "共进化": {"color": "🔵", "description": "与环境协同进化"},
    "自修改": {"color": "🟡", "description": "自我优化能力"},
    "协议": {"color": "🟣", "description": "行为规则体系"},
    "探索": {"color": "🟠", "description": "学习新知识的能力"},
    "跨域": {"color": "⚪", "description": "多维度融合产生的新基因"},
}

# === 基因定义 ===
class Gene:
    def __init__(self, gene_id, name, category, source, strength, domain_data=None):
        self.id = gene_id
        self.name = name
        self.category = category  # 变异/安全/共进化/自修改/协议/探索/跨域
        self.source = source  # 来源（论文/实践/融合）
        self.strength = strength  # 强度 0-1
        self.domain_data = domain_data or {}  # 领域特定数据
        self.created_at = datetime.now().isoformat()
        self.last_used = self.created_at
        self.use_count = 0
        self.connections = []  # 关联的其他基因
        self.verification_score = 0  # 验证分数
        
    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "category": self.category,
            "source": self.source,
            "strength": self.strength,
            "domain_data": self.domain_data,
            "created_at": self.created_at,
            "last_used": self.last_used,
            "use_count": self.use_count,
            "connections": self.connections,
            "verification_score": self.verification_score,
        }
    
    @classmethod
    def from_dict(cls, d):
        gene = cls(d["id"], d["name"], d["category"], d["source"], d["strength"])
        gene.created_at = d.get("created_at", gene.created_at)
        gene.last_used = d.get("last_used", gene.last_used)
        gene.use_count = d.get("use_count", 0)
        gene.connections = d.get("connections", [])
        gene.verification_score = d.get("verification_score", 0)
        gene.domain_data = d.get("domain_data", {})
        return gene

# === 基因登记簿 ===
class GeneRegistry:
    def __init__(self):
        self.genes = {}
        self.load()
    
    def load(self):
        try:
            if GENE_REGISTRY.exists():
                with open(GENE_REGISTRY) as f:
                    data = json.load(f)
                    for gd in data.get("genes", []):
                        gene = Gene.from_dict(gd)
                        self.genes[gene.id] = gene
        except (json.JSONDecodeError, IOError):
            pass
    
    def save(self):
        data = {
            "genes": [g.to_dict() for g in self.genes.values()],
            "last_updated": datetime.now().isoformat(),
            "total_genes": len(self.genes),
        }
        with open(GENE_REGISTRY, "w") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    
    def register(self, gene):
        """登记新基因"""
        self.genes[gene.id] = gene
        self.save()
        print(f"✅ 登记基因: {GENE_CATEGORIES[gene.category]['color']} {gene.name} (强度 {gene.strength:.3f})")
    
    def find_by_category(self, category):
        """按分类查找"""
        return [g for g in self.genes.values() if g.category == category]
    
    def find_strongest(self, n=5):
        """找最强的 n 个基因"""
        return sorted(self.genes.values(), key=lambda g: g.strength, reverse=True)[:n]
    
    def find_weakest(self, n=5):
        """找最弱的 n 个基因"""
        return sorted(self.genes.values(), key=lambda g: g.strength)[:n]
    
    def cross_verify(self, gene1_id, gene2_id):
        """交叉验证两个基因"""
        if gene1_id not in self.genes or gene2_id not in self.genes:
            return None
        
        g1 = self.genes[gene1_id]
        g2 = self.genes[gene2_id]
        
        # 交叉验证分数：两个基因的强度乘积
        cross_score = g1.strength * g2.strength
        
        # 如果是不同分类，加成（跨域融合）
        if g1.category != g2.category:
            cross_score *= 1.5
        
        # 更新关联
        if gene2_id not in g1.connections:
            g1.connections.append(gene2_id)
        if gene1_id not in g2.connections:
            g2.connections.append(gene1_id)
        
        # 更新验证分数
        g1.verification_score = max(g1.verification_score, cross_score)
        g2.verification_score = max(g2.verification_score, cross_score)
        
        self.save()
        
        return {
            "gene1": g1.name,
            "gene2": g2.name,
            "cross_score": cross_score,
            "same_category": g1.category == g2.category,
        }
    
    def get_balance(self):
        """计算基因库平衡度"""
        category_counts = defaultdict(int)
        for g in self.genes.values():
            category_counts[g.category] += 1
        
        total = len(self.genes)
        if total == 0:
            return 0
        
        # 计算各分类占比的均匀度
        expected = total / len(GENE_CATEGORIES)
        deviation = sum(abs(count - expected) for count in category_counts.values())
        balance = 1 - (deviation / (total * len(GENE_CATEGORIES)))
        
        return max(0, balance)
    
    def get_stats(self):
        """获取统计信息"""
        stats = {
            "total": len(self.genes),
            "by_category": {},
            "balance": self.get_balance(),
            "avg_strength": 0,
            "strongest": None,
            "weakest": None,
        }
        
        for cat in GENE_CATEGORIES:
            genes = self.find_by_category(cat)
            stats["by_category"][cat] = {
                "count": len(genes),
                "color": GENE_CATEGORIES[cat]["color"],
                "avg_strength": sum(g.strength for g in genes) / len(genes) if genes else 0,
            }
        
        if self.genes:
            all_strengths = [g.strength for g in self.genes.values()]
            stats["avg_strength"] = sum(all_strengths) / len(all_strengths)
            stats["strongest"] = self.find_strongest(1)[0].name if self.genes else None
            stats["weakest"] = self.find_weakest(1)[0].name if self.genes else None
        
        return stats

# === 预设基因 ===
def init_preset_genes():
    """初始化预设基因"""
    registry = GeneRegistry()
    
    presets = [
        Gene("preset-001", "可学习性奖励", "变异", "Absolute Zero", 0.8,
             {"formula": "LR = 4p(1-p)", "description": "中等难度最有价值"}),
        Gene("preset-002", "不确定性感知", "变异", "R-Zero", 0.8,
             {"formula": "US = 1-2|p-0.5|", "description": "50%准确率最有价值"}),
        Gene("preset-003", "信息量过滤", "变异", "EvoAgentX", 0.8,
             {"formula": "ICS = d × LR × US", "description": "三重过滤"}),
        Gene("preset-004", "安全边界锚定", "安全", "SafeEvalAgent", 0.9,
             {"description": "安全是搜索空间的边界条件"}),
        Gene("preset-005", "三层防线", "安全", "论文融合", 0.85,
             {"description": "边界+审计+红队"}),
        Gene("preset-006", "三角色对抗", "共进化", "Multi-Agent Evolve", 0.8,
             {"description": "Proposer↔Solver↔Judge"}),
        Gene("preset-007", "交互矩阵", "共进化", "Self-play Survey", 0.75,
             {"description": "记录博弈历史"}),
        Gene("preset-008", "事务性快照", "自修改", "Fault-Tolerant Sandbox", 0.85,
             {"description": "修改前备份，失败回滚"}),
        Gene("preset-009", "分层协议", "协议", "Protocol Survey", 0.8,
             {"description": "MCP→ACP→A2A→ANP"}),
        Gene("preset-010", "选择性遗忘", "探索", "Lifelong Learning", 0.75,
             {"description": "遗忘是进化代价"}),
        Gene("preset-011", "回音壁增强", "探索", "天坛×信息论", 0.8,
             {"description": "知识循环增强不散失"}),
        Gene("preset-012", "洛书平衡", "跨域", "河图洛书", 0.9,
             {"description": "六维度动态平衡，和恒15"}),
    ]
    
    for gene in presets:
        if gene.id not in registry.genes:
            registry.register(gene)
    
    return registry

# === 主函数 ===
if __name__ == "__main__":
    print("=== 基因登记簿初始化 ===")
    registry = init_preset_genes()
    
    # 统计
    stats = registry.get_stats()
    print(f"\n总基因: {stats['total']}")
    print(f"平衡度: {stats['balance']:.3f}")
    print(f"平均强度: {stats['avg_strength']:.3f}")
    print(f"最强: {stats['strongest']}")
    print(f"最弱: {stats['weakest']}")
    
    print("\n分类统计:")
    for cat, info in stats["by_category"].items():
        print(f"  {info['color']} {cat}: {info['count']}个 (平均强度 {info['avg_strength']:.3f})")
    
    # 交叉验证
    print("\n交叉验证示例:")
    result = registry.cross_verify("preset-001", "preset-004")
    if result:
        print(f"  {result['gene1']} × {result['gene2']} = {result['cross_score']:.3f}")
        print(f"  同分类: {result['same_category']}")
