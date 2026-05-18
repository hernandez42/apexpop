#!/usr/bin/env python3
"""
热拔插管理器 — 不停机更新模块

核心能力：
  1. 自动备份旧版本（支持多版本回滚）
  2. 安全替换模块文件
  3. 失败自动回滚
  4. 验证替换后模块可加载

设计原则：
  - 原子操作：备份 → 写入 → 验证，任何一步失败都回滚
  - 版本管理：保留最近 N 个版本的备份
  - 无锁设计：利用文件系统原子性（write-to-temp + rename）
"""

import json
import os
import shutil
import sys
import time
import hashlib
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field

# === 路径配置 ===
WORKSPACE = Path("/home/.openclaw/workspace")
CORE_DIR = WORKSPACE / "core-dna"
MEMORY_DIR = WORKSPACE / "memory"
BACKUP_DIR = MEMORY_DIR / "code-backups"
SWAP_LOG = MEMORY_DIR / "hot-swap.log"

# === 配置 ===
MAX_BACKUPS = 10          # 每个模块最多保留 10 个备份
VERIFY_TIMEOUT = 10       # 模块验证超时（秒）


def log(msg: str, level: str = "INFO"):
    """记录日志"""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] [{level}] [HotSwap] {msg}"
    print(line, flush=True)
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    with open(SWAP_LOG, "a") as f:
        f.write(line + "\n")


@dataclass
class BackupInfo:
    """备份信息"""
    module_name: str        # 模块名
    backup_path: str        # 备份路径
    original_size: int      # 原始大小
    original_hash: str      # 原始文件哈希
    created_at: float       # 创建时间
    reason: str             # 备份原因


@dataclass
class SwapResult:
    """热拔插结果"""
    success: bool           # 是否成功
    module_name: str        # 模块名
    action: str             # 操作类型 (swap/rollback/cleanup)
    backup_path: Optional[str] = None
    error: Optional[str] = None
    elapsed: float = 0.0    # 耗时（秒）


class HotSwapManager:
    """
    热拔插管理器
    
    流程：
    1. 备份当前版本 → 2. 原子写入新版本 → 3. 验证可加载 → 4. 清理旧备份
    
    回滚流程：
    1. 找到最近的备份 → 2. 恢复文件 → 3. 验证可加载
    """
    
    def __init__(self):
        self.backup_index: Dict[str, List[BackupInfo]] = {}
        self._load_backup_index()
    
    def _load_backup_index(self):
        """加载备份索引"""
        index_file = BACKUP_DIR / "index.json"
        if index_file.exists():
            try:
                with open(index_file) as f:
                    data = json.load(f)
                for module_name, backups in data.items():
                    self.backup_index[module_name] = [
                        BackupInfo(**b) for b in backups
                    ]
            except Exception as e:
                log(f"加载备份索引失败: {e}", "WARN")
    
    def _save_backup_index(self):
        """保存备份索引"""
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        index_file = BACKUP_DIR / "index.json"
        data = {}
        for module_name, backups in self.backup_index.items():
            data[module_name] = [
                {
                    "module_name": b.module_name,
                    "backup_path": b.backup_path,
                    "original_size": b.original_size,
                    "original_hash": b.original_hash,
                    "created_at": b.created_at,
                    "reason": b.reason,
                }
                for b in backups
            ]
        with open(index_file, "w") as f:
            json.dump(data, f, indent=2)
    
    def _file_hash(self, filepath: Path) -> str:
        """计算文件 MD5 哈希"""
        h = hashlib.md5()
        with open(filepath, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()
    
    def _verify_module(self, filepath: Path) -> bool:
        """
        验证模块可加载
        
        检查：
        1. 文件存在且非空
        2. Python 语法正确
        3. 可以 compile 通过
        """
        if not filepath.exists():
            log(f"  验证失败: 文件不存在 {filepath}", "WARN")
            return False
        
        if filepath.stat().st_size == 0:
            log(f"  验证失败: 文件为空 {filepath}", "WARN")
            return False
        
        if filepath.suffix == ".py":
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    content = f.read()
                compile(content, str(filepath), "exec")
                log(f"  验证通过: {filepath.name} 语法正确")
                return True
            except SyntaxError as e:
                log(f"  验证失败: {filepath.name} 语法错误 - {e.msg} (行 {e.lineno})", "WARN")
                return False
        
        # 非 Python 文件只检查存在性
        return True
    
    def backup(self, module_name: str, reason: str = "pre-swap") -> Optional[BackupInfo]:
        """
        备份模块
        
        Args:
            module_name: 模块文件名（如 "self-evolve.py"）
            reason: 备份原因
        
        Returns:
            备份信息，失败返回 None
        """
        start_time = time.time()
        
        module_path = CORE_DIR / module_name
        if not module_path.exists():
            log(f"模块不存在，跳过备份: {module_name}", "WARN")
            return None
        
        # 创建备份目录
        module_backup_dir = BACKUP_DIR / module_name.replace(".", "_")
        module_backup_dir.mkdir(parents=True, exist_ok=True)
        
        # 生成备份文件名
        timestamp = int(time.time())
        backup_name = f"{module_path.stem}_{timestamp}{module_path.suffix}"
        backup_path = module_backup_dir / backup_name
        
        # 执行备份
        try:
            shutil.copy2(module_path, backup_path)
            
            # 计算哈希
            file_hash = self._file_hash(module_path)
            
            # 创建备份信息
            info = BackupInfo(
                module_name=module_name,
                backup_path=str(backup_path),
                original_size=module_path.stat().st_size,
                original_hash=file_hash,
                created_at=time.time(),
                reason=reason,
            )
            
            # 更新索引
            if module_name not in self.backup_index:
                self.backup_index[module_name] = []
            self.backup_index[module_name].append(info)
            
            # 清理旧备份
            self._cleanup_old_backups(module_name)
            
            # 保存索引
            self._save_backup_index()
            
            elapsed = time.time() - start_time
            log(f"  备份完成: {module_name} → {backup_name} "
                f"({info.original_size} bytes, {elapsed:.1f}s)")
            
            return info
            
        except Exception as e:
            log(f"  备份失败: {module_name} - {e}", "ERROR")
            return None
    
    def _cleanup_old_backups(self, module_name: str):
        """清理旧备份，只保留最近 MAX_BACKUPS 个"""
        if module_name not in self.backup_index:
            return
        
        backups = self.backup_index[module_name]
        if len(backups) <= MAX_BACKUPS:
            return
        
        # 按时间排序，保留最新的
        backups.sort(key=lambda b: b.created_at, reverse=True)
        to_remove = backups[MAX_BACKUPS:]
        
        for b in to_remove:
            try:
                Path(b.backup_path).unlink(missing_ok=True)
                log(f"  清理旧备份: {Path(b.backup_path).name}")
            except Exception:
                pass
        
        self.backup_index[module_name] = backups[:MAX_BACKUPS]
    
    def swap(self, module_name: str, new_content: str,
             reason: str = "evolution") -> SwapResult:
        """
        热拔插：原子替换模块
        
        流程：
        1. 备份当前版本
        2. 写入临时文件
        3. 验证临时文件
        4. 原子 rename 替换
        5. 验证最终文件
        
        Args:
            module_name: 模块文件名
            new_content: 新的代码内容
            reason: 替换原因
        
        Returns:
            SwapResult
        """
        start_time = time.time()
        module_path = CORE_DIR / module_name
        
        log(f"🔄 热拔插开始: {module_name}")
        
        # Step 1: 备份
        backup_info = self.backup(module_name, reason)
        
        # Step 2: 写入临时文件
        try:
            # 在同一目录下创建临时文件（保证 rename 原子性）
            temp_fd, temp_path = tempfile.mkstemp(
                suffix=".py.tmp",
                prefix=f"{module_path.stem}_",
                dir=str(CORE_DIR)
            )
            
            with os.fdopen(temp_fd, "w", encoding="utf-8") as f:
                f.write(new_content)
            
            temp_file = Path(temp_path)
            
            # Step 3: 验证临时文件
            if not self._verify_module(temp_file):
                temp_file.unlink(missing_ok=True)
                return SwapResult(
                    success=False,
                    module_name=module_name,
                    action="swap",
                    error="新模块验证失败",
                    elapsed=time.time() - start_time,
                )
            
            # Step 4: 原子替换
            # 如果目标不存在，直接 rename
            # 如果目标存在，先 rename 到 .bak，再 rename 新文件
            backup_for_swap = module_path.with_suffix(module_path.suffix + ".bak")
            
            if module_path.exists():
                # 临时备份当前文件（防止 rename 失败时丢失）
                shutil.copy2(module_path, backup_for_swap)
            
            try:
                # 原子替换
                os.replace(temp_path, module_path)
            except Exception as e:
                # 替换失败，从 .bak 恢复
                if backup_for_swap.exists():
                    shutil.move(str(backup_for_swap), str(module_path))
                raise
            
            # 清理临时 .bak
            backup_for_swap.unlink(missing_ok=True)
            
            # Step 5: 验证最终文件
            if not self._verify_module(module_path):
                # 最终验证失败，回滚
                log(f"  最终验证失败，回滚...", "WARN")
                self.rollback(module_name)
                return SwapResult(
                    success=False,
                    module_name=module_name,
                    action="swap",
                    error="最终验证失败，已回滚",
                    elapsed=time.time() - start_time,
                )
            
            elapsed = time.time() - start_time
            log(f"  ✅ 热拔插成功: {module_name} ({len(new_content)} bytes, {elapsed:.1f}s)")
            
            return SwapResult(
                success=True,
                module_name=module_name,
                action="swap",
                backup_path=backup_info.backup_path if backup_info else None,
                elapsed=elapsed,
            )
            
        except Exception as e:
            # 任何异常都回滚
            log(f"  热拔插异常: {e}，尝试回滚...", "ERROR")
            try:
                self.rollback(module_name)
                return SwapResult(
                    success=False,
                    module_name=module_name,
                    action="swap+rollback",
                    error=f"交换失败已回滚: {e}",
                    elapsed=time.time() - start_time,
                )
            except Exception as rollback_error:
                return SwapResult(
                    success=False,
                    module_name=module_name,
                    action="swap",
                    error=f"交换失败且回滚也失败: {e} / {rollback_error}",
                    elapsed=time.time() - start_time,
                )
    
    def rollback(self, module_name: str, version: int = -1) -> SwapResult:
        """
        回滚到指定版本
        
        Args:
            module_name: 模块名
            version: 版本索引 (-1 = 最近的备份, -2 = 倒数第二个, ...)
        
        Returns:
            SwapResult
        """
        start_time = time.time()
        
        log(f"⏪ 回滚开始: {module_name} (版本 {version})")
        
        if module_name not in self.backup_index or not self.backup_index[module_name]:
            return SwapResult(
                success=False,
                module_name=module_name,
                action="rollback",
                error="没有可用的备份",
                elapsed=time.time() - start_time,
            )
        
        # 按时间排序
        backups = sorted(self.backup_index[module_name], key=lambda b: b.created_at, reverse=True)
        
        if abs(version) > len(backups):
            return SwapResult(
                success=False,
                module_name=module_name,
                action="rollback",
                error=f"版本索引超出范围 (共 {len(backups)} 个备份)",
                elapsed=time.time() - start_time,
            )
        
        target_backup = backups[version]
        backup_path = Path(target_backup.backup_path)
        
        if not backup_path.exists():
            return SwapResult(
                success=False,
                module_name=module_name,
                action="rollback",
                error=f"备份文件不存在: {backup_path}",
                elapsed=time.time() - start_time,
            )
        
        module_path = CORE_DIR / module_name
        
        try:
            # 验证备份文件
            if not self._verify_module(backup_path):
                return SwapResult(
                    success=False,
                    module_name=module_name,
                    action="rollback",
                    error="备份文件验证失败",
                    elapsed=time.time() - start_time,
                )
            
            # 先备份当前版本（以防万一）
            if module_path.exists():
                self.backup(module_name, "pre-rollback")
            
            # 恢复
            shutil.copy2(backup_path, module_path)
            
            elapsed = time.time() - start_time
            log(f"  ✅ 回滚成功: {module_name} ← {backup_path.name} ({elapsed:.1f}s)")
            
            return SwapResult(
                success=True,
                module_name=module_name,
                action="rollback",
                backup_path=str(backup_path),
                elapsed=elapsed,
            )
            
        except Exception as e:
            return SwapResult(
                success=False,
                module_name=module_name,
                action="rollback",
                error=str(e),
                elapsed=time.time() - start_time,
            )
    
    def list_backups(self, module_name: Optional[str] = None) -> List[Dict]:
        """列出备份"""
        results = []
        
        if module_name:
            modules = [module_name] if module_name in self.backup_index else []
        else:
            modules = list(self.backup_index.keys())
        
        for mod in modules:
            for b in self.backup_index.get(mod, []):
                ts = datetime.fromtimestamp(b.created_at).strftime("%Y-%m-%d %H:%M:%S")
                results.append({
                    "module": mod,
                    "backup": Path(b.backup_path).name,
                    "size": b.original_size,
                    "hash": b.original_hash[:8],
                    "time": ts,
                    "reason": b.reason,
                })
        
        return results
    
    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        total_backups = sum(len(v) for v in self.backup_index.values())
        total_size = 0
        for backups in self.backup_index.values():
            for b in backups:
                if Path(b.backup_path).exists():
                    total_size += Path(b.backup_path).stat().st_size
        
        return {
            "modules_tracked": len(self.backup_index),
            "total_backups": total_backups,
            "total_size_bytes": total_size,
            "total_size_mb": round(total_size / 1024 / 1024, 2),
            "backup_dir": str(BACKUP_DIR),
        }


# =============================================================================
# CLI 入口
# =============================================================================

def main():
    """命令行入口"""
    import argparse
    parser = argparse.ArgumentParser(description="MiMoClaw 热拔插管理器")
    parser.add_argument("command", choices=["swap", "rollback", "list", "stats", "verify"],
                       help="执行命令")
    parser.add_argument("--module", "-m", help="模块名")
    parser.add_argument("--file", "-f", help="新代码文件路径（swap 命令）")
    parser.add_argument("--version", "-v", type=int, default=-1, help="回滚版本索引")
    args = parser.parse_args()
    
    manager = HotSwapManager()
    
    if args.command == "list":
        backups = manager.list_backups(args.module)
        print(f"\n备份列表 ({len(backups)} 个):")
        for b in backups:
            print(f"  [{b['time']}] {b['module']} → {b['backup']} "
                  f"({b['size']} bytes) - {b['reason']}")
    
    elif args.command == "stats":
        stats = manager.get_stats()
        print(json.dumps(stats, ensure_ascii=False, indent=2))
    
    elif args.command == "verify":
        if not args.module:
            print("请指定 --module")
            return
        path = CORE_DIR / args.module
        ok = manager._verify_module(path)
        print(f"{'✅ 验证通过' if ok else '❌ 验证失败'}: {args.module}")
    
    elif args.command == "swap":
        if not args.module or not args.file:
            print("请指定 --module 和 --file")
            return
        new_content = Path(args.file).read_text(encoding="utf-8")
        result = manager.swap(args.module, new_content)
        print(f"\n{'✅ 成功' if result.success else '❌ 失败'}: {result.error or 'OK'}")
    
    elif args.command == "rollback":
        if not args.module:
            print("请指定 --module")
            return
        result = manager.rollback(args.module, args.version)
        print(f"\n{'✅ 成功' if result.success else '❌ 失败'}: {result.error or 'OK'}")


if __name__ == "__main__":
    main()
