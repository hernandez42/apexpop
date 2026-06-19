"""superclaw 核心桥接 — Python 真调 C/Rust 二进制

让"三核架构 C × Rust × Python"名副其实：
- CCoreBridge: 通过 subprocess 真调 core-dna/c-core-pipe（身份/心跳/短板检测/健康）
- RustEngineBridge: 通过 subprocess 真调 core-dna/rust-engine-pipe（基因变异/评估/平衡/遗忘）

设计：
- 持久化进程模式（keep_alive=True）：启动一次，多次 send/recv，避免反复 fork
- 一次性模式（keep_alive=False）：每条命令独立 subprocess（无状态、安全、慢）
- 自动构建：二进制不存在时按 CI 同款命令编译
- 超时与异常隔离：所有调用不抛异常给上层，返回 {"status":"error",...}

不依赖任何第三方库，仅用 stdlib。
"""
from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# 项目根目录（superclaw/ 包的父目录）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_CORE_DNA_DIR = _PROJECT_ROOT / "core-dna"
_C_BINARY = _CORE_DNA_DIR / "c-core-pipe"
_RUST_BINARY = _CORE_DNA_DIR / "rust-engine-pipe"

# 默认超时（秒）
DEFAULT_TIMEOUT = 10


def _ensure_binary(binary: Path, build_cmd: list, desc: str) -> bool:
    """确保二进制存在，不存在则按 build_cmd 编译。

    Returns:
        True 如果二进制可用（已存在或编译成功），False 否则
    """
    if binary.exists():
        return True
    try:
        result = subprocess.run(
            build_cmd,
            cwd=str(_CORE_DNA_DIR),
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            logger.warning("%s 编译失败 (exit=%s): %s",
                           desc, result.returncode, result.stderr.strip())
            return False
        return binary.exists()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        logger.warning("%s 编译异常: %s", desc, e)
        return False


def ensure_c_core() -> bool:
    """确保 C 核心二进制存在（不存在则编译）"""
    return _ensure_binary(
        _C_BINARY,
        ["gcc", "-O2", "-Wall", "-Wextra", "-std=c11",
         "-D_POSIX_C_SOURCE=200809L", "-o", "c-core-pipe",
         "main_pipe.c", "-lm"],
        "C Core",
    )


def ensure_rust_engine() -> bool:
    """确保 Rust 引擎二进制存在（不存在则 cargo build）"""
    if _RUST_BINARY.exists():
        return True
    try:
        result = subprocess.run(
            ["cargo", "build", "--release"],
            cwd=str(_CORE_DNA_DIR / "rust_pipe"),
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.returncode != 0:
            logger.warning("Rust Engine 编译失败 (exit=%s): %s",
                           result.returncode, result.stderr.strip())
            return False
        # cargo 产物在 target/release/，复制到 core-dna/ 根
        cargo_out = _CORE_DNA_DIR / "rust_pipe" / "target" / "release" / "rust-engine-pipe"
        if cargo_out.exists():
            import shutil
            shutil.copy2(str(cargo_out), str(_RUST_BINARY))
        return _RUST_BINARY.exists()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        logger.warning("Rust Engine 编译异常: %s", e)
        return False


class _PipeBridge:
    """管道桥接基类 — 管理 subprocess 持久化进程或一次性调用"""

    def __init__(self, binary: Path, ensure_fn, name: str,
                 keep_alive: bool = True, timeout: int = DEFAULT_TIMEOUT):
        self.binary = binary
        self.ensure_fn = ensure_fn
        self.name = name
        self.keep_alive = keep_alive
        self.timeout = timeout
        self._proc: Optional[subprocess.Popen] = None
        self._available: Optional[bool] = None  # 缓存可用性检查

    def _is_available(self) -> bool:
        """二进制是否可用（存在或可编译）"""
        if self._available is None:
            self._available = self.ensure_fn()
        return self._available

    def _start_proc(self) -> bool:
        """启动持久化进程"""
        if not self._is_available():
            return False
        try:
            self._proc = subprocess.Popen(
                [str(self.binary)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=1,
                text=True,
            )
            # 读启动消息（ready 行）
            if self._proc.stdout is not None:
                self._proc.stdout.readline()
            return True
        except (OSError, FileNotFoundError) as e:
            logger.warning("%s 启动失败: %s", self.name, e)
            self._proc = None
            return False

    def _send_one_shot(self, cmd: Dict[str, Any]) -> Dict[str, Any]:
        """一次性模式：每条命令独立 subprocess"""
        if not self._is_available():
            return {"status": "error", "msg": f"{self.name} 二进制不可用"}
        try:
            result = subprocess.run(
                [str(self.binary)],
                input=json.dumps(cmd) + "\n",
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
            # 输出有多行（ready + 响应 + exited），取响应行
            lines = [ln for ln in result.stdout.splitlines() if ln.strip()]
            for ln in lines:
                try:
                    parsed = json.loads(ln)
                    if parsed.get("status") not in ("ready", "exited"):
                        return parsed
                except json.JSONDecodeError:
                    continue
            return {"status": "error", "msg": f"{self.name} 无有效响应"}
        except subprocess.TimeoutExpired:
            return {"status": "error", "msg": f"{self.name} 超时"}
        except (OSError, FileNotFoundError) as e:
            return {"status": "error", "msg": f"{self.name} 调用异常: {e}"}

    def _send_keep_alive(self, cmd: Dict[str, Any]) -> Dict[str, Any]:
        """持久化模式：复用进程"""
        if self._proc is None or self._proc.poll() is not None:
            if not self._start_proc():
                return {"status": "error", "msg": f"{self.name} 进程不可用"}
        assert self._proc is not None
        assert self._proc.stdin is not None
        assert self._proc.stdout is not None
        try:
            self._proc.stdin.write(json.dumps(cmd) + "\n")
            self._proc.stdin.flush()
            line = self._proc.stdout.readline()
            if not line:
                return {"status": "error", "msg": f"{self.name} 进程已退出"}
            return json.loads(line)
        except (BrokenPipeError, OSError) as e:
            self._proc = None
            return {"status": "error", "msg": f"{self.name} 管道断开: {e}"}
        except json.JSONDecodeError as e:
            return {"status": "error", "msg": f"{self.name} 响应解析失败: {e}"}

    def send(self, cmd: Dict[str, Any]) -> Dict[str, Any]:
        """发送命令，返回响应 dict。出错返回 {"status":"error",...}"""
        if self.keep_alive:
            return self._send_keep_alive(cmd)
        return self._send_one_shot(cmd)

    def close(self) -> None:
        """关闭持久化进程"""
        if self._proc is not None and self._proc.poll() is None:
            try:
                if self._proc.stdin is not None:
                    self._proc.stdin.close()
                self._proc.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                self._proc.kill()
        self._proc = None

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass


class CCoreBridge(_PipeBridge):
    """C 核心桥接 — 身份锚定 / 心跳 / 短板检测 / 健康检查

    协议命令：
        heartbeat, status, detect_weakness, health_check, record_evolution
    """

    def __init__(self, keep_alive: bool = True, timeout: int = DEFAULT_TIMEOUT):
        super().__init__(_C_BINARY, ensure_c_core, "C Core",
                         keep_alive=keep_alive, timeout=timeout)

    def heartbeat(self) -> Dict[str, Any]:
        """心跳 — 推进 cycle、更新 fitness/balance"""
        return self.send({"cmd": "heartbeat"})

    def status(self) -> Dict[str, Any]:
        """完整状态查询"""
        return self.send({"cmd": "status"})

    def detect_weakness(self) -> Dict[str, Any]:
        """短板检测 — 返回当前弱点列表"""
        return self.send({"cmd": "detect_weakness"})

    def health_check(self) -> Dict[str, Any]:
        """健康检查 + 自动修复"""
        return self.send({"cmd": "health_check"})

    def record_evolution(self, mutations: int = 0, knowledge: int = 0,
                         fitness: Optional[float] = None) -> Dict[str, Any]:
        """记录一次进化结果"""
        cmd: Dict[str, Any] = {
            "cmd": "record_evolution",
            "mutations": int(mutations),
            "knowledge": int(knowledge),
        }
        if fitness is not None:
            cmd["fitness"] = float(fitness)
        return self.send(cmd)


class RustEngineBridge(_PipeBridge):
    """Rust 引擎桥接 — 基因变异 / 评估 / 保留 / 遗忘 / 平衡

    协议命令：
        mutate, evaluate, retain, forget, balance, status, retention_check
    """

    def __init__(self, keep_alive: bool = True, timeout: int = DEFAULT_TIMEOUT):
        super().__init__(_RUST_BINARY, ensure_rust_engine, "Rust Engine",
                         keep_alive=keep_alive, timeout=timeout)

    def mutate(self, domain: str, change: float = 0.1) -> Dict[str, Any]:
        """基因变异 — 在指定 domain 增加一个基因"""
        return self.send({"cmd": "mutate", "domain": domain,
                          "change": float(change)})

    def evaluate(self, gene_id: str) -> Dict[str, Any]:
        """评估指定基因的得分"""
        return self.send({"cmd": "evaluate", "gene_id": gene_id})

    def retain(self, gene_id: str) -> Dict[str, Any]:
        """保留基因（增加 use_count）"""
        return self.send({"cmd": "retain", "gene_id": gene_id})

    def forget(self, threshold: float = 0.1) -> Dict[str, Any]:
        """遗忘弱基因（按阈值清理）"""
        return self.send({"cmd": "forget", "threshold": float(threshold)})

    def balance(self) -> Dict[str, Any]:
        """查询当前平衡状态（按 domain 聚合）"""
        return self.send({"cmd": "balance"})

    def status(self) -> Dict[str, Any]:
        """完整状态查询"""
        return self.send({"cmd": "status"})

    def retention_check(self) -> Dict[str, Any]:
        """保留检查 — 统计强/弱基因数"""
        return self.send({"cmd": "retention_check"})


# ============================================================
# 便捷单例（懒加载）— 适合 CLI / Agent 复用
# ============================================================

_c_bridge: Optional[CCoreBridge] = None
_rust_bridge: Optional[RustEngineBridge] = None


def get_c_core(keep_alive: bool = True) -> CCoreBridge:
    """获取 C 核心桥接单例"""
    global _c_bridge
    if _c_bridge is None or _c_bridge.keep_alive != keep_alive:
        if _c_bridge is not None:
            _c_bridge.close()
        _c_bridge = CCoreBridge(keep_alive=keep_alive)
    return _c_bridge


def get_rust_engine(keep_alive: bool = True) -> RustEngineBridge:
    """获取 Rust 引擎桥接单例"""
    global _rust_bridge
    if _rust_bridge is None or _rust_bridge.keep_alive != keep_alive:
        if _rust_bridge is not None:
            _rust_bridge.close()
        _rust_bridge = RustEngineBridge(keep_alive=keep_alive)
    return _rust_bridge


def shutdown_bridges() -> None:
    """关闭所有桥接单例（进程退出前调用）"""
    global _c_bridge, _rust_bridge
    if _c_bridge is not None:
        _c_bridge.close()
        _c_bridge = None
    if _rust_bridge is not None:
        _rust_bridge.close()
        _rust_bridge = None
