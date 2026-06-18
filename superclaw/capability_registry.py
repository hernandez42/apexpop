"""
superclaw 能力清单注册表 — 自进化的短板感知基础

设计目标：
- 把 superclaw 真实具备的能力（io/network/llm/evolution/channel/tool）登记成结构化清单
- 记录每项能力的健康度（最近调用成功率），暴露短板
- detect_gaps / analyze_gaps：对比"任务需要的能力" vs "已登记的能力"，输出结构化缺口
- 替换 memory.SelfReflection 里"fitness < 0.8 需要加强变异"这类模板字符串感知

持久化：JSON 文件（默认 superclaw-data/capabilities.json）
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


# ============================================================
# Capability — 单项能力
# ============================================================

@dataclass
class Capability:
    """能力清单中的一项

    Attributes:
        name: 能力名（如 "file_read"）
        description: 能力描述
        category: 能力类别（io/network/llm/evolution/security/channel/tool）
        source: 来源（builtin/github/generated/manual）
        enabled: 是否启用
        dependencies: 依赖的其他能力名或 pip 包
        health: 健康度 0-1，最近调用成功率
        last_used: 最后使用时间 ISO 格式
        call_count: 总调用次数
        fail_count: 失败次数
    """
    name: str
    description: str = ""
    category: str = "tool"
    source: str = "manual"
    enabled: bool = True
    dependencies: List[str] = field(default_factory=list)
    health: float = 1.0
    last_used: Optional[str] = None
    call_count: int = 0
    fail_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        """序列化为可 JSON 持久化的字典"""
        return {
            "name": self.name,
            "description": self.description,
            "category": self.category,
            "source": self.source,
            "enabled": self.enabled,
            "dependencies": list(self.dependencies),
            "health": self.health,
            "last_used": self.last_used,
            "call_count": self.call_count,
            "fail_count": self.fail_count,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Capability":
        """从字典反序列化（容忍缺失字段，给默认值）"""
        return cls(
            name=d["name"],
            description=d.get("description", ""),
            category=d.get("category", "tool"),
            source=d.get("source", "manual"),
            enabled=d.get("enabled", True),
            dependencies=list(d.get("dependencies", [])),
            health=float(d.get("health", 1.0)),
            last_used=d.get("last_used"),
            call_count=int(d.get("call_count", 0)),
            fail_count=int(d.get("fail_count", 0)),
        )


# ============================================================
# 默认能力清单 — superclaw 当前真实具备的能力
# ============================================================

DEFAULT_CAPABILITIES: List[Capability] = [
    # ---- io ----
    Capability(
        name="file_read",
        description="读取本地文件内容，支持相对/绝对路径，大文件自动截断",
        category="io",
        source="builtin",
        enabled=True,
        dependencies=[],
    ),
    Capability(
        name="file_write",
        description="写入本地文件内容，自动创建父目录",
        category="io",
        source="builtin",
        enabled=True,
        dependencies=[],
    ),
    Capability(
        name="shell",
        description="执行 shell 命令（shlex 解析 + shell=False 防注入），返回 stdout/stderr",
        category="io",
        source="builtin",
        enabled=True,
        dependencies=[],
    ),
    # ---- network ----
    Capability(
        name="web_get",
        description="获取 http/https URL 的文本内容（网页/API 返回），仅允许 http/https scheme",
        category="network",
        source="builtin",
        enabled=True,
        dependencies=[],
    ),
    # ---- llm ----
    Capability(
        name="llm_complete",
        description="通过 LLMRouter 调用 LLM 完成对话，支持自动路由/故障转移/成本优化",
        category="llm",
        source="builtin",
        enabled=True,
        dependencies=["superclaw.llm_router"],
    ),
    # ---- evolution ----
    Capability(
        name="gep_cycle",
        description="GEP 10 步进化循环：扫描→信号→选基因→生成→修改→验证→固化→发布→记录→监控",
        category="evolution",
        source="builtin",
        enabled=True,
        dependencies=["llm_complete", "memory_query"],
    ),
    Capability(
        name="apex_reflect",
        description="APEX 四问反思：什么没做/还能做什么/能否更好/有什么问题",
        category="evolution",
        source="builtin",
        enabled=True,
        dependencies=["memory_query"],
    ),
    Capability(
        name="memory_query",
        description="查询记忆系统（自然语言检索 md 知识/反思日志/进化历史）",
        category="evolution",
        source="builtin",
        enabled=True,
        dependencies=["file_read"],
    ),
    # ---- channel ----
    Capability(
        name="console_channel",
        description="终端控制台渠道，从 stdin 读取、输出到 stdout，用于本地测试和 CLI 模式",
        category="channel",
        source="builtin",
        enabled=True,
        dependencies=[],
    ),
    Capability(
        name="feishu_channel",
        description="飞书/Lark 渠道，基于 lark-oapi WebSocket 长连接",
        category="channel",
        source="builtin",
        enabled=True,
        dependencies=["lark-oapi"],
    ),
    # ---- tool ----
    Capability(
        name="tool_registry",
        description="工具注册表，注册/调用工具并生成给 LLM 的工具说明文本",
        category="tool",
        source="builtin",
        enabled=True,
        dependencies=[],
    ),
    Capability(
        name="skill_loader",
        description="扫描 skills 目录加载 .md skill 定义，提取标题/触发词/预览",
        category="tool",
        source="builtin",
        enabled=True,
        dependencies=["file_read"],
    ),
]


# ============================================================
# CapabilityRegistry — 能力注册表
# ============================================================

class CapabilityRegistry:
    """能力清单注册表

    持久化到 JSON 文件（默认 superclaw-data/capabilities.json）。
    提供 register/unregister/get/list/record_call/detect_gaps 等操作。
    """

    def __init__(self, registry_path: Optional[Path] = None):
        self.registry_path: Path = (
            registry_path if registry_path is not None
            else Path("superclaw-data/capabilities.json")
        )
        self._capabilities: Dict[str, Capability] = {}
        # 启动时若文件已存在，自动载入
        self.load()

    # ---- 注册 / 注销 ----

    def register(self, capability: Capability) -> None:
        """注册新能力（同名覆盖）"""
        self._capabilities[capability.name] = capability

    def unregister(self, name: str) -> None:
        """注销能力（不存在则静默忽略）"""
        self._capabilities.pop(name, None)

    def register_defaults(self) -> None:
        """把 DEFAULT_CAPABILITIES 全部注册进来（已存在的同名覆盖）"""
        for cap in DEFAULT_CAPABILITIES:
            self.register(cap)

    # ---- 查询 ----

    def get(self, name: str) -> Optional[Capability]:
        """按名获取能力，不存在返回 None"""
        return self._capabilities.get(name)

    def list_all(self) -> List[Capability]:
        """列出全部能力"""
        return list(self._capabilities.values())

    def list_by_category(self, category: str) -> List[Capability]:
        """按类别列出"""
        return [c for c in self._capabilities.values() if c.category == category]

    def list_enabled(self) -> List[Capability]:
        """列出所有已启用能力"""
        return [c for c in self._capabilities.values() if c.enabled]

    # ---- 调用记录 ----

    def record_call(self, name: str, success: bool) -> None:
        """记录一次调用结果，更新 call_count/fail_count/health/last_used

        health 取累计成功率：(call_count - fail_count) / call_count
        未知能力静默忽略（不抛错，便于在未注册能力上观测）。
        """
        cap = self._capabilities.get(name)
        if cap is None:
            return
        cap.call_count += 1
        if not success:
            cap.fail_count += 1
        if cap.call_count > 0:
            cap.health = (cap.call_count - cap.fail_count) / cap.call_count
        cap.last_used = datetime.now().isoformat()

    # ---- 缺口分析（核心）----

    def detect_gaps(self, required: List[str]) -> List[str]:
        """对比需要的能力 vs 已登记的能力，返回缺失列表

        缺失 = required 中未登记（register 过）的能力名。
        保留 required 顺序，去重。
        """
        registered = set(self._capabilities.keys())
        seen: set = set()
        missing: List[str] = []
        for name in required:
            if name not in registered and name not in seen:
                missing.append(name)
                seen.add(name)
        return missing

    # ---- 持久化 ----

    def save(self) -> None:
        """持久化到 JSON 文件"""
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "capabilities": [c.to_dict() for c in self._capabilities.values()],
        }
        self.registry_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def load(self) -> None:
        """从 JSON 文件载入（文件不存在或损坏则保持空注册表）"""
        if not self.registry_path.exists():
            return
        try:
            data = json.loads(self.registry_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, IOError):
            return
        caps = data.get("capabilities", []) if isinstance(data, dict) else []
        self._capabilities = {}
        for c in caps:
            if not isinstance(c, dict) or "name" not in c:
                continue
            try:
                cap = Capability.from_dict(c)
                self._capabilities[cap.name] = cap
            except (KeyError, TypeError, ValueError):
                continue


# ============================================================
# CapabilityGap — 结构化缺口
# ============================================================

@dataclass
class CapabilityGap:
    """能力缺口（带建议行动）

    Attributes:
        missing_capability: 缺失的能力名
        severity: 严重度（critical/important/nice-to-have）
        suggested_action: 建议获取方式（github_search/code_generate/manual）
        search_query: 若 suggested_action=github_search，给出搜索关键词
    """
    missing_capability: str
    severity: str = "nice-to-have"
    suggested_action: str = "code_generate"
    search_query: str = ""


# 类别 → 默认严重度（builtin 能力缺失时用）
_CATEGORY_SEVERITY: Dict[str, str] = {
    "io": "critical",
    "llm": "critical",
    "evolution": "critical",
    "security": "critical",
    "network": "important",
    "channel": "important",
    "tool": "important",
}

_SEVERITY_ORDER: Dict[str, int] = {
    "critical": 0,
    "important": 1,
    "nice-to-have": 2,
}


def analyze_gaps(registry: CapabilityRegistry,
                 task_requirements: List[str]) -> List[CapabilityGap]:
    """分析任务需要的能力 vs 注册表，返回结构化缺口列表

    每个缺口带 severity 与 suggested_action：
    - 若缺失能力在 DEFAULT_CAPABILITIES 中（builtin）：severity 按类别，action=manual（重新注册内置能力）
    - 否则按能力名关键词推断 severity，action=github_search 并给出搜索关键词

    返回列表按 severity 从重到轻排序。
    """
    missing_names = registry.detect_gaps(task_requirements)
    if not missing_names:
        return []

    default_map = {c.name: c for c in DEFAULT_CAPABILITIES}
    gaps: List[CapabilityGap] = []

    for name in missing_names:
        default_cap = default_map.get(name)
        if default_cap is not None:
            # builtin 能力缺失 → 应重新注册
            severity = _CATEGORY_SEVERITY.get(default_cap.category, "important")
            gaps.append(CapabilityGap(
                missing_capability=name,
                severity=severity,
                suggested_action="manual",
                search_query="",
            ))
            continue

        # 未知能力 → 需要外部获取，按名关键词推断严重度
        lowered = name.lower()
        if any(kw in lowered for kw in ("read", "write", "shell", "complete", "query", "llm")):
            severity = "critical"
        elif any(kw in lowered for kw in ("channel", "tool", "skill", "web", "network", "api")):
            severity = "important"
        else:
            severity = "nice-to-have"
        gaps.append(CapabilityGap(
            missing_capability=name,
            severity=severity,
            suggested_action="github_search",
            search_query=f"python {name.replace('_', ' ')} library",
        ))

    gaps.sort(key=lambda g: _SEVERITY_ORDER.get(g.severity, 3))
    return gaps
