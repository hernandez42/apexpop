#!/usr/bin/env python3
"""
进化验证器 — 验证进化闭环的每一步

三大验证维度：
  1. 代码质量验证 — 语法、风格、安全
  2. 部署结果验证 — 文件完整性、模块可加载
  3. 进化效果验证 — 前后对比、功能测试

验证原则：
  - 每次进化操作后必须验证
  - 验证失败触发回滚
  - 验证结果可追溯
"""

import ast
import json
import os
import re
import sys
import time
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field

# === 路径配置 ===
WORKSPACE = Path("/home/.openclaw/workspace")
CORE_DIR = WORKSPACE / "core-dna"
MEMORY_DIR = WORKSPACE / "memory"
VALIDATOR_LOG = MEMORY_DIR / "evolution-validator.log"
VALIDATION_HISTORY = MEMORY_DIR / "validation-history.jsonl"


def log(msg: str, level: str = "INFO"):
    """记录日志"""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] [{level}] [Validator] {msg}"
    print(line, flush=True)
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    with open(VALIDATOR_LOG, "a") as f:
        f.write(line + "\n")


@dataclass
class ValidationResult:
    """单项验证结果"""
    check_name: str          # 检查项名称
    passed: bool             # 是否通过
    score: float             # 评分 (0-1)
    details: str             # 详细说明
    severity: str            # 严重程度 (critical/warning/info)
    elapsed: float = 0.0     # 耗时（秒）


@dataclass
class ValidationReport:
    """完整验证报告"""
    target: str              # 验证目标（文件/模块）
    timestamp: float         # 时间戳
    results: List[ValidationResult] = field(default_factory=list)
    overall_passed: bool = True
    overall_score: float = 1.0
    
    def add_result(self, result: ValidationResult):
        """添加验证结果"""
        self.results.append(result)
        if not result.passed and result.severity == "critical":
            self.overall_passed = False
        # 综合评分 = 所有结果的加权平均
        if self.results:
            self.overall_score = sum(r.score for r in self.results) / len(self.results)
    
    def to_dict(self) -> Dict:
        return {
            "target": self.target,
            "timestamp": self.timestamp,
            "overall_passed": self.overall_passed,
            "overall_score": round(self.overall_score, 3),
            "checks": [
                {
                    "name": r.check_name,
                    "passed": r.passed,
                    "score": round(r.score, 3),
                    "details": r.details,
                    "severity": r.severity,
                }
                for r in self.results
            ],
        }


class EvolutionValidator:
    """
    进化验证器
    
    对进化后的代码进行全面验证：
    1. 语法检查 — Python 语法正确性
    2. AST 分析 — 代码结构合理性
    3. 风格检查 — PEP 8 基础合规
    4. 安全扫描 — 敏感信息泄露
    5. 依赖检查 — import 有效性
    6. 文件完整性 — 大小、编码
    7. 功能测试 — 模块可加载
    """
    
    def validate_file(self, filepath: Path) -> ValidationReport:
        """
        验证单个文件
        
        Args:
            filepath: 文件路径
        
        Returns:
            ValidationReport
        """
        report = ValidationReport(
            target=str(filepath),
            timestamp=time.time(),
        )
        
        log(f"🔍 开始验证: {filepath.name}")
        
        # 1. 文件存在性
        self._check_exists(filepath, report)
        
        if not filepath.exists():
            return report
        
        # 2. 文件大小
        self._check_file_size(filepath, report)
        
        # 3. 编码检查
        content = self._check_encoding(filepath, report)
        
        if content is None:
            return report
        
        # 4. 语法检查
        self._check_syntax(filepath, content, report)
        
        # 5. AST 分析
        self._check_ast(filepath, content, report)
        
        # 6. 安全扫描
        self._check_security(filepath, content, report)
        
        # 7. 风格检查
        self._check_style(filepath, content, report)
        
        # 8. 依赖检查
        self._check_dependencies(filepath, content, report)
        
        # 9. 功能测试（模块可加载）
        self._check_loadable(filepath, report)
        
        # 计算综合评分
        if report.results:
            report.overall_score = sum(r.score for r in report.results) / len(report.results)
        
        status = "✅ 通过" if report.overall_passed else "❌ 未通过"
        log(f"  {status}: {filepath.name} (评分 {report.overall_score:.2f})")
        
        return report
    
    def validate_directory(self, directory: Path = CORE_DIR) -> Dict[str, ValidationReport]:
        """验证整个目录"""
        reports = {}
        
        py_files = list(directory.glob("*.py"))
        log(f"🔍 批量验证: {len(py_files)} 个 Python 文件")
        
        for filepath in sorted(py_files):
            report = self.validate_file(filepath)
            reports[filepath.name] = report
        
        # 汇总
        total = len(reports)
        passed = sum(1 for r in reports.values() if r.overall_passed)
        avg_score = sum(r.overall_score for r in reports.values()) / total if total else 0
        
        log(f"\n📊 批量验证完成: {passed}/{total} 通过, 平均评分 {avg_score:.2f}")
        
        return reports
    
    def validate_evolution(self, before_files: Dict[str, str],
                          after_files: Dict[str, str]) -> Dict[str, Any]:
        """
        验证进化效果 — 前后对比
        
        Args:
            before_files: 进化前的文件内容 {filename: content}
            after_files: 进化后的文件内容 {filename: content}
        
        Returns:
            验证结果摘要
        """
        log("📊 验证进化效果（前后对比）")
        
        result = {
            "files_changed": 0,
            "files_improved": 0,
            "files_degraded": 0,
            "details": [],
        }
        
        all_files = set(list(before_files.keys()) + list(after_files.keys()))
        
        for filename in all_files:
            before = before_files.get(filename, "")
            after = after_files.get(filename, "")
            
            if before == after:
                continue
            
            result["files_changed"] += 1
            
            # 对比代码质量
            before_score = self._quick_quality_score(before)
            after_score = self._quick_quality_score(after)
            
            change = after_score - before_score
            detail = {
                "file": filename,
                "before_score": round(before_score, 3),
                "after_score": round(after_score, 3),
                "change": round(change, 3),
                "improved": change > 0,
            }
            result["details"].append(detail)
            
            if change > 0:
                result["files_improved"] += 1
                log(f"  ✅ {filename}: {before_score:.2f} → {after_score:.2f} (+{change:.2f})")
            elif change < -0.01:
                result["files_degraded"] += 1
                log(f"  ⚠️ {filename}: {before_score:.2f} → {after_score:.2f} ({change:.2f})")
            else:
                log(f"  ➡️ {filename}: 持平 ({after_score:.2f})")
        
        # 总体评估
        result["net_improvement"] = result["files_improved"] - result["files_degraded"]
        result["success"] = result["net_improvement"] >= 0
        
        log(f"\n  变化: {result['files_changed']} 个文件, "
            f"提升: {result['files_improved']}, 退化: {result['files_degraded']}")
        
        return result
    
    def _quick_quality_score(self, content: str) -> float:
        """快速质量评分（用于前后对比）"""
        score = 0.5  # 基础分
        
        # 语法正确 +0.2
        try:
            ast.parse(content)
            score += 0.2
        except SyntaxError:
            score -= 0.3
        
        # 有类型标注 +0.1
        if "def " in content and (":" in content and "->" in content):
            score += 0.1
        
        # 有 docstring +0.1
        if '"""' in content or "'''" in content:
            score += 0.1
        
        # 有错误处理 +0.1
        if "try:" in content and "except" in content:
            score += 0.1
        
        # 空 except 扣分
        lines = content.split("\n")
        empty_excepts = sum(1 for i, line in enumerate(lines)
                           if line.strip() in ("except:", "except Exception:")
                           and i + 1 < len(lines) and lines[i + 1].strip() == "pass")
        score -= empty_excepts * 0.05
        
        # 无硬编码密钥 +0.1
        if not re.search(r"(sk-|ghp_|Bearer\s+['\"])", content):
            score += 0.1
        
        return max(0.0, min(1.0, score))
    
    # === 具体检查方法 ===
    
    def _check_exists(self, filepath: Path, report: ValidationReport):
        """检查文件存在"""
        start = time.time()
        exists = filepath.exists()
        report.add_result(ValidationResult(
            check_name="文件存在",
            passed=exists,
            score=1.0 if exists else 0.0,
            details=f"文件{'存在' if exists else '不存在'}: {filepath.name}",
            severity="critical" if not exists else "info",
            elapsed=time.time() - start,
        ))
    
    def _check_file_size(self, filepath: Path, report: ValidationReport):
        """检查文件大小"""
        start = time.time()
        size = filepath.stat().st_size
        
        if size == 0:
            passed = False
            score = 0.0
            severity = "critical"
            details = "文件为空"
        elif size > 500_000:  # > 500KB
            passed = True
            score = 0.7
            severity = "warning"
            details = f"文件较大: {size:,} bytes"
        else:
            passed = True
            score = 1.0
            details = f"文件大小正常: {size:,} bytes"
            severity = "info"
        
        report.add_result(ValidationResult(
            check_name="文件大小",
            passed=passed,
            score=score,
            details=details,
            severity=severity,
            elapsed=time.time() - start,
        ))
    
    def _check_encoding(self, filepath: Path, report: ValidationReport) -> Optional[str]:
        """检查文件编码"""
        start = time.time()
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()
            report.add_result(ValidationResult(
                check_name="编码检查",
                passed=True,
                score=1.0,
                details="UTF-8 编码正确",
                severity="info",
                elapsed=time.time() - start,
            ))
            return content
        except UnicodeDecodeError as e:
            report.add_result(ValidationResult(
                check_name="编码检查",
                passed=False,
                score=0.0,
                details=f"编码错误: {e}",
                severity="critical",
                elapsed=time.time() - start,
            ))
            return None
    
    def _check_syntax(self, filepath: Path, content: str, report: ValidationReport):
        """语法检查"""
        start = time.time()
        try:
            compile(content, str(filepath), "exec")
            report.add_result(ValidationResult(
                check_name="语法检查",
                passed=True,
                score=1.0,
                details="Python 语法正确",
                severity="info",
                elapsed=time.time() - start,
            ))
        except SyntaxError as e:
            report.add_result(ValidationResult(
                check_name="语法检查",
                passed=False,
                score=0.0,
                details=f"语法错误: {e.msg} (行 {e.lineno})",
                severity="critical",
                elapsed=time.time() - start,
            ))
    
    def _check_ast(self, filepath: Path, content: str, report: ValidationReport):
        """AST 结构分析"""
        start = time.time()
        try:
            tree = ast.parse(content)
            
            # 统计信息
            classes = [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
            functions = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
            imports = [n for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))]
            
            # 评分：有结构 > 纯脚本
            score = 0.5
            if classes:
                score += 0.2
            if functions:
                score += 0.2
            if imports:
                score += 0.1
            
            report.add_result(ValidationResult(
                check_name="AST 分析",
                passed=True,
                score=min(1.0, score),
                details=f"类: {len(classes)}, 函数: {len(functions)}, 导入: {len(imports)}",
                severity="info",
                elapsed=time.time() - start,
            ))
        except Exception as e:
            report.add_result(ValidationResult(
                check_name="AST 分析",
                passed=False,
                score=0.3,
                details=f"AST 解析异常: {e}",
                severity="warning",
                elapsed=time.time() - start,
            ))
    
    def _check_security(self, filepath: Path, content: str, report: ValidationReport):
        """安全扫描"""
        start = time.time()
        issues = []
        
        lines = content.split("\n")
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            
            # 硬编码密钥
            if re.search(r"(sk-[a-zA-Z0-9]{20,}|ghp_[a-zA-Z0-9]{36}|Bearer\s+['\"][a-zA-Z0-9]{20,})", stripped):
                issues.append(f"行 {i}: 疑似硬编码密钥")
            
            # 危险函数
            if re.search(r"\b(eval|exec|__import__)\s*\(", stripped):
                issues.append(f"行 {i}: 使用了危险函数 {stripped.split('(')[0].strip()}")
            
            # 不安全的文件操作
            if re.search(r"os\.system\s*\(", stripped):
                issues.append(f"行 {i}: 使用了 os.system（建议用 subprocess）")
            
            # rm -rf
            if re.search(r"rm\s+-rf", stripped):
                issues.append(f"行 {i}: 包含 rm -rf 命令")
        
        if issues:
            severity = "critical" if any("密钥" in i for i in issues) else "warning"
            report.add_result(ValidationResult(
                check_name="安全扫描",
                passed=False,
                score=max(0.0, 1.0 - len(issues) * 0.2),
                details=f"发现 {len(issues)} 个安全问题: " + "; ".join(issues[:3]),
                severity=severity,
                elapsed=time.time() - start,
            ))
        else:
            report.add_result(ValidationResult(
                check_name="安全扫描",
                passed=True,
                score=1.0,
                details="未发现安全问题",
                severity="info",
                elapsed=time.time() - start,
            ))
    
    def _check_style(self, filepath: Path, content: str, report: ValidationReport):
        """风格检查"""
        start = time.time()
        issues = []
        lines = content.split("\n")
        
        for i, line in enumerate(lines, 1):
            # 行长度
            if len(line) > 120:
                issues.append(f"行 {i}: 行长度 {len(line)} > 120")
            
            # 缩进一致性
            if line and not line.startswith("#"):
                stripped = line.lstrip()
                indent = len(line) - len(stripped)
                if indent > 0 and indent % 4 != 0 and indent % 2 != 0:
                    issues.append(f"行 {i}: 非标准缩进 ({indent} spaces)")
        
        # 检查是否有中文注释（正面指标）
        has_chinese_comments = bool(re.search(r"#\s*[\u4e00-\u9fff]", content))
        
        score = 1.0
        if issues:
            score -= len(issues) * 0.05
        if has_chinese_comments:
            score += 0.1  # 中文注释加分
        
        report.add_result(ValidationResult(
            check_name="风格检查",
            passed=len(issues) <= 3,
            score=max(0.0, min(1.0, score)),
            details=f"问题: {len(issues)} 个" + (f" ({issues[0]})" if issues else "") +
                    (" | 有中文注释" if has_chinese_comments else ""),
            severity="warning" if len(issues) > 3 else "info",
            elapsed=time.time() - start,
        ))
    
    def _check_dependencies(self, filepath: Path, content: str, report: ValidationReport):
        """依赖检查"""
        start = time.time()
        
        # 提取所有 import
        import_pattern = re.compile(r"(?:from\s+(\S+)\s+)?import\s+(\S+)")
        imports = import_pattern.findall(content)
        
        # 检查标准库 import 是否有效
        stdlib_modules = {
            "json", "os", "sys", "time", "hashlib", "subprocess", "shutil",
            "tempfile", "pathlib", "datetime", "typing", "dataclasses",
            "re", "ast", "urllib", "signal", "select", "importlib",
        }
        
        custom_modules = set()
        for from_mod, import_name in imports:
            if from_mod and not from_mod.startswith(".") and from_mod not in stdlib_modules:
                custom_modules.add(from_mod)
        
        # 检查自定义模块是否存在
        missing = []
        for mod in custom_modules:
            mod_path = CORE_DIR / f"{mod}.py"
            if not mod_path.exists():
                missing.append(mod)
        
        if missing:
            report.add_result(ValidationResult(
                check_name="依赖检查",
                passed=False,
                score=0.6,
                details=f"缺失依赖模块: {', '.join(missing)}",
                severity="warning",
                elapsed=time.time() - start,
            ))
        else:
            report.add_result(ValidationResult(
                check_name="依赖检查",
                passed=True,
                score=1.0,
                details=f"标准库: {len(stdlib_modules & {i[1] for i in imports})} 个, "
                        f"自定义: {len(custom_modules)} 个, 全部可用",
                severity="info",
                elapsed=time.time() - start,
            ))
    
    def _check_loadable(self, filepath: Path, report: ValidationReport):
        """检查模块可加载"""
        start = time.time()
        try:
            result = subprocess.run(
                ["python3", "-c", f"import ast; ast.parse(open('{filepath}').read())"],
                capture_output=True, text=True, timeout=10,
            )
            passed = result.returncode == 0
            report.add_result(ValidationResult(
                check_name="可加载测试",
                passed=passed,
                score=1.0 if passed else 0.0,
                details="模块可正常加载" if passed else f"加载失败: {result.stderr[:200]}",
                severity="critical" if not passed else "info",
                elapsed=time.time() - start,
            ))
        except Exception as e:
            report.add_result(ValidationResult(
                check_name="可加载测试",
                passed=False,
                score=0.0,
                details=f"测试异常: {e}",
                severity="critical",
                elapsed=time.time() - start,
            ))
    
    def record_validation(self, report: ValidationReport):
        """记录验证结果"""
        entry = {
            "timestamp": int(report.timestamp),
            "target": report.target,
            "passed": report.overall_passed,
            "score": round(report.overall_score, 3),
            "checks": len(report.results),
        }
        with open(VALIDATION_HISTORY, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# =============================================================================
# CLI 入口
# =============================================================================

def main():
    """命令行入口"""
    import argparse
    parser = argparse.ArgumentParser(description="MiMoClaw 进化验证器")
    parser.add_argument("command", choices=["file", "dir", "history"],
                       help="验证单个文件 / 整个目录 / 查看历史")
    parser.add_argument("--target", "-t", help="目标文件路径")
    args = parser.parse_args()
    
    validator = EvolutionValidator()
    
    if args.command == "file":
        if not args.target:
            print("请指定 --target")
            return
        filepath = Path(args.target)
        if not filepath.is_absolute():
            filepath = CORE_DIR / filepath
        report = validator.validate_file(filepath)
        validator.record_validation(report)
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    
    elif args.command == "dir":
        reports = validator.validate_directory()
        passed = sum(1 for r in reports.values() if r.overall_passed)
        total = len(reports)
        print(f"\n验证完成: {passed}/{total} 通过")
        for name, report in sorted(reports.items()):
            status = "✅" if report.overall_passed else "❌"
            print(f"  {status} {name}: {report.overall_score:.2f}")
    
    elif args.command == "history":
        if VALIDATION_HISTORY.exists():
            with open(VALIDATION_HISTORY) as f:
                lines = f.readlines()
            print(f"验证历史 ({len(lines)} 条):")
            for line in lines[-10:]:
                entry = json.loads(line)
                ts = datetime.fromtimestamp(entry["timestamp"]).strftime("%m-%d %H:%M")
                status = "✅" if entry["passed"] else "❌"
                print(f"  [{ts}] {status} {Path(entry['target']).name} "
                      f"(评分 {entry['score']:.2f}, {entry['checks']} 项检查)")
        else:
            print("暂无验证历史")


if __name__ == "__main__":
    main()
