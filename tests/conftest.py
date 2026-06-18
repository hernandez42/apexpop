"""pytest 全局 fixtures — superclaw 测试基础设施"""
import os
import sys
from pathlib import Path

import pytest

# 把项目根目录（/workspace/superclaw）插入 sys.path，让 superclaw 包可导入
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


@pytest.fixture()
def tmp_workspace(tmp_path, monkeypatch):
    """创建临时工作目录，chdir 进去，yield，最后恢复原 cwd。

    用于隔离文件系统副作用（GeneLibrary / MemoryStore / EvolutionHistory 等）。
    """
    original_cwd = os.getcwd()
    monkeypatch.chdir(tmp_path)
    yield tmp_path
    # 恢复原 cwd（monkeypatch 会自动恢复 chdir，这里显式恢复以兼容老版本）
    os.chdir(original_cwd)


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "integration: requires real API credentials, skipped by default",
    )
