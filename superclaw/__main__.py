#!/usr/bin/env python3
"""支持 `python3 -m superclaw` 入口"""
import sys
from pathlib import Path

# 确保 repo 根目录在 sys.path（支持 `python3 -m superclaw` 从任意目录运行）
_repo_root = Path(__file__).parent.parent.resolve()
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

# 复用 cli.py 的入口逻辑（避免重复代码）
from cli import main

if __name__ == "__main__":
    main()
