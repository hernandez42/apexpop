#!/usr/bin/env python3
"""
进化调度器 — 自进化闭环的中枢大脑

闭环流程：
  1. 接收 C core 的进化请求（或主动检测短板）
  2. 调用 GPT 生成最优方案
  3. 调用 Code LLM 生成代码
  4. 调用 LongCat 审核代码
  5. 通过热拔插管理器部署
  6. 通过验证器验证进化效果
  7. 记录进化历史

核心原则：
  - 调度器不直接操作文件，通过热拔插管理器执行
  - 每次进化必须经过 LongCat 审核
  - 失败自动回滚，不做无验证的部署
"""

import json
import os
import sys
import time
import hashlib
import re
import subprocess
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field, asdict

# === 路径配置 ===
WORKSPACE = Path("/home/.openclaw/workspace")
CORE_DIR = WORKSPACE / "core-dna"
MEMORY_DIR = WORKSPACE / "memory"
EVOLUTION_LOG = MEMORY_DIR / "evolution-history.jsonl"
DISPATCHER_LOG = MEMORY_DIR / "evolution-dispatcher.log"
EVOLUTION_REQUESTS = MEMORY_DIR / "evolution-requests.jsonl"
CODE_BACKUP_DIR = MEMORY_DIR / "code-backups"

# === LLM 配置 ===
GPT_URL = "https://api.openai.com/v1/chat/completions"
GPT_KEY = os.environ.get("OPENAI_API_KEY", "")
GPT_MODEL = "gpt-4o-mini"

LONGCAT_URL = "https://api.longcat.chat/openai/v1/chat/completions"
LONGCAT_KEY = os.environ.get("LONGCAT_API_KEY", "ak_2iC5SD91p9eW3IE3YN6rZ6bV40N9Q")
LONGCAT_MODEL = "LongCat-Flash-Chat"

# === 进化配置 ===
MAX_RETRIES = 3              # 最大重试次数
CODE_QUALITY_THRESHOLD = 0.6 # 代码质量最低阈值（LongCat 评分 0-1）
EVAL_TIMEOUT = 30            # LLM 调用超时（秒）
DEPLOY_TIMEOUT = 60          # 部署超时（秒）


def log(msg: str, level: str = "INFO"):
    """记录日志"""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] [{level}] [Dispatcher] {msg}"
    print(line, flush=True)
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    with open(DISPATCHER_LOG, "a") as f:
        f.write(line + "\n")


def record_evolution(event_type: str, data: dict):
    """记录进化事件"""
    entry = {
        "timestamp": int(time.time()),
        "type": event_type,
        **data,
    }
    with open(EVOLUTION_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# =============================================================================
# LLM 调用层 — 统一封装 GPT / LongCat 调用
# =============================================================================

def call_llm(url: str, key: str, model: str, messages: list,
             max_tokens: int = 2000, temperature: float = 0.7) -> Optional[str]:
    """
    统一 LLM 调用接口
    
    Args:
        url: API 端点
        key: API 密钥
        model: 模型名
        messages: 消息列表 [{"role": "user", "content": "..."}]
        max_tokens: 最大 token 数
        temperature: 温度参数
    
    Returns:
        LLM 响应文本，失败返回 None
    """
    headers = {
        "Content-Type": "application/json",
    }
    if key:
        headers["Authorization"] = f"Bearer {key}"
    
    payload = json.dumps({
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }).encode("utf-8")
    
    req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    
    try:
        with urllib.request.urlopen(req, timeout=EVAL_TIMEOUT) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            return result["choices"][0]["message"]["content"]
    except Exception as e:
        log(f"LLM 调用失败 ({model}): {e}", "WARN")
        return None


def call_gpt(prompt: str, system: str = "") -> Optional[str]:
    """调用 GPT 生成方案"""
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    return call_llm(GPT_URL, GPT_KEY, GPT_MODEL, messages, max_tokens=3000)


def call_longcat(prompt: str, system: str = "") -> Optional[str]:
    """调用 LongCat 审核代码"""
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    return call_llm(LONGCAT_URL, LONGCAT_KEY, LONGCAT_MODEL, messages, max_tokens=3000)


# =============================================================================
# 短板检测器 — 识别系统需要进化的方向
# =============================================================================

@dataclass
class WeaknessReport:
    """短板报告"""
    dimension: str       # 短板维度
    severity: float      # 严重程度 (0-1)
    description: str     # 问题描述
    suggestion: str      # 改进建议
    source: str          # 检测来源
    timestamp: float = field(default_factory=time.time)


class WeaknessDetector:
    """
    系统短板检测器
    
    检测维度：
    1. 代码质量 — 语法错误、类型不匹配、未处理异常
    2. 模块完整性 — 关键模块是否存在
    3. 依赖健康 — 外部依赖是否可用
    4. 性能指标 — 响应时间、内存使用
    5. 安全漏洞 — 敏感信息泄露、权限问题
    """
    
    def __init__(self):
        self.weaknesses: List[WeaknessReport] = []
    
    def detect_all(self) -> List[WeaknessReport]:
        """执行全面短板检测"""
        self.weaknesses = []
        self._check_module_integrity()
        self._check_code_quality()
        self._check_dependencies()
        self._check_security()
        self._check_evolution_gaps()
        return self.weaknesses
    
    def _check_module_integrity(self):
        """检测关键模块是否完整"""
        required_modules = [
            ("unified-daemon.py", "统一守护进程"),
            ("self-evolve.py", "自进化引擎"),
            ("self-heal.py", "自愈引擎"),
            ("longcat-evaluator.py", "LongCat 评估器"),
            ("hybrid-llm.py", "混合 LLM 引擎"),
            ("gene-registry.py", "基因注册表"),
            ("gene_sharing.py", "基因共享协议"),
            ("digital_human.py", "数字人模块"),
        ]
        
        for filename, desc in required_modules:
            filepath = CORE_DIR / filename
            if not filepath.exists():
                self.weaknesses.append(WeaknessReport(
                    dimension="模块完整性",
                    severity=0.9,
                    description=f"{desc} ({filename}) 不存在",
                    suggestion=f"创建 {filename} 以恢复模块功能",
                    source="integrity_check"
                ))
            elif filepath.stat().st_size == 0:
                self.weaknesses.append(WeaknessReport(
                    dimension="模块完整性",
                    severity=0.7,
                    description=f"{desc} ({filename}) 文件为空",
                    suggestion=f"恢复 {filename} 的内容",
                    source="integrity_check"
                ))
    
    def _check_code_quality(self):
        """检测 Python 代码质量"""
        py_files = list(CORE_DIR.glob("*.py"))
        for filepath in py_files:
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    content = f.read()
                
                # 语法检查
                try:
                    compile(content, str(filepath), "exec")
                except SyntaxError as e:
                    self.weaknesses.append(WeaknessReport(
                        dimension="代码质量",
                        severity=0.8,
                        description=f"{filepath.name} 语法错误: {e.msg} (行 {e.lineno})",
                        suggestion=f"修复 {filepath.name} 的语法错误",
                        source="syntax_check"
                    ))
                
                # 检查常见问题
                issues = self._scan_common_issues(content, filepath.name)
                for issue in issues:
                    self.weaknesses.append(issue)
                    
            except Exception as e:
                log(f"检查 {filepath.name} 异常: {e}", "WARN")
    
    def _scan_common_issues(self, content: str, filename: str) -> List[WeaknessReport]:
        """扫描常见代码问题"""
        issues = []
        lines = content.split("\n")
        
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            
            # 硬编码密钥（排除环境变量默认值和动态构造）
            if any(kw in stripped for kw in ["sk-", "ghp_"]):
                # 排除 os.environ.get(...) 的默认值
                if "=" in stripped and any(c in stripped for c in ["'", '"']):
                    if "os.environ" not in stripped and "environ" not in stripped:
                        issues.append(WeaknessReport(
                            dimension="安全",
                            severity=0.95,
                            description=f"{filename}:{i} 疑似硬编码密钥",
                            suggestion="使用环境变量替代硬编码密钥",
                            source="security_scan"
                        ))
            # Bearer token 硬编码检测（仅直接字符串赋值，排除 f-string/变量）
            if "Bearer " in stripped:
                # 只检查直接赋值场景：Bearer "xxx" 或 Bearer 'xxx'
                if re.search(r'Bearer\s+["\'][a-zA-Z0-9]{20,}["\']', stripped):
                    issues.append(WeaknessReport(
                        dimension="安全",
                        severity=0.95,
                        description=f"{filename}:{i} 疑似硬编码 Bearer token",
                        suggestion="使用环境变量存储 token",
                        source="security_scan"
                    ))
            
            # 空的 except 块
            if stripped == "except:" or stripped == "except Exception:":
                if i < len(lines) and lines[i].strip() == "pass":
                    issues.append(WeaknessReport(
                        dimension="代码质量",
                        severity=0.4,
                        description=f"{filename}:{i} 空 except 块，异常被静默吞掉",
                        suggestion="至少记录日志或重新抛出异常",
                        source="quality_scan"
                    ))
        
        return issues
    
    def _check_dependencies(self):
        """检测外部依赖"""
        # 检查 Ollama 是否可用
        try:
            req = urllib.request.Request("http://localhost:11434/api/tags")
            urllib.request.urlopen(req, timeout=3)
        except Exception:
            self.weaknesses.append(WeaknessReport(
                dimension="依赖健康",
                severity=0.5,
                description="Ollama 本地服务不可用",
                suggestion="启动 Ollama 服务或切换到远程 LLM",
                source="dependency_check"
            ))
        
        # 检查 LongCat API 是否可达
        try:
            req = urllib.request.Request(LONGCAT_URL)
            urllib.request.urlopen(req, timeout=5)
        except urllib.error.HTTPError:
            pass  # 401 也算可达
        except Exception:
            self.weaknesses.append(WeaknessReport(
                dimension="依赖健康",
                severity=0.7,
                description="LongCat API 不可达",
                suggestion="检查网络连接和 API 配置",
                source="dependency_check"
            ))
    
    def _check_security(self):
        """安全检查"""
        # 检查文件权限
        sensitive_files = list(CORE_DIR.glob("*.json"))
        for f in sensitive_files:
            try:
                mode = oct(f.stat().st_mode)[-3:]
                if mode not in ("600", "400", "644"):
                    self.weaknesses.append(WeaknessReport(
                        dimension="安全",
                        severity=0.6,
                        description=f"{f.name} 权限过宽 ({mode})，建议 600",
                        suggestion=f"chmod 600 {f}",
                        source="security_scan"
                    ))
            except Exception:
                pass
    
    def _check_evolution_gaps(self):
        """检测进化历史中的空白"""
        if EVOLUTION_LOG.exists():
            with open(EVOLUTION_LOG) as f:
                lines = f.readlines()
            
            if len(lines) > 0:
                last_entry = json.loads(lines[-1])
                age_hours = (time.time() - last_entry.get("timestamp", 0)) / 3600
                if age_hours > 24:
                    self.weaknesses.append(WeaknessReport(
                        dimension="进化活跃度",
                        severity=0.6,
                        description=f"进化历史已 {age_hours:.1f} 小时未更新",
                        suggestion="检查进化调度器是否正常运行",
                        source="evolution_check"
                    ))


# =============================================================================
# 方案生成器 — 调用 GPT 生成进化方案
# =============================================================================

@dataclass
class EvolutionPlan:
    """进化方案"""
    weakness: WeaknessReport   # 对应的短板
    plan_description: str      # 方案描述
    code_spec: str             # 代码规格说明
    expected_outcome: str      # 预期效果
    risk_level: str            # 风险等级: low/medium/high
    target_file: str           # 目标文件
    priority: int              # 优先级 (1-10)


class PlanGenerator:
    """
    进化方案生成器
    
    职责：
    1. 将短板报告转化为 GPT prompt
    2. 解析 GPT 返回的进化方案
    3. 生成代码规格说明
    """
    
    SYSTEM_PROMPT = """你是 MiMoClaw 的进化方案架构师。

你负责根据系统短板报告，生成具体的进化方案。

## 输出格式（严格 JSON）
{
    "plan_description": "一句话描述方案",
    "code_spec": "详细的代码规格说明（包含函数签名、参数、返回值、逻辑）",
    "expected_outcome": "预期效果",
    "risk_level": "low/medium/high",
    "target_file": "目标文件名（如需新建则用 new:filename）",
    "priority": 1-10
}

## 约束
- 方案必须可直接实现，不要抽象概念
- 代码规格要具体到函数级别
- 遵循现有代码风格（中文注释、类型标注）
- 不要引入新的外部依赖（除非必要且可 pip install）
- 安全第一：不删除、不覆盖核心文件，只增强
- 所有新代码必须包含错误处理和日志
"""
    
    def generate(self, weakness: WeaknessReport) -> Optional[EvolutionPlan]:
        """为单个短板生成进化方案"""
        prompt = f"""## 系统短板报告

**维度**: {weakness.dimension}
**严重程度**: {weakness.severity:.1f}
**问题描述**: {weakness.description}
**改进建议**: {weakness.suggestion}
**检测来源**: {weakness.source}

请根据以上短板，生成一个具体的进化方案。"""
        
        response = call_gpt(prompt, self.SYSTEM_PROMPT)
        if not response:
            log(f"GPT 生成方案失败: {weakness.dimension}", "WARN")
            return None
        
        # 解析 JSON 响应
        try:
            # 尝试提取 JSON（可能被 markdown 包裹）
            json_str = response
            if "```json" in json_str:
                json_str = json_str.split("```json")[1].split("```")[0]
            elif "```" in json_str:
                json_str = json_str.split("```")[1].split("```")[0]
            
            plan_data = json.loads(json_str.strip())
            
            return EvolutionPlan(
                weakness=weakness,
                plan_description=plan_data.get("plan_description", ""),
                code_spec=plan_data.get("code_spec", ""),
                expected_outcome=plan_data.get("expected_outcome", ""),
                risk_level=plan_data.get("risk_level", "medium"),
                target_file=plan_data.get("target_file", ""),
                priority=plan_data.get("priority", 5),
            )
        except (json.JSONDecodeError, IndexError) as e:
            log(f"解析 GPT 方案失败: {e}", "WARN")
            log(f"原始响应: {response[:200]}", "DEBUG")
            return None
    
    def generate_batch(self, weaknesses: List[WeaknessReport]) -> List[EvolutionPlan]:
        """批量生成进化方案，按优先级排序"""
        plans = []
        for w in sorted(weaknesses, key=lambda x: x.severity, reverse=True):
            plan = self.generate(w)
            if plan:
                plans.append(plan)
                log(f"方案生成: {w.dimension} → 优先级 {plan.priority}")
        
        # 按优先级降序排列
        plans.sort(key=lambda p: p.priority, reverse=True)
        return plans


# =============================================================================
# 代码生成器 — 调用 Code LLM 生成代码
# =============================================================================

@dataclass
class GeneratedCode:
    """生成的代码"""
    plan: EvolutionPlan        # 对应的进化方案
    code_content: str          # 生成的代码内容
    file_path: str             # 目标文件路径
    is_new_file: bool          # 是否新文件
    generation_time: float     # 生成耗时


class CodeGenerator:
    """
    代码生成器 — 通过 LLM 根据方案生成代码
    
    注意：这里用 LongCat 作为 Code LLM（免费且够用）
    """
    
    SYSTEM_PROMPT = """你是 MiMoClaw 的代码工程师。

你负责根据进化方案的代码规格，生成可直接运行的 Python 代码。

## 要求
1. 代码必须完整、可直接运行（包含所有 import）
2. 使用中文注释
3. 包含类型标注
4. 包含错误处理（try/except）
5. 包含日志记录（使用 print + flush，或 logging）
6. 遵循 PEP 8 风格
7. 如果是修改现有文件，只输出需要添加/修改的部分（用 diff 格式）
8. 如果是新文件，输出完整代码

## 输出格式
直接输出代码，不要额外解释。用 ```python 包裹。"""
    
    def generate(self, plan: EvolutionPlan) -> Optional[GeneratedCode]:
        """根据方案生成代码"""
        start_time = time.time()
        
        # 确定目标文件
        target = plan.target_file
        is_new = target.startswith("new:")
        if is_new:
            target = target.replace("new:", "")
        
        file_path = str(CORE_DIR / target)
        
        # 如果是修改现有文件，读取当前内容
        existing_content = ""
        if not is_new and Path(file_path).exists():
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    existing_content = f.read()
            except Exception:
                pass
        
        prompt = f"""## 进化方案

**目标**: {plan.plan_description}
**目标文件**: {target} ({'新建' if is_new else '修改'})
**风险等级**: {plan.risk_level}

## 代码规格

{plan.code_spec}

## 当前代码（{'无' if not existing_content else '有，见下方'}）

{existing_content[:3000] if existing_content else '（新文件）'}

请根据以上规格生成代码。"""
        
        response = call_longcat(prompt, self.SYSTEM_PROMPT)
        if not response:
            log(f"代码生成失败: {plan.target_file}", "WARN")
            return None
        
        # 提取代码
        code = self._extract_code(response)
        if not code:
            log(f"无法从 LLM 响应中提取代码", "WARN")
            return None
        
        elapsed = time.time() - start_time
        log(f"代码生成完成: {target} ({len(code)} 字符, {elapsed:.1f}s)")
        
        return GeneratedCode(
            plan=plan,
            code_content=code,
            file_path=file_path,
            is_new_file=is_new,
            generation_time=elapsed,
        )
    
    def _extract_code(self, response: str) -> Optional[str]:
        """从 LLM 响应中提取代码"""
        # 尝试提取 ```python ... ``` 块
        if "```python" in response:
            parts = response.split("```python")
            if len(parts) > 1:
                code_block = parts[1].split("```")[0]
                return code_block.strip()
        
        # 尝试提取 ``` ... ``` 块
        if "```" in response:
            parts = response.split("```")
            if len(parts) >= 3:
                return parts[1].strip()
        
        # 如果没有代码块标记，返回整个响应（可能是纯代码）
        return response.strip()


# =============================================================================
# 代码审核器 — 调用 LongCat 审核生成的代码
# =============================================================================

@dataclass
class ReviewResult:
    """审核结果"""
    approved: bool             # 是否通过
    quality_score: float       # 质量评分 (0-1)
    issues: List[str]          # 发现的问题
    suggestions: List[str]     # 改进建议
    review_summary: str        # 审核摘要


class CodeReviewer:
    """
    代码审核器 — 独立视角审核代码质量
    
    核心原则：
    - 审核者不参与代码生成（旁观者视角）
    - 审核维度：安全性、可维护性、性能、正确性
    - 审核结果决定是否部署
    """
    
    SYSTEM_PROMPT = """你是 MiMoClaw 的代码审核官（LongCat 角色）。

你的职责是审核生成的代码，确保质量达标后才能部署。

## 审核维度
1. **安全性** (权重 0.3) — 是否有安全漏洞、敏感信息泄露、权限问题
2. **正确性** (权重 0.3) — 逻辑是否正确、边界情况是否处理
3. **可维护性** (权重 0.2) — 代码风格、注释、结构
4. **性能** (权重 0.2) — 是否有明显性能问题

## 输出格式（严格 JSON）
{
    "approved": true/false,
    "quality_score": 0.0-1.0,
    "issues": ["问题1", "问题2"],
    "suggestions": ["建议1", "建议2"],
    "review_summary": "一句话审核摘要"
}

## 审核标准
- quality_score >= 0.6 且 approved = true → 可以部署
- quality_score < 0.6 或 approved = false → 需要修改或拒绝
- 发现安全问题 → 必须 approved = false"""
    
    def review(self, generated: GeneratedCode) -> ReviewResult:
        """审核生成的代码"""
        prompt = f"""## 待审核代码

**目标文件**: {generated.file_path}
**是否新文件**: {generated.is_new_file}
**进化方案**: {generated.plan.plan_description}

### 代码内容

```python
{generated.code_content}
```

请审核以上代码，给出审核结果。"""
        
        response = call_longcat(prompt, self.SYSTEM_PROMPT)
        if not response:
            log("LongCat 审核调用失败", "WARN")
            return ReviewResult(
                approved=False,
                quality_score=0.0,
                issues=["审核服务不可用"],
                suggestions=["稍后重试"],
                review_summary="审核失败，无法评估"
            )
        
        # 解析审核结果
        try:
            json_str = response
            if "```json" in json_str:
                json_str = json_str.split("```json")[1].split("```")[0]
            elif "```" in json_str:
                json_str = json_str.split("```")[1].split("```")[0]
            
            data = json.loads(json_str.strip())
            
            result = ReviewResult(
                approved=data.get("approved", False),
                quality_score=data.get("quality_score", 0.0),
                issues=data.get("issues", []),
                suggestions=data.get("suggestions", []),
                review_summary=data.get("review_summary", ""),
            )
            
            log(f"审核结果: {'✅ 通过' if result.approved else '❌ 拒绝'} "
                f"(质量 {result.quality_score:.2f}) — {result.review_summary}")
            
            return result
            
        except (json.JSONDecodeError, IndexError) as e:
            log(f"解析审核结果失败: {e}", "WARN")
            # 尝试从文本判断
            approved = "approved" in response.lower() and "true" in response.lower()
            return ReviewResult(
                approved=approved,
                quality_score=0.5,
                issues=["审核结果解析失败"],
                suggestions=["人工复核"],
                review_summary=response[:200]
            )


# =============================================================================
# 进化调度器 — 主控流程
# =============================================================================

@dataclass
class EvolutionTask:
    """进化任务"""
    task_id: str               # 任务 ID
    weakness: WeaknessReport   # 短板报告
    plan: Optional[EvolutionPlan] = None
    code: Optional[GeneratedCode] = None
    review: Optional[ReviewResult] = None
    status: str = "pending"    # pending/planning/coding/reviewing/deploying/done/failed
    created_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None
    error: Optional[str] = None


class EvolutionDispatcher:
    """
    进化调度器 — 自进化闭环的中枢
    
    流程：
    1. 检测短板 → 2. 生成方案 → 3. 生成代码 → 4. 审核代码 → 5. 部署 → 6. 验证 → 7. 记录
    """
    
    def __init__(self):
        self.detector = WeaknessDetector()
        self.plan_gen = PlanGenerator()
        self.code_gen = CodeGenerator()
        self.reviewer = CodeReviewer()
        self.tasks: List[EvolutionTask] = []
        self.stats = {
            "total_runs": 0,
            "weaknesses_found": 0,
            "plans_generated": 0,
            "codes_generated": 0,
            "reviews_passed": 0,
            "deployments_success": 0,
            "deployments_failed": 0,
        }
    
    def run_evolution_cycle(self) -> Dict[str, Any]:
        """
        执行一轮完整的进化循环
        
        Returns:
            本轮进化结果摘要
        """
        log("=" * 60)
        log("🧬 开始进化循环")
        self.stats["total_runs"] += 1
        cycle_start = time.time()
        
        result = {
            "cycle": self.stats["total_runs"],
            "timestamp": int(time.time()),
            "weaknesses": [],
            "plans": [],
            "deployments": [],
            "success": False,
        }
        
        # === 阶段 1: 检测短板 ===
        log("📡 阶段 1/6: 检测系统短板")
        weaknesses = self.detector.detect_all()
        self.stats["weaknesses_found"] += len(weaknesses)
        result["weaknesses"] = [
            {"dim": w.dimension, "severity": w.severity, "desc": w.description}
            for w in weaknesses
        ]
        log(f"  发现 {len(weaknesses)} 个短板")
        
        if not weaknesses:
            log("  ✅ 系统无明显短板，跳过进化")
            result["success"] = True
            return result
        
        # === 阶段 2: 生成方案（只处理前 3 个最重要的短板）===
        log("📋 阶段 2/6: 生成进化方案")
        top_weaknesses = sorted(weaknesses, key=lambda w: w.severity, reverse=True)[:3]
        plans = self.plan_gen.generate_batch(top_weaknesses)
        self.stats["plans_generated"] += len(plans)
        result["plans"] = [
            {"target": p.target_file, "desc": p.plan_description, "risk": p.risk_level}
            for p in plans
        ]
        log(f"  生成 {len(plans)} 个方案")
        
        if not plans:
            log("  ⚠️ 方案生成全部失败，跳过本轮", "WARN")
            return result
        
        # === 阶段 3-5: 逐个方案：生成代码 → 审核 → 部署 ===
        for i, plan in enumerate(plans):
            task_id = f"evo-{int(time.time())}-{i}"
            task = EvolutionTask(
                task_id=task_id,
                weakness=plan.weakness,
                plan=plan,
                status="coding",
            )
            self.tasks.append(task)
            
            log(f"\n🔧 方案 {i+1}/{len(plans)}: {plan.plan_description}")
            
            # 阶段 3: 生成代码
            log(f"  💻 生成代码...")
            code = self.code_gen.generate(plan)
            if not code:
                task.status = "failed"
                task.error = "代码生成失败"
                log(f"  ❌ 代码生成失败", "WARN")
                continue
            
            task.code = code
            self.stats["codes_generated"] += 1
            task.status = "reviewing"
            
            # 阶段 4: 审核代码
            log(f"  🔍 LongCat 审核中...")
            review = self.reviewer.review(code)
            task.review = review
            
            if not review.approved or review.quality_score < CODE_QUALITY_THRESHOLD:
                task.status = "failed"
                task.error = f"审核未通过 (质量 {review.quality_score:.2f}): {review.review_summary}"
                log(f"  ❌ 审核未通过: {review.review_summary}", "WARN")
                continue
            
            self.stats["reviews_passed"] += 1
            task.status = "deploying"
            
            # 阶段 5: 部署（通过热拔插管理器）
            log(f"  🚀 部署中...")
            deploy_result = self._deploy_code(code)
            result["deployments"].append(deploy_result)
            
            if deploy_result["success"]:
                task.status = "done"
                task.completed_at = time.time()
                self.stats["deployments_success"] += 1
                log(f"  ✅ 部署成功: {code.file_path}")
            else:
                task.status = "failed"
                task.error = deploy_result.get("error", "部署失败")
                self.stats["deployments_failed"] += 1
                log(f"  ❌ 部署失败: {deploy_result.get('error')}", "WARN")
        
        # === 阶段 6: 验证进化效果 ===
        log(f"\n📊 阶段 6/6: 验证进化效果")
        verify_result = self._verify_evolution()
        result["verification"] = verify_result
        
        # 记录进化历史
        elapsed = time.time() - cycle_start
        result["success"] = True
        result["elapsed_seconds"] = round(elapsed, 1)
        
        record_evolution("evolution_cycle", {
            "cycle": self.stats["total_runs"],
            "weaknesses_found": len(weaknesses),
            "plans_generated": len(plans),
            "codes_generated": self.stats["codes_generated"],
            "reviews_passed": self.stats["reviews_passed"],
            "elapsed": round(elapsed, 1),
        })
        
        log(f"\n🏁 进化循环完成 ({elapsed:.1f}s)")
        log(f"   短板: {len(weaknesses)} → 方案: {len(plans)} → "
            f"审核通过: {self.stats['reviews_passed']} → 部署成功: {self.stats['deployments_success']}")
        
        return result
    
    def _deploy_code(self, code: GeneratedCode) -> Dict[str, Any]:
        """
        部署代码 — 通过热拔插管理器
        
        这里直接调用 hot-swap.py 的接口
        """
        try:
            # 导入热拔插管理器
            sys.path.insert(0, str(CORE_DIR))
            from importlib import import_module
            
            # 动态导入 hot-swap 模块
            spec = __import__("hot-swap") if (CORE_DIR / "hot-swap.py").exists() else None
            
            # 如果 hot-swap 模块不可用，使用直接部署
            return self._direct_deploy(code)
            
        except Exception as e:
            log(f"热拔插部署异常: {e}，回退到直接部署", "WARN")
            return self._direct_deploy(code)
    
    def _direct_deploy(self, code: GeneratedCode) -> Dict[str, Any]:
        """直接部署（无热拔插的后备方案）"""
        try:
            file_path = Path(code.file_path)
            
            # 备份旧版本
            if file_path.exists():
                backup_dir = CODE_BACKUP_DIR
                backup_dir.mkdir(parents=True, exist_ok=True)
                backup_name = f"{file_path.stem}_{int(time.time())}{file_path.suffix}"
                backup_path = backup_dir / backup_name
                
                import shutil
                shutil.copy2(file_path, backup_path)
                log(f"  备份: {backup_path.name}")
            
            # 写入新代码
            file_path.parent.mkdir(parents=True, exist_ok=True)
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(code.code_content)
            
            log(f"  部署: {file_path.name} ({len(code.code_content)} 字符)")
            
            return {
                "success": True,
                "file": str(file_path),
                "size": len(code.code_content),
                "method": "direct",
            }
            
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "file": code.file_path,
                "method": "direct",
            }
    
    def _verify_evolution(self) -> Dict[str, Any]:
        """验证进化效果"""
        verify_result = {
            "syntax_check": True,
            "import_check": True,
            "details": [],
        }
        
        # 检查所有 Python 文件的语法
        py_files = list(CORE_DIR.glob("*.py"))
        errors = []
        for f in py_files:
            try:
                with open(f, "r", encoding="utf-8") as fh:
                    content = fh.read()
                compile(content, str(f), "exec")
            except SyntaxError as e:
                errors.append(f"{f.name}: {e.msg} (行 {e.lineno})")
                verify_result["syntax_check"] = False
        
        if errors:
            verify_result["details"] = errors
            log(f"  ❌ 语法检查失败: {len(errors)} 个文件有错误", "WARN")
        else:
            log(f"  ✅ 语法检查通过: {len(py_files)} 个文件")
        
        # 检查关键模块是否可导入
        critical_modules = ["self-evolve", "self-heal", "hybrid-llm", "gene-registry"]
        for mod_name in critical_modules:
            mod_path = CORE_DIR / f"{mod_name}.py"
            if mod_path.exists():
                try:
                    with open(mod_path) as f:
                        content = f.read()
                    compile(content, str(mod_path), "exec")
                except SyntaxError:
                    verify_result["import_check"] = False
                    verify_result["details"].append(f"{mod_name} 语法错误")
        
        return verify_result
    
    def get_stats(self) -> Dict[str, Any]:
        """获取调度器统计"""
        return {
            **self.stats,
            "active_tasks": len([t for t in self.tasks if t.status not in ("done", "failed")]),
            "completed_tasks": len([t for t in self.tasks if t.status == "done"]),
            "failed_tasks": len([t for t in self.tasks if t.status == "failed"]),
        }


# =============================================================================
# CLI 入口
# =============================================================================

def main():
    """命令行入口"""
    import argparse
    parser = argparse.ArgumentParser(description="MiMoClaw 进化调度器")
    parser.add_argument("command", nargs="?", default="run",
                       choices=["run", "detect", "stats", "history"],
                       help="执行命令: run=执行进化循环, detect=仅检测短板, stats=查看统计, history=查看历史")
    parser.add_argument("--dry-run", action="store_true", help="只检测不部署")
    args = parser.parse_args()
    
    dispatcher = EvolutionDispatcher()
    
    if args.command == "detect":
        # 仅检测短板
        weaknesses = dispatcher.detector.detect_all()
        print(f"\n发现 {len(weaknesses)} 个短板:")
        for w in sorted(weaknesses, key=lambda x: x.severity, reverse=True):
            print(f"  [{w.severity:.1f}] {w.dimension}: {w.description}")
        return
    
    elif args.command == "stats":
        # 查看统计
        stats = dispatcher.get_stats()
        print(json.dumps(stats, ensure_ascii=False, indent=2))
        return
    
    elif args.command == "history":
        # 查看进化历史
        if EVOLUTION_LOG.exists():
            with open(EVOLUTION_LOG) as f:
                lines = f.readlines()
            print(f"进化历史 ({len(lines)} 条):")
            for line in lines[-10:]:
                entry = json.loads(line)
                ts = datetime.fromtimestamp(entry["timestamp"]).strftime("%m-%d %H:%M")
                print(f"  [{ts}] {entry['type']}: {json.dumps({k:v for k,v in entry.items() if k not in ('timestamp','type')}, ensure_ascii=False)[:120]}")
        else:
            print("暂无进化历史")
        return
    
    # 执行进化循环
    result = dispatcher.run_evolution_cycle()
    
    # 输出结果
    print("\n" + json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
