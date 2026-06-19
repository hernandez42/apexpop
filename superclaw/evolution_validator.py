"""
superclaw 进化验证器 — 真实验证闭环和回滚机制

替换 GEP engine 里只看文本长度的假验证。
跑 pytest、检查回归、blast radius 量化、失败回滚。

组件：
- TestRunner: 跑 pytest / verify 脚本 / 冒烟测试
- BlastRadiusCalculator: 量化修改影响范围和风险等级
- SnapshotManager: 文件快照和回滚
- EvolutionValidator: 整合上述组件，执行验证闭环
"""
import json
import logging
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ============================================================
# 数据类
# ============================================================

@dataclass
class TestResult:
    """测试运行结果"""
    passed: bool
    total: int
    passed_count: int
    failed_count: int
    error_count: int
    output: str
    duration_ms: int


@dataclass
class BlastRadius:
    """爆炸半径 — 量化修改的影响范围"""
    files_modified: int
    files_added: int
    files_deleted: int
    lines_added: int
    lines_deleted: int
    risk_level: str  # low/medium/high/critical
    affected_modules: List[str] = field(default_factory=list)


@dataclass
class EvolutionAction:
    """进化动作描述"""
    action_type: str  # add_tool/modify_skill/install_dependency/modify_code
    target_files: List[Path]
    backup_snapshot_id: Optional[str]
    description: str


@dataclass
class ValidationResult:
    """进化验证结果"""
    passed: bool
    test_result: TestResult
    blast_radius: BlastRadius
    rollback_performed: bool
    errors: List[str] = field(default_factory=list)
    recommendations: List[str] = field(default_factory=list)


# ============================================================
# TestRunner — 跑 pytest 和 verify 脚本
# ============================================================

class TestRunner:
    """测试运行器 — 跑 pytest、verify 脚本、冒烟测试"""

    VERIFY_SCRIPTS = [
        "verify.py",
        "verify_memory.py",
        "verify_channels.py",
        "verify_gep.py",
    ]

    def __init__(self, project_root: Path, test_timeout: int = 120):
        self.project_root = Path(project_root)
        self.test_timeout = test_timeout

    def run_unit_tests(self, test_path: Optional[Path] = None) -> TestResult:
        """跑 pytest，返回 TestResult"""
        target = str(test_path) if test_path else "tests/"
        cmd = [sys.executable, "-m", "pytest", target, "-v", "--tb=short"]

        start = time.time()
        try:
            result = subprocess.run(
                cmd,
                cwd=str(self.project_root),
                timeout=self.test_timeout,
                capture_output=True,
                text=True,
            )
            duration_ms = int((time.time() - start) * 1000)
            output = result.stdout + result.stderr
            passed, failed, errors = self._parse_pytest_output(output)
            total = passed + failed + errors
            return TestResult(
                passed=result.returncode == 0 and failed == 0 and errors == 0,
                total=total,
                passed_count=passed,
                failed_count=failed,
                error_count=errors,
                output=output,
                duration_ms=duration_ms,
            )
        except subprocess.TimeoutExpired as e:
            duration_ms = int((time.time() - start) * 1000)
            return TestResult(
                passed=False,
                total=0,
                passed_count=0,
                failed_count=0,
                error_count=0,
                output=f"测试超时（{self.test_timeout}s）: {e}",
                duration_ms=duration_ms,
            )

    def run_verify_scripts(self) -> TestResult:
        """跑 verify*.py 脚本，聚合结果"""
        total_passed = 0
        total_failed = 0
        total_error = 0
        scripts_run = 0
        scripts_failed = 0
        outputs: List[str] = []
        start = time.time()

        for script_name in self.VERIFY_SCRIPTS:
            script_path = self.project_root / script_name
            if not script_path.exists():
                continue
            scripts_run += 1
            try:
                result = subprocess.run(
                    [sys.executable, str(script_path)],
                    cwd=str(self.project_root),
                    timeout=self.test_timeout,
                    capture_output=True,
                    text=True,
                )
                output = result.stdout + result.stderr
                outputs.append(f"=== {script_name} ===\n{output}")
                p, f = self._parse_verify_output(output)
                total_passed += p
                total_failed += f
                if result.returncode != 0:
                    scripts_failed += 1
                    if p == 0 and f == 0:
                        total_error += 1
            except subprocess.TimeoutExpired:
                outputs.append(f"=== {script_name} === 超时")
                scripts_failed += 1
                total_error += 1

        duration_ms = int((time.time() - start) * 1000)
        total = total_passed + total_failed + total_error
        passed = scripts_run > 0 and scripts_failed == 0 and total_failed == 0
        return TestResult(
            passed=passed,
            total=total,
            passed_count=total_passed,
            failed_count=total_failed,
            error_count=total_error,
            output="\n".join(outputs),
            duration_ms=duration_ms,
        )

    def run_smoke_test(self, command: str) -> TestResult:
        """跑冒烟测试命令"""
        import shlex
        start = time.time()
        try:
            argv = shlex.split(command)
            result = subprocess.run(
                argv,
                cwd=str(self.project_root),
                timeout=self.test_timeout,
                capture_output=True,
                text=True,
            )
            duration_ms = int((time.time() - start) * 1000)
            output = result.stdout + result.stderr
            return TestResult(
                passed=result.returncode == 0,
                total=1,
                passed_count=1 if result.returncode == 0 else 0,
                failed_count=0 if result.returncode == 0 else 1,
                error_count=0,
                output=output,
                duration_ms=duration_ms,
            )
        except subprocess.TimeoutExpired:
            duration_ms = int((time.time() - start) * 1000)
            return TestResult(
                passed=False,
                total=1,
                passed_count=0,
                failed_count=0,
                error_count=1,
                output=f"冒烟测试超时（{self.test_timeout}s）",
                duration_ms=duration_ms,
            )

    def _parse_pytest_output(self, output: str) -> Tuple[int, int, int]:
        """解析 pytest 输出，返回 (passed, failed, error)"""
        passed = 0
        failed = 0
        error = 0
        match = re.search(r"(\d+)\s+passed", output)
        if match:
            passed = int(match.group(1))
        match = re.search(r"(\d+)\s+failed", output)
        if match:
            failed = int(match.group(1))
        match = re.search(r"(\d+)\s+error", output)
        if match:
            error = int(match.group(1))
        return passed, failed, error

    def _parse_verify_output(self, output: str) -> Tuple[int, int]:
        """解析 verify 脚本输出，返回 (passed, failed)

        识别 "✓ X 通过" / "✗ Y 失败" / "ALL PASS" 等关键字
        """
        passed = 0
        failed = 0
        match = re.search(r"✓\s*(\d+)\s*通过", output)
        if match:
            passed = int(match.group(1))
        match = re.search(r"✗\s*(\d+)\s*失败", output)
        if match:
            failed = int(match.group(1))
        if "ALL PASS" in output.upper() and passed == 0:
            passed = 1
        return passed, failed


# ============================================================
# BlastRadiusCalculator — 量化爆炸半径
# ============================================================

class BlastRadiusCalculator:
    """爆炸半径计算器 — 量化修改的影响范围和风险等级"""

    # 核心包目录名（修改这些目录 → critical）
    CORE_PACKAGES = ("superclaw", "core-dna")

    def __init__(self, project_root: Path):
        self.project_root = Path(project_root)

    def calculate(self,
                  modified_files: List[Path],
                  added_files: List[Path],
                  deleted_files: List[Path]) -> BlastRadius:
        """计算爆炸半径"""
        all_files = list(modified_files) + list(added_files) + list(deleted_files)

        # 行数变化（git diff --stat）
        lines_added, lines_deleted = self._git_diff_stat()

        # 受影响模块
        affected = self._extract_modules(all_files)

        # 风险等级
        risk_level = self._determine_risk(all_files, lines_added + lines_deleted)

        return BlastRadius(
            files_modified=len(modified_files),
            files_added=len(added_files),
            files_deleted=len(deleted_files),
            lines_added=lines_added,
            lines_deleted=lines_deleted,
            risk_level=risk_level,
            affected_modules=affected,
        )

    def _git_diff_stat(self) -> Tuple[int, int]:
        """跑 git diff --stat，解析行数变化"""
        try:
            result = subprocess.run(
                ["git", "diff", "--stat"],
                cwd=str(self.project_root),
                capture_output=True,
                text=True,
            )
            return self._parse_git_diff_stat(result.stdout)
        except (subprocess.SubprocessError, FileNotFoundError, OSError):
            return 0, 0

    def _parse_git_diff_stat(self, output: str) -> Tuple[int, int]:
        """解析 git diff --stat 输出，返回 (lines_added, lines_deleted)

        示例输出:
            file.py | 10 +++++++---
            1 file changed, 5 insertions(+), 5 deletions(-)
        """
        lines_added = 0
        lines_deleted = 0
        match = re.search(r"(\d+)\s+insertions?\(\+\)", output)
        if match:
            lines_added = int(match.group(1))
        match = re.search(r"(\d+)\s+deletions?\(-\)", output)
        if match:
            lines_deleted = int(match.group(1))
        return lines_added, lines_deleted

    def _extract_modules(self, files: List[Path]) -> List[str]:
        """提取受影响的模块（顶层目录名）"""
        modules: List[str] = []
        for f in files:
            try:
                rel = f.relative_to(self.project_root)
                if len(rel.parts) > 1:
                    module = rel.parts[0]
                    if module not in modules:
                        modules.append(module)
            except ValueError:
                # 文件不在 project_root 下，用父目录名
                name = f.parent.name if f.parent.name else f.name
                if name and name not in modules:
                    modules.append(name)
        return modules

    def _is_critical_path(self, file_path: Path) -> bool:
        """检查文件是否在核心包内（superclaw/ 或 core-dna/）"""
        try:
            rel = file_path.relative_to(self.project_root)
            return len(rel.parts) > 0 and rel.parts[0] in self.CORE_PACKAGES
        except ValueError:
            return False

    def _determine_risk(self, files: List[Path], total_lines: int) -> str:
        """判定风险等级

        - critical: 修改了 superclaw/ 核心包或 core-dna/
        - high: >5 个文件 或 >100 行
        - medium: 3-5 个文件 或 30-100 行
        - low: <3 个文件 且 <30 行
        """
        # critical: 修改了核心包
        for f in files:
            if self._is_critical_path(f):
                return "critical"

        total_files = len(files)

        # high: >5 文件 或 >100 行
        if total_files > 5 or total_lines > 100:
            return "high"

        # medium: 3-5 文件 或 30-100 行
        if 3 <= total_files <= 5 or 30 <= total_lines <= 100:
            return "medium"

        # low: <3 文件 且 <30 行
        return "low"


# ============================================================
# SnapshotManager — 快照和回滚
# ============================================================

class SnapshotManager:
    """快照管理器 — 创建/恢复/列出/删除文件快照"""

    METADATA_FILENAME = "metadata.json"

    def __init__(self, snapshot_dir: Optional[Path] = None):
        if snapshot_dir is None:
            snapshot_dir = Path("superclaw-data/snapshots")
        self.snapshot_dir = Path(snapshot_dir)
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)

    def create_snapshot(self, files: List[Path], label: str) -> str:
        """创建快照，返回快照 ID

        把指定文件复制到 snapshot_dir/{timestamp}_{label}/，
        记录元数据（原路径、时间、label）。
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        snapshot_id = f"{timestamp}_{label}"
        snapshot_path = self.snapshot_dir / snapshot_id
        snapshot_path.mkdir(parents=True, exist_ok=True)

        files_meta: List[Dict] = []
        for i, file_path in enumerate(files):
            file_path = Path(file_path)
            if not file_path.exists():
                continue
            dest_name = f"{i:04d}_{file_path.name}"
            dest = snapshot_path / dest_name
            shutil.copy2(str(file_path), str(dest))
            files_meta.append({
                "original_path": str(file_path),
                "snapshot_path": str(dest),
                "filename": file_path.name,
            })

        metadata = {
            "snapshot_id": snapshot_id,
            "label": label,
            "timestamp": datetime.now().isoformat(),
            "files": files_meta,
        }
        meta_path = snapshot_path / self.METADATA_FILENAME
        meta_path.write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        return snapshot_id

    def restore_snapshot(self, snapshot_id: str) -> bool:
        """恢复快照，把文件复制回原路径"""
        snapshot_path = self.snapshot_dir / snapshot_id
        meta_path = snapshot_path / self.METADATA_FILENAME
        if not meta_path.exists():
            return False

        try:
            metadata = json.loads(meta_path.read_text(encoding="utf-8"))
            for file_meta in metadata.get("files", []):
                src = Path(file_meta["snapshot_path"])
                dst = Path(file_meta["original_path"])
                if src.exists():
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(str(src), str(dst))
            return True
        except (json.JSONDecodeError, IOError, KeyError):
            return False

    def list_snapshots(self) -> List[Dict]:
        """列出所有快照"""
        snapshots: List[Dict] = []
        if not self.snapshot_dir.exists():
            return snapshots
        for entry in self.snapshot_dir.iterdir():
            if not entry.is_dir():
                continue
            meta_path = entry / self.METADATA_FILENAME
            if not meta_path.exists():
                continue
            try:
                metadata = json.loads(meta_path.read_text(encoding="utf-8"))
                snapshots.append({
                    "id": metadata.get("snapshot_id", entry.name),
                    "label": metadata.get("label", ""),
                    "timestamp": metadata.get("timestamp", ""),
                    "files_count": len(metadata.get("files", [])),
                })
            except json.JSONDecodeError:
                continue
        return snapshots

    def delete_snapshot(self, snapshot_id: str) -> bool:
        """删除快照"""
        snapshot_path = self.snapshot_dir / snapshot_id
        if not snapshot_path.exists():
            return False
        try:
            shutil.rmtree(str(snapshot_path))
            return True
        except IOError:
            return False

    def cleanup_old(self, keep: int = 10) -> int:
        """保留最近 N 个快照，删除其余，返回删除数量"""
        snapshots = self.list_snapshots()
        if len(snapshots) <= keep:
            return 0
        # 按 id（时间戳前缀）排序，旧的在前
        snapshots.sort(key=lambda s: s["id"])
        if keep <= 0:
            to_delete = snapshots
        else:
            to_delete = snapshots[:-keep]
        for s in to_delete:
            self.delete_snapshot(s["id"])
        return len(to_delete)


# ============================================================
# GitSnapshotManager — git-based 事务性快照与回滚
# ============================================================

class GitSnapshotManager:
    """git-based 快照管理器 — 用 git stash 做事务性回滚

    相比 SnapshotManager 的文件复制方式，git-based 回滚的优势：
    - 事务性：多文件回滚要么全成功要么全失败（git checkout 原子操作）
    - 完整性：自动追踪所有被 git 管理的文件，不漏文件
    - 可审计：git stash list 可查历史

    局限：
    - 只能回滚 git 已追踪的文件（新文件需先 git add）
    - 需要 git 可用且项目已初始化
    - 不适用于无 git 的环境（降级到 SnapshotManager）

    用法：
        mgr = GitSnapshotManager(project_root)
        sid = mgr.create_snapshot([file1, file2], "before_evolution")
        # ... 修改文件 ...
        if test_failed:
            mgr.restore_snapshot(sid)  # 原子回滚
    """

    def __init__(self, project_root: Path):
        self.project_root = Path(project_root)

    def _git(self, args: List[str]) -> Tuple[bool, str]:
        """执行 git 命令，返回 (success, output)"""
        try:
            result = subprocess.run(
                ["git"] + args,
                cwd=str(self.project_root),
                capture_output=True,
                text=True,
                timeout=30,
            )
            output = (result.stdout or "") + (result.stderr or "")
            return result.returncode == 0, output.strip()
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
            return False, str(e)

    def _ensure_git(self) -> bool:
        """检查 git 可用且项目已初始化"""
        ok, _ = self._git(["rev-parse", "--is-inside-work-tree"])
        return ok

    def create_snapshot(self, files: List[Path], label: str) -> str:
        """创建 git stash 快照，返回快照 ID

        流程：
        1. git add 指定文件（确保新文件也被追踪）
        2. git stash create 创建临时 commit（不影响工作区）
        3. 返回 commit hash 作为快照 ID
        """
        if not self._ensure_git():
            # 降级：返回空 ID，restore 时会失败并报错
            return ""

        # git add 指定文件
        for f in files:
            f = Path(f)
            if f.exists():
                self._git(["add", str(f)])

        # git stash create 返回 commit hash（不影响工作区和 stash list）
        ok, commit_hash = self._git(["stash", "create"])
        if not ok or not commit_hash:
            # 没有改动可 stash，返回特殊标记
            return "no_changes"

        return commit_hash.strip()

    def restore_snapshot(self, snapshot_id: str) -> bool:
        """从 git stash 恢复（原子回滚）

        用 git checkout <commit> -- <files> 恢复指定快照的文件状态。
        """
        if not snapshot_id or snapshot_id == "no_changes":
            return snapshot_id == "no_changes"  # 无改动算成功

        if not self._ensure_git():
            return False

        # 用 git checkout 从快照 commit 恢复所有被追踪文件
        # 这是原子操作：要么全恢复要么不恢复
        ok, out = self._git(["checkout", snapshot_id, "--", "."])
        if not ok:
            logger.warning("git 回滚失败: %s", out)
            return False
        return True

    def list_snapshots(self) -> List[Dict]:
        """列出 git stash 历史"""
        ok, out = self._git(["stash", "list"])
        if not ok:
            return []
        snapshots: List[Dict] = []
        for line in out.splitlines():
            if line.strip():
                snapshots.append({"id": line.split(":")[0], "label": line.strip()})
        return snapshots

    def delete_snapshot(self, snapshot_id: str) -> bool:
        """删除快照（git stash drop）"""
        if not snapshot_id or snapshot_id == "no_changes":
            return True
        ok, _ = self._git(["stash", "drop", snapshot_id])
        return ok

    def cleanup_old(self, keep: int = 10) -> int:
        """git stash 不支持保留最近 N 个，返回 0"""
        return 0


# ============================================================
# EvolutionValidator — 整合验证闭环
# ============================================================

class EvolutionValidator:
    """进化验证器 — 整合测试、爆炸半径、回滚的验证闭环

    流程:
    a. 计算 blast radius
    b. 如果 risk_level == critical，拒绝（不允许修改核心包）
    c. 跑单元测试
    d. 如果测试失败，自动恢复快照，设 rollback_performed=True
    e. 返回结果

    支持两种快照管理器：
    - SnapshotManager: 文件复制方式（默认，无 git 依赖）
    - GitSnapshotManager: git-based 事务性回滚（更可靠，需 git）
    """

    def __init__(self,
                 project_root: Path,
                 test_runner: TestRunner,
                 snapshot_mgr: SnapshotManager):
        self.project_root = Path(project_root)
        self.test_runner = test_runner
        self.snapshot_mgr = snapshot_mgr
        self.blast_calculator = BlastRadiusCalculator(self.project_root)

    def validate_evolution(self, action: EvolutionAction) -> ValidationResult:
        """验证进化动作，执行完整的验证闭环"""
        errors: List[str] = []
        recommendations: List[str] = []

        # a. 计算 blast radius
        blast_radius = self.blast_calculator.calculate(
            modified_files=action.target_files,
            added_files=[],
            deleted_files=[],
        )

        # b. critical 拒绝（不允许修改核心包）
        if blast_radius.risk_level == "critical":
            errors.append("拒绝：修改核心包（superclaw/ 或 core-dna/）不允许")
            recommendations.append("使用扩展机制，不要直接修改核心包")
            return ValidationResult(
                passed=False,
                test_result=self._empty_test_result(),
                blast_radius=blast_radius,
                rollback_performed=False,
                errors=errors,
                recommendations=recommendations,
            )

        # c. 跑单元测试
        test_result = self.test_runner.run_unit_tests()

        # d. 测试失败 → 自动回滚
        if not test_result.passed:
            rollback_performed = False
            if action.backup_snapshot_id:
                restored = self.snapshot_mgr.restore_snapshot(action.backup_snapshot_id)
                if restored:
                    rollback_performed = True
                    recommendations.append(
                        f"已自动回滚到快照 {action.backup_snapshot_id}"
                    )
                else:
                    errors.append(
                        f"回滚失败：快照 {action.backup_snapshot_id} 不存在或恢复失败"
                    )
            else:
                errors.append("测试失败但无备份快照，无法回滚")
            return ValidationResult(
                passed=False,
                test_result=test_result,
                blast_radius=blast_radius,
                rollback_performed=rollback_performed,
                errors=errors,
                recommendations=recommendations,
            )

        # e. 通过
        recommendations.append("进化通过验证")
        return ValidationResult(
            passed=True,
            test_result=test_result,
            blast_radius=blast_radius,
            rollback_performed=False,
            errors=[],
            recommendations=recommendations,
        )

    @staticmethod
    def _empty_test_result() -> TestResult:
        """构造空测试结果（用于 critical 拒绝时）"""
        return TestResult(
            passed=False,
            total=0,
            passed_count=0,
            failed_count=0,
            error_count=0,
            output="",
            duration_ms=0,
        )
