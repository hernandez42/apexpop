"""测试 superclaw.evolution_validator — 真实验证闭环和回滚机制

覆盖：
- TestRunner: pytest 命令、输出解析（passed/failed/error）、超时、verify 脚本、冒烟测试
- BlastRadiusCalculator: git diff 解析、行数计算、risk_level 判定（low/medium/high/critical）
- SnapshotManager: 真实文件操作（创建/恢复/列出/删除/cleanup_old）
- EvolutionValidator: 整合测试（低风险通过、critical 拒绝、测试失败回滚、无快照不回滚）
- 完整流程：创建快照 → 修改文件 → 跑测试（失败）→ 自动回滚 → 验证恢复
"""
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from superclaw.evolution_validator import (
    BlastRadiusCalculator,
    EvolutionAction,
    EvolutionValidator,
    SnapshotManager,
    TestResult,
    TestRunner,
)


# ============================================================
# Helper
# ============================================================

def fake_completed(returncode=0, stdout="", stderr=""):
    """构造 fake subprocess.CompletedProcess"""
    return subprocess.CompletedProcess(
        args=["fake"],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


def _make_verify_scripts(project_root: Path):
    """在 project_root 下创建 4 个 verify 脚本占位文件"""
    for name in TestRunner.VERIFY_SCRIPTS:
        (project_root / name).write_text("# verify script\n", encoding="utf-8")


# ============================================================
# TestRunner 测试
# ============================================================

class TestTestRunner:
    def test_init_defaults(self, tmp_path):
        runner = TestRunner(tmp_path)
        assert runner.project_root == tmp_path
        assert runner.test_timeout == 120

    def test_init_custom_timeout(self, tmp_path):
        runner = TestRunner(tmp_path, test_timeout=60)
        assert runner.test_timeout == 60

    @patch("superclaw.evolution_validator.subprocess.run")
    def test_run_unit_tests_all_pass(self, mock_run, tmp_path):
        """pytest 全部通过"""
        mock_run.return_value = fake_completed(
            returncode=0,
            stdout="===== 5 passed in 1.23s =====",
        )
        runner = TestRunner(tmp_path)
        result = runner.run_unit_tests()

        assert result.passed is True
        assert result.passed_count == 5
        assert result.failed_count == 0
        assert result.error_count == 0
        assert result.total == 5
        assert result.duration_ms >= 0

        # 验证 pytest 命令
        args, kwargs = mock_run.call_args
        cmd = args[0]
        assert sys.executable in cmd
        assert "pytest" in cmd
        assert "-v" in cmd
        assert "--tb=short" in cmd
        assert kwargs["cwd"] == str(tmp_path)
        assert kwargs["timeout"] == 120

    @patch("superclaw.evolution_validator.subprocess.run")
    def test_run_unit_tests_with_failures(self, mock_run, tmp_path):
        """pytest 有失败"""
        mock_run.return_value = fake_completed(
            returncode=1,
            stdout="===== 3 passed, 2 failed in 1.0s =====",
        )
        runner = TestRunner(tmp_path)
        result = runner.run_unit_tests()

        assert result.passed is False
        assert result.passed_count == 3
        assert result.failed_count == 2
        assert result.error_count == 0
        assert result.total == 5

    @patch("superclaw.evolution_validator.subprocess.run")
    def test_run_unit_tests_with_errors(self, mock_run, tmp_path):
        """pytest 有 error"""
        mock_run.return_value = fake_completed(
            returncode=1,
            stdout="===== 1 passed, 1 failed, 2 errors in 1.0s =====",
        )
        runner = TestRunner(tmp_path)
        result = runner.run_unit_tests()

        assert result.passed is False
        assert result.passed_count == 1
        assert result.failed_count == 1
        assert result.error_count == 2
        assert result.total == 4

    @patch("superclaw.evolution_validator.subprocess.run")
    def test_run_unit_tests_timeout(self, mock_run, tmp_path):
        """超时 → 失败，输出包含超时信息"""
        mock_run.side_effect = subprocess.TimeoutExpired(
            cmd="pytest", timeout=5,
        )
        runner = TestRunner(tmp_path, test_timeout=5)
        result = runner.run_unit_tests()

        assert result.passed is False
        assert "超时" in result.output

    @patch("superclaw.evolution_validator.subprocess.run")
    def test_run_unit_tests_custom_path(self, mock_run, tmp_path):
        """指定测试路径 → 命令包含该路径"""
        mock_run.return_value = fake_completed(
            returncode=0,
            stdout="===== 1 passed in 1.0s =====",
        )
        runner = TestRunner(tmp_path)
        custom = tmp_path / "tests" / "test_x.py"
        runner.run_unit_tests(test_path=custom)

        args, _ = mock_run.call_args
        assert str(custom) in args[0]

    @patch("superclaw.evolution_validator.subprocess.run")
    def test_run_unit_tests_default_path(self, mock_run, tmp_path):
        """不指定路径 → 默认 tests/"""
        mock_run.return_value = fake_completed(
            returncode=0,
            stdout="===== 1 passed in 1.0s =====",
        )
        runner = TestRunner(tmp_path)
        runner.run_unit_tests()

        args, _ = mock_run.call_args
        assert "tests/" in args[0]

    @patch("superclaw.evolution_validator.subprocess.run")
    def test_run_verify_scripts_all_pass(self, mock_run, tmp_path):
        """所有 verify 脚本通过"""
        _make_verify_scripts(tmp_path)
        mock_run.return_value = fake_completed(
            returncode=0,
            stdout="结果:  ✓ 7 通过  |  ✗ 0 失败",
        )
        runner = TestRunner(tmp_path)
        result = runner.run_verify_scripts()

        assert result.passed is True
        assert mock_run.call_count == 4
        assert result.passed_count == 28  # 7 * 4
        assert result.failed_count == 0

    @patch("superclaw.evolution_validator.subprocess.run")
    def test_run_verify_scripts_some_fail(self, mock_run, tmp_path):
        """部分 verify 脚本失败"""
        _make_verify_scripts(tmp_path)
        mock_run.return_value = fake_completed(
            returncode=1,
            stdout="结果:  ✓ 5 通过  |  ✗ 2 失败",
        )
        runner = TestRunner(tmp_path)
        result = runner.run_verify_scripts()

        assert result.passed is False
        assert result.failed_count == 8  # 2 * 4
        assert result.passed_count == 20  # 5 * 4

    @patch("superclaw.evolution_validator.subprocess.run")
    def test_run_verify_scripts_all_pass_keyword(self, mock_run, tmp_path):
        """verify 输出含 ALL PASS → 通过"""
        _make_verify_scripts(tmp_path)
        mock_run.return_value = fake_completed(
            returncode=0,
            stdout="ALL PASS",
        )
        runner = TestRunner(tmp_path)
        result = runner.run_verify_scripts()

        assert result.passed is True
        assert result.passed_count == 4  # 每个 script 解析出 1 个 pass

    @patch("superclaw.evolution_validator.subprocess.run")
    def test_run_verify_scripts_no_scripts(self, mock_run, tmp_path):
        """没有 verify 脚本 → 不调用 subprocess，passed=False"""
        runner = TestRunner(tmp_path)
        result = runner.run_verify_scripts()

        assert mock_run.call_count == 0
        assert result.passed is False

    @patch("superclaw.evolution_validator.subprocess.run")
    def test_run_smoke_test_pass(self, mock_run, tmp_path):
        """冒烟测试通过"""
        mock_run.return_value = fake_completed(
            returncode=0, stdout="OK",
        )
        runner = TestRunner(tmp_path)
        result = runner.run_smoke_test("python cli.py --version")

        assert result.passed is True
        assert result.passed_count == 1
        assert result.failed_count == 0
        assert result.total == 1

    @patch("superclaw.evolution_validator.subprocess.run")
    def test_run_smoke_test_fail(self, mock_run, tmp_path):
        """冒烟测试失败"""
        mock_run.return_value = fake_completed(
            returncode=1, stderr="error",
        )
        runner = TestRunner(tmp_path)
        result = runner.run_smoke_test("python cli.py --bad")

        assert result.passed is False
        assert result.failed_count == 1
        assert result.passed_count == 0

    @patch("superclaw.evolution_validator.subprocess.run")
    def test_run_smoke_test_timeout(self, mock_run, tmp_path):
        """冒烟测试超时"""
        mock_run.side_effect = subprocess.TimeoutExpired(
            cmd="bad", timeout=5,
        )
        runner = TestRunner(tmp_path, test_timeout=5)
        result = runner.run_smoke_test("python cli.py --slow")

        assert result.passed is False
        assert result.error_count == 1
        assert "超时" in result.output

    def test_parse_pytest_output_variants(self, tmp_path):
        """直接测试 pytest 输出解析器"""
        runner = TestRunner(tmp_path)
        assert runner._parse_pytest_output("5 passed") == (5, 0, 0)
        assert runner._parse_pytest_output("3 passed, 2 failed") == (3, 2, 0)
        assert runner._parse_pytest_output("1 passed, 1 failed, 2 errors") == (1, 1, 2)
        assert runner._parse_pytest_output("no tests ran") == (0, 0, 0)

    def test_parse_verify_output_variants(self, tmp_path):
        """直接测试 verify 输出解析器"""
        runner = TestRunner(tmp_path)
        assert runner._parse_verify_output("✓ 7 通过  |  ✗ 0 失败") == (7, 0)
        assert runner._parse_verify_output("✓ 5 通过  |  ✗ 2 失败") == (5, 2)
        assert runner._parse_verify_output("ALL PASS") == (1, 0)
        assert runner._parse_verify_output("nothing matched") == (0, 0)


# ============================================================
# BlastRadiusCalculator 测试
# ============================================================

class TestBlastRadiusCalculator:
    @patch("superclaw.evolution_validator.subprocess.run")
    def test_low_risk(self, mock_run, tmp_path):
        """<3 文件 且 <30 行 → low"""
        mock_run.return_value = fake_completed(
            returncode=0,
            stdout=" file.py | 10 +++++++---\n"
                   " 1 file changed, 5 insertions(+), 5 deletions(-)",
        )
        calc = BlastRadiusCalculator(tmp_path)
        result = calc.calculate(
            modified_files=[tmp_path / "file.py"],
            added_files=[],
            deleted_files=[],
        )

        assert result.risk_level == "low"
        assert result.files_modified == 1
        assert result.files_added == 0
        assert result.files_deleted == 0
        assert result.lines_added == 5
        assert result.lines_deleted == 5

    @patch("superclaw.evolution_validator.subprocess.run")
    def test_medium_risk_by_file_count(self, mock_run, tmp_path):
        """3 文件 → medium"""
        mock_run.return_value = fake_completed(
            returncode=0,
            stdout=" 3 files changed, 20 insertions(+)",
        )
        calc = BlastRadiusCalculator(tmp_path)
        result = calc.calculate(
            modified_files=[tmp_path / f"f{i}.py" for i in range(3)],
            added_files=[],
            deleted_files=[],
        )

        assert result.risk_level == "medium"
        assert result.files_modified == 3

    @patch("superclaw.evolution_validator.subprocess.run")
    def test_medium_risk_by_line_count(self, mock_run, tmp_path):
        """2 文件但 50 行 → medium（30-100 行）"""
        mock_run.return_value = fake_completed(
            returncode=0,
            stdout=" 2 files changed, 50 insertions(+)",
        )
        calc = BlastRadiusCalculator(tmp_path)
        result = calc.calculate(
            modified_files=[tmp_path / "a.py", tmp_path / "b.py"],
            added_files=[],
            deleted_files=[],
        )

        assert result.risk_level == "medium"

    @patch("superclaw.evolution_validator.subprocess.run")
    def test_high_risk_many_files(self, mock_run, tmp_path):
        """>5 文件 → high"""
        mock_run.return_value = fake_completed(
            returncode=0,
            stdout=" 6 files changed, 50 insertions(+)",
        )
        calc = BlastRadiusCalculator(tmp_path)
        files = [tmp_path / f"f{i}.py" for i in range(6)]
        result = calc.calculate(
            modified_files=files,
            added_files=[],
            deleted_files=[],
        )

        assert result.risk_level == "high"
        assert result.files_modified == 6

    @patch("superclaw.evolution_validator.subprocess.run")
    def test_high_risk_many_lines(self, mock_run, tmp_path):
        """>100 行 → high"""
        mock_run.return_value = fake_completed(
            returncode=0,
            stdout=" 2 files changed, 150 insertions(+)",
        )
        calc = BlastRadiusCalculator(tmp_path)
        result = calc.calculate(
            modified_files=[tmp_path / "a.py", tmp_path / "b.py"],
            added_files=[],
            deleted_files=[],
        )

        assert result.risk_level == "high"
        assert result.lines_added == 150

    @patch("superclaw.evolution_validator.subprocess.run")
    def test_critical_core_package(self, mock_run, tmp_path):
        """修改 superclaw/ 核心包 → critical"""
        mock_run.return_value = fake_completed(
            returncode=0,
            stdout=" 1 file changed, 5 insertions(+)",
        )
        calc = BlastRadiusCalculator(tmp_path)
        result = calc.calculate(
            modified_files=[tmp_path / "superclaw" / "agent.py"],
            added_files=[],
            deleted_files=[],
        )

        assert result.risk_level == "critical"

    @patch("superclaw.evolution_validator.subprocess.run")
    def test_critical_core_dna(self, mock_run, tmp_path):
        """修改 core-dna/ → critical"""
        mock_run.return_value = fake_completed(
            returncode=0,
            stdout=" 1 file changed, 5 insertions(+)",
        )
        calc = BlastRadiusCalculator(tmp_path)
        result = calc.calculate(
            modified_files=[tmp_path / "core-dna" / "main_pipe.c"],
            added_files=[],
            deleted_files=[],
        )

        assert result.risk_level == "critical"

    @patch("superclaw.evolution_validator.subprocess.run")
    def test_critical_added_file_in_core(self, mock_run, tmp_path):
        """新增文件到核心包 → critical"""
        mock_run.return_value = fake_completed(
            returncode=0,
            stdout=" 1 file changed, 5 insertions(+)",
        )
        calc = BlastRadiusCalculator(tmp_path)
        result = calc.calculate(
            modified_files=[],
            added_files=[tmp_path / "superclaw" / "new_tool.py"],
            deleted_files=[],
        )

        assert result.risk_level == "critical"
        assert result.files_added == 1

    @patch("superclaw.evolution_validator.subprocess.run")
    def test_affected_modules(self, mock_run, tmp_path):
        """affected_modules 包含顶层目录名"""
        mock_run.return_value = fake_completed(
            returncode=0,
            stdout=" 1 file changed, 5 insertions(+)",
        )
        calc = BlastRadiusCalculator(tmp_path)
        result = calc.calculate(
            modified_files=[tmp_path / "src" / "module_a" / "file.py"],
            added_files=[tmp_path / "src" / "module_b" / "new.py"],
            deleted_files=[],
        )

        assert "src" in result.affected_modules

    @patch("superclaw.evolution_validator.subprocess.run")
    def test_git_diff_stat_parsing(self, mock_run, tmp_path):
        """直接测试 git diff --stat 解析"""
        calc = BlastRadiusCalculator(tmp_path)

        # 标准格式
        assert calc._parse_git_diff_stat(
            " 1 file changed, 5 insertions(+), 3 deletions(-)"
        ) == (5, 3)

        # 只有 insertions
        assert calc._parse_git_diff_stat(
            " 3 files changed, 30 insertions(+)"
        ) == (30, 0)

        # 只有 deletions
        assert calc._parse_git_diff_stat(
            " 1 file changed, 5 deletions(-)"
        ) == (0, 5)

        # 空输出
        assert calc._parse_git_diff_stat("") == (0, 0)

    @patch("superclaw.evolution_validator.subprocess.run")
    def test_git_diff_failure_returns_zero(self, mock_run, tmp_path):
        """git diff 失败 → 行数为 0"""
        mock_run.side_effect = FileNotFoundError("git not found")
        calc = BlastRadiusCalculator(tmp_path)
        result = calc.calculate(
            modified_files=[tmp_path / "file.py"],
            added_files=[],
            deleted_files=[],
        )

        assert result.lines_added == 0
        assert result.lines_deleted == 0


# ============================================================
# SnapshotManager 测试（真实文件操作）
# ============================================================

class TestSnapshotManager:
    def test_create_snapshot(self, tmp_workspace):
        """创建快照 → 目录和元数据存在"""
        src_file = tmp_workspace / "src.py"
        src_file.write_text("original content", encoding="utf-8")

        snap_dir = tmp_workspace / "snapshots"
        mgr = SnapshotManager(snap_dir)
        snap_id = mgr.create_snapshot([src_file], label="before_change")

        assert snap_id is not None
        assert "before_change" in snap_id
        assert snap_dir.exists()
        assert (snap_dir / snap_id / "metadata.json").exists()

    def test_create_snapshot_multiple_files(self, tmp_workspace):
        """创建多文件快照"""
        f1 = tmp_workspace / "a.py"
        f1.write_text("a", encoding="utf-8")
        f2 = tmp_workspace / "b.py"
        f2.write_text("b", encoding="utf-8")

        mgr = SnapshotManager(tmp_workspace / "snapshots")
        snap_id = mgr.create_snapshot([f1, f2], label="multi")

        assert snap_id is not None
        snaps = mgr.list_snapshots()
        assert len(snaps) == 1
        assert snaps[0]["files_count"] == 2

    def test_create_snapshot_nonexistent_file_skipped(self, tmp_workspace):
        """不存在的文件被跳过"""
        real_file = tmp_workspace / "real.py"
        real_file.write_text("real", encoding="utf-8")
        fake_file = tmp_workspace / "fake.py"

        mgr = SnapshotManager(tmp_workspace / "snapshots")
        snap_id = mgr.create_snapshot([real_file, fake_file], label="mixed")

        assert snap_id is not None
        snaps = mgr.list_snapshots()
        assert snaps[0]["files_count"] == 1  # 只有 real_file

    def test_restore_snapshot(self, tmp_workspace):
        """恢复快照 → 文件回到原内容"""
        src_file = tmp_workspace / "src.py"
        src_file.write_text("original", encoding="utf-8")

        snap_dir = tmp_workspace / "snapshots"
        mgr = SnapshotManager(snap_dir)
        snap_id = mgr.create_snapshot([src_file], label="backup")

        # 修改文件
        src_file.write_text("modified", encoding="utf-8")
        assert src_file.read_text() == "modified"

        # 恢复
        result = mgr.restore_snapshot(snap_id)
        assert result is True
        assert src_file.read_text() == "original"

    def test_restore_snapshot_creates_parent_dirs(self, tmp_workspace):
        """恢复快照时自动创建父目录"""
        src_file = tmp_workspace / "deep" / "nested" / "src.py"
        src_file.parent.mkdir(parents=True)
        src_file.write_text("original", encoding="utf-8")

        mgr = SnapshotManager(tmp_workspace / "snapshots")
        snap_id = mgr.create_snapshot([src_file], label="deep")

        # 删除原文件和父目录
        src_file.unlink()
        src_file.parent.rmdir()
        src_file.parent.parent.rmdir()
        assert not src_file.exists()

        # 恢复
        result = mgr.restore_snapshot(snap_id)
        assert result is True
        assert src_file.read_text() == "original"

    def test_list_snapshots(self, tmp_workspace):
        """列出快照"""
        src_file = tmp_workspace / "src.py"
        src_file.write_text("content", encoding="utf-8")

        mgr = SnapshotManager(tmp_workspace / "snapshots")
        mgr.create_snapshot([src_file], label="first")
        time.sleep(0.001)
        mgr.create_snapshot([src_file], label="second")

        snaps = mgr.list_snapshots()
        assert len(snaps) == 2
        labels = {s["label"] for s in snaps}
        assert labels == {"first", "second"}
        for s in snaps:
            assert "id" in s
            assert "label" in s
            assert "timestamp" in s
            assert "files_count" in s

    def test_delete_snapshot(self, tmp_workspace):
        """删除快照"""
        src_file = tmp_workspace / "src.py"
        src_file.write_text("content", encoding="utf-8")

        mgr = SnapshotManager(tmp_workspace / "snapshots")
        snap_id = mgr.create_snapshot([src_file], label="to_delete")

        assert mgr.delete_snapshot(snap_id) is True
        assert len(mgr.list_snapshots()) == 0

    def test_delete_nonexistent_snapshot(self, tmp_workspace):
        """删除不存在的快照 → False"""
        mgr = SnapshotManager(tmp_workspace / "snapshots")
        assert mgr.delete_snapshot("nonexistent") is False

    def test_restore_nonexistent_snapshot(self, tmp_workspace):
        """恢复不存在的快照 → False"""
        mgr = SnapshotManager(tmp_workspace / "snapshots")
        assert mgr.restore_snapshot("nonexistent") is False

    def test_cleanup_old(self, tmp_workspace):
        """保留最近 N 个，删除其余"""
        src_file = tmp_workspace / "src.py"
        src_file.write_text("content", encoding="utf-8")

        mgr = SnapshotManager(tmp_workspace / "snapshots")
        for i in range(5):
            mgr.create_snapshot([src_file], label=f"l{i}")
            time.sleep(0.001)

        deleted = mgr.cleanup_old(keep=3)
        assert deleted == 2
        assert len(mgr.list_snapshots()) == 3

    def test_cleanup_old_keep_all(self, tmp_workspace):
        """keep >= 现有数量 → 不删除"""
        src_file = tmp_workspace / "src.py"
        src_file.write_text("content", encoding="utf-8")

        mgr = SnapshotManager(tmp_workspace / "snapshots")
        for i in range(3):
            mgr.create_snapshot([src_file], label=f"l{i}")
            time.sleep(0.001)

        deleted = mgr.cleanup_old(keep=10)
        assert deleted == 0
        assert len(mgr.list_snapshots()) == 3

    def test_default_snapshot_dir(self, tmp_workspace):
        """不传 snapshot_dir → 默认 superclaw-data/snapshots/"""
        mgr = SnapshotManager()
        assert mgr.snapshot_dir == Path("superclaw-data/snapshots")
        # 清理（避免污染工作区）
        import shutil
        if mgr.snapshot_dir.exists():
            shutil.rmtree(str(mgr.snapshot_dir))


# ============================================================
# EvolutionValidator 测试
# ============================================================

class TestEvolutionValidator:
    def _make_runner(self, passed=True, failed=0, error=0, total=5):
        runner = MagicMock(spec=TestRunner)
        runner.run_unit_tests.return_value = TestResult(
            passed=passed,
            total=total,
            passed_count=total - failed - error,
            failed_count=failed,
            error_count=error,
            output="ok" if passed else "failed",
            duration_ms=100,
        )
        return runner

    @patch("superclaw.evolution_validator.subprocess.run")
    def test_low_risk_passes(self, mock_run, tmp_workspace):
        """低风险 + 测试通过 → 验证通过"""
        mock_run.return_value = fake_completed(
            returncode=0,
            stdout=" 1 file changed, 5 insertions(+)",
        )
        runner = self._make_runner(passed=True)
        snap_mgr = SnapshotManager(tmp_workspace / "snapshots")
        validator = EvolutionValidator(tmp_workspace, runner, snap_mgr)

        action = EvolutionAction(
            action_type="modify_code",
            target_files=[tmp_workspace / "src.py"],
            backup_snapshot_id=None,
            description="small fix",
        )
        result = validator.validate_evolution(action)

        assert result.passed is True
        assert result.rollback_performed is False
        assert result.blast_radius.risk_level == "low"
        assert len(result.errors) == 0
        runner.run_unit_tests.assert_called_once()

    @patch("superclaw.evolution_validator.subprocess.run")
    def test_critical_rejected(self, mock_run, tmp_workspace):
        """修改核心包 → 拒绝，不跑测试"""
        mock_run.return_value = fake_completed(
            returncode=0,
            stdout=" 1 file changed, 5 insertions(+)",
        )
        runner = self._make_runner(passed=True)
        snap_mgr = SnapshotManager(tmp_workspace / "snapshots")
        validator = EvolutionValidator(tmp_workspace, runner, snap_mgr)

        action = EvolutionAction(
            action_type="modify_code",
            target_files=[tmp_workspace / "superclaw" / "agent.py"],
            backup_snapshot_id=None,
            description="modify core",
        )
        result = validator.validate_evolution(action)

        assert result.passed is False
        assert result.blast_radius.risk_level == "critical"
        assert len(result.errors) > 0
        assert any("核心" in e or "critical" in e.lower() for e in result.errors)
        # critical 时不应跑测试
        runner.run_unit_tests.assert_not_called()

    @patch("superclaw.evolution_validator.subprocess.run")
    def test_critical_core_dna_rejected(self, mock_run, tmp_workspace):
        """修改 core-dna/ → 拒绝"""
        mock_run.return_value = fake_completed(
            returncode=0,
            stdout=" 1 file changed, 5 insertions(+)",
        )
        runner = self._make_runner(passed=True)
        snap_mgr = SnapshotManager(tmp_workspace / "snapshots")
        validator = EvolutionValidator(tmp_workspace, runner, snap_mgr)

        action = EvolutionAction(
            action_type="modify_code",
            target_files=[tmp_workspace / "core-dna" / "main_pipe.c"],
            backup_snapshot_id=None,
            description="modify core-dna",
        )
        result = validator.validate_evolution(action)

        assert result.passed is False
        assert result.blast_radius.risk_level == "critical"

    @patch("superclaw.evolution_validator.subprocess.run")
    def test_test_failure_with_rollback(self, mock_run, tmp_workspace):
        """测试失败 + 有快照 → 自动回滚"""
        mock_run.return_value = fake_completed(
            returncode=0,
            stdout=" 1 file changed, 5 insertions(+)",
        )
        src_file = tmp_workspace / "src.py"
        src_file.write_text("original", encoding="utf-8")

        snap_mgr = SnapshotManager(tmp_workspace / "snapshots")
        snap_id = snap_mgr.create_snapshot([src_file], label="backup")

        # 修改文件
        src_file.write_text("modified", encoding="utf-8")

        runner = self._make_runner(passed=False, failed=2)
        validator = EvolutionValidator(tmp_workspace, runner, snap_mgr)

        action = EvolutionAction(
            action_type="modify_code",
            target_files=[src_file],
            backup_snapshot_id=snap_id,
            description="change that breaks tests",
        )
        result = validator.validate_evolution(action)

        assert result.passed is False
        assert result.rollback_performed is True
        assert src_file.read_text() == "original"
        assert any("回滚" in r for r in result.recommendations)

    @patch("superclaw.evolution_validator.subprocess.run")
    def test_test_failure_no_snapshot(self, mock_run, tmp_workspace):
        """测试失败 + 无快照 → 不回滚，记录错误"""
        mock_run.return_value = fake_completed(
            returncode=0,
            stdout=" 1 file changed, 5 insertions(+)",
        )
        runner = self._make_runner(passed=False, failed=2)
        snap_mgr = SnapshotManager(tmp_workspace / "snapshots")
        validator = EvolutionValidator(tmp_workspace, runner, snap_mgr)

        action = EvolutionAction(
            action_type="modify_code",
            target_files=[tmp_workspace / "src.py"],
            backup_snapshot_id=None,
            description="change without backup",
        )
        result = validator.validate_evolution(action)

        assert result.passed is False
        assert result.rollback_performed is False
        assert any("无法回滚" in e or "无备份" in e for e in result.errors)

    @patch("superclaw.evolution_validator.subprocess.run")
    def test_test_failure_invalid_snapshot(self, mock_run, tmp_workspace):
        """测试失败 + 无效快照 ID → 回滚失败"""
        mock_run.return_value = fake_completed(
            returncode=0,
            stdout=" 1 file changed, 5 insertions(+)",
        )
        runner = self._make_runner(passed=False, failed=1)
        snap_mgr = SnapshotManager(tmp_workspace / "snapshots")
        validator = EvolutionValidator(tmp_workspace, runner, snap_mgr)

        action = EvolutionAction(
            action_type="modify_code",
            target_files=[tmp_workspace / "src.py"],
            backup_snapshot_id="invalid-snapshot-id",
            description="change with bad backup",
        )
        result = validator.validate_evolution(action)

        assert result.passed is False
        assert result.rollback_performed is False
        assert any("回滚失败" in e for e in result.errors)

    @patch("superclaw.evolution_validator.subprocess.run")
    def test_high_risk_passes_if_tests_pass(self, mock_run, tmp_workspace):
        """高风险但测试通过 → 通过（不拒绝高风险）"""
        mock_run.return_value = fake_completed(
            returncode=0,
            stdout=" 6 files changed, 150 insertions(+)",
        )
        runner = self._make_runner(passed=True)
        snap_mgr = SnapshotManager(tmp_workspace / "snapshots")
        validator = EvolutionValidator(tmp_workspace, runner, snap_mgr)

        action = EvolutionAction(
            action_type="modify_code",
            target_files=[tmp_workspace / f"f{i}.py" for i in range(6)],
            backup_snapshot_id=None,
            description="big change",
        )
        result = validator.validate_evolution(action)

        assert result.passed is True
        assert result.blast_radius.risk_level == "high"


# ============================================================
# 完整流程测试
# ============================================================

class TestFullFlow:
    @patch("superclaw.evolution_validator.subprocess.run")
    def test_snapshot_modify_test_fail_rollback(self, mock_run, tmp_workspace):
        """完整流程：创建快照 → 修改文件 → 跑测试（失败）→ 自动回滚 → 验证恢复"""
        mock_run.return_value = fake_completed(
            returncode=0,
            stdout=" 1 file changed, 5 insertions(+)",
        )

        # 1. 创建原文件
        target = tmp_workspace / "module.py"
        target.write_text("def hello():\n    return 'world'\n", encoding="utf-8")
        original_content = target.read_text()

        # 2. 创建快照
        snap_mgr = SnapshotManager(tmp_workspace / "snapshots")
        snap_id = snap_mgr.create_snapshot([target], label="before_evolution")

        # 3. 修改文件（模拟进化）
        target.write_text("def hello():\n    return 'broken'\n", encoding="utf-8")
        assert target.read_text() != original_content

        # 4. 跑测试（mock 失败）
        runner = MagicMock(spec=TestRunner)
        runner.run_unit_tests.return_value = TestResult(
            passed=False,
            total=3,
            passed_count=1,
            failed_count=2,
            error_count=0,
            output="===== 1 passed, 2 failed =====",
            duration_ms=50,
        )

        validator = EvolutionValidator(tmp_workspace, runner, snap_mgr)
        action = EvolutionAction(
            action_type="modify_code",
            target_files=[target],
            backup_snapshot_id=snap_id,
            description="evolution that breaks tests",
        )
        result = validator.validate_evolution(action)

        # 5. 验证回滚
        assert result.passed is False
        assert result.rollback_performed is True
        assert target.read_text() == original_content
        assert result.test_result.failed_count == 2

    @patch("superclaw.evolution_validator.subprocess.run")
    def test_snapshot_modify_test_pass_no_rollback(self, mock_run, tmp_workspace):
        """完整流程：创建快照 → 修改文件 → 跑测试（通过）→ 不回滚"""
        mock_run.return_value = fake_completed(
            returncode=0,
            stdout=" 1 file changed, 5 insertions(+)",
        )

        target = tmp_workspace / "module.py"
        target.write_text("def hello():\n    return 'world'\n", encoding="utf-8")

        snap_mgr = SnapshotManager(tmp_workspace / "snapshots")
        snap_id = snap_mgr.create_snapshot([target], label="before_evolution")

        # 修改文件
        target.write_text("def hello():\n    return 'better'\n", encoding="utf-8")
        modified_content = target.read_text()

        runner = MagicMock(spec=TestRunner)
        runner.run_unit_tests.return_value = TestResult(
            passed=True,
            total=3,
            passed_count=3,
            failed_count=0,
            error_count=0,
            output="===== 3 passed =====",
            duration_ms=50,
        )

        validator = EvolutionValidator(tmp_workspace, runner, snap_mgr)
        action = EvolutionAction(
            action_type="modify_code",
            target_files=[target],
            backup_snapshot_id=snap_id,
            description="evolution that passes tests",
        )
        result = validator.validate_evolution(action)

        # 测试通过 → 不回滚，文件保持修改后状态
        assert result.passed is True
        assert result.rollback_performed is False
        assert target.read_text() == modified_content

    @patch("superclaw.evolution_validator.subprocess.run")
    def test_full_flow_critical_blocked(self, mock_run, tmp_workspace):
        """完整流程：修改核心包 → critical 拒绝，不跑测试"""
        mock_run.return_value = fake_completed(
            returncode=0,
            stdout=" 1 file changed, 5 insertions(+)",
        )

        target = tmp_workspace / "superclaw" / "agent.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("original core code", encoding="utf-8")

        snap_mgr = SnapshotManager(tmp_workspace / "snapshots")
        runner = MagicMock(spec=TestRunner)
        runner.run_unit_tests.return_value = TestResult(
            passed=True, total=1, passed_count=1,
            failed_count=0, error_count=0,
            output="ok", duration_ms=10,
        )

        validator = EvolutionValidator(tmp_workspace, runner, snap_mgr)
        action = EvolutionAction(
            action_type="modify_code",
            target_files=[target],
            backup_snapshot_id=None,
            description="modify core package",
        )
        result = validator.validate_evolution(action)

        assert result.passed is False
        assert result.blast_radius.risk_level == "critical"
        assert result.rollback_performed is False
        runner.run_unit_tests.assert_not_called()
