"""
superclaw 进化调度器 — 定时触发进化循环

设计理念：
当前 GEP engine 的进化循环需要手动调用 run_cycle()，无法在后台持续运行。
本模块引入"调度器"，让 superclaw 能：
- 定时触发进化循环（默认每小时一次）
- 支持多种进化模式（cycle/self_evolution/curious/experience/feedback）
- 后台线程运行，不阻塞主进程
- 优雅启停（start/stop/join）
- 状态持久化（JSON 文件），进程崩溃重启后能恢复 run_count/last_run

不依赖外部库，用 threading.Timer 实现简单的定时调度。

诚实说明：
- threading.Timer 是进程内调度，进程挂了调度就停。
  生产环境需要 systemd/cron 级别的外部调度。
- 状态持久化只记录 run_count/last_run/last_status，不恢复"未完成的进化"
  （进化循环是幂等的，重启后从下一个周期开始即可）。
"""
import json
import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ============================================================
# 进化模式常量
# ============================================================

MODE_CYCLE = "cycle"                    # 基础 10 步进化循环
MODE_SELF_EVOLUTION = "self_evolution"  # 自进化循环（感知短板→获取能力→验证）
MODE_CURIOUS = "curious"                # 好奇心驱动探索
MODE_EXPERIENCE = "experience"          # 经验驱动权重调整
MODE_FEEDBACK = "feedback"              # 反馈驱动进化

VALID_MODES = {
    MODE_CYCLE, MODE_SELF_EVOLUTION, MODE_CURIOUS,
    MODE_EXPERIENCE, MODE_FEEDBACK,
}

# 默认调度间隔（秒）
DEFAULT_INTERVAL = 3600  # 1 小时


# ============================================================
# EvolutionScheduler — 进化调度器
# ============================================================

class EvolutionScheduler:
    """定时调度进化循环

    用法：
        scheduler = EvolutionScheduler(engine, interval=3600, mode="cycle")
        scheduler.start()  # 后台开始定时进化
        # ... 主进程做其他事 ...
        scheduler.stop()   # 停止调度

    线程安全：start/stop 可在任意线程调用，内部用 threading.Lock 保护状态。
    """

    def __init__(self, engine: Any, interval: int = DEFAULT_INTERVAL,
                 mode: str = MODE_CYCLE,
                 state_file: Optional[Path] = None):
        """
        Args:
            engine: GEPEngine 实例
            interval: 调度间隔（秒），默认 3600（1 小时）
            mode: 进化模式，见 VALID_MODES
            state_file: 状态持久化文件路径，None 表示不持久化
        """
        if mode not in VALID_MODES:
            raise ValueError(f"无效模式 {mode}，可选: {VALID_MODES}")
        if interval < 1:
            raise ValueError("interval 必须 >= 1 秒")

        self.engine = engine
        self.interval = interval
        self.mode = mode
        self.state_file = Path(state_file) if state_file else None

        self._timer: Optional[threading.Timer] = None
        self._lock = threading.Lock()
        self._running = False
        self._results: List[Dict[str, Any]] = []
        self._run_count = 0
        self._last_run: Optional[str] = None  # ISO 时间戳
        # 防抖：上一周期是否仍在执行中
        self._executing = False
        # 回调钩子：MultiModeScheduler 用它实现多模式轮转
        self._callback_override: Optional[Any] = None

        # 从持久化状态恢复 run_count/last_run
        if self.state_file is not None:
            self._load_state()

    def start(self) -> bool:
        """启动调度器（后台定时触发）

        Returns: True 如果成功启动，False 如果已在运行
        """
        with self._lock:
            if self._running:
                return False
            self._running = True
            self._schedule_next()
            return True

    def stop(self) -> bool:
        """停止调度器

        Returns: True 如果成功停止，False 如果未在运行
        """
        with self._lock:
            if not self._running:
                return False
            self._running = False
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            return True

    def is_running(self) -> bool:
        """是否正在运行"""
        with self._lock:
            return self._running

    def run_once(self) -> Dict[str, Any]:
        """手动触发一次进化（不影响定时调度）

        Returns: 进化结果字典
        """
        return self._execute_evolution()

    def get_results(self, limit: int = 10) -> List[Dict[str, Any]]:
        """获取最近的进化结果"""
        with self._lock:
            return list(self._results[-limit:])

    def stats(self) -> Dict[str, Any]:
        """获取调度器统计信息"""
        with self._lock:
            return {
                "running": self._running,
                "mode": self.mode,
                "interval": self.interval,
                "run_count": self._run_count,
                "results_count": len(self._results),
                "last_run": self._last_run,
            }

    def join(self, timeout: Optional[float] = None) -> bool:
        """等待调度器停止（阻塞调用线程）

        Args:
            timeout: 超时秒数，None 表示无限等待

        Returns: True 如果调度器已停止，False 如果超时
        """
        deadline = None
        if timeout is not None:
            deadline = time.time() + timeout

        while True:
            with self._lock:
                if not self._running:
                    return True
            if deadline is not None and time.time() >= deadline:
                return False
            time.sleep(0.1)

    # ---- 状态持久化 ----

    def _load_state(self) -> None:
        """从 state_file 恢复 run_count/last_run（启动时调用）"""
        if self.state_file is None or not self.state_file.exists():
            return
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
            self._run_count = int(data.get("run_count", 0))
            self._last_run = data.get("last_run")
            logger.info("调度器状态恢复: run_count=%d last_run=%s",
                        self._run_count, self._last_run)
        except (json.JSONDecodeError, OSError, ValueError) as e:
            logger.warning("调度器状态恢复失败: %s", e)

    def _save_state(self) -> None:
        """保存 run_count/last_run 到 state_file（每次进化后调用）"""
        if self.state_file is None:
            return
        try:
            data = {
                "run_count": self._run_count,
                "last_run": self._last_run,
                "mode": self.mode,
                "interval": self.interval,
                "saved_at": datetime.now().isoformat(),
            }
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            self.state_file.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as e:
            logger.warning("调度器状态保存失败: %s", e)

    # ---- 内部方法 ----

    def _schedule_next(self) -> None:
        """调度下一次执行（必须在 _lock 内调用）"""
        if not self._running:
            return
        self._timer = threading.Timer(
            self.interval, self._timer_callback,
        )
        self._timer.daemon = True
        self._timer.start()

    def _timer_callback(self) -> None:
        """Timer 回调（后台线程执行）"""
        with self._lock:
            if not self._running:
                return
            # 防抖：上一周期尚未完成，跳过本周期
            if self._executing:
                logger.warning("[Scheduler] 上一进化周期未完成，跳过本周期（防抖）")
                # 调度下一次（如果还在运行）
                if self._running:
                    self._schedule_next()
                return
            self._executing = True

        # 如果有回调覆盖（MultiModeScheduler 用），走覆盖逻辑
        if self._callback_override is not None:
            self._callback_override()
            self._executing = False
            return

        # 执行进化（不在锁内，避免长时间持锁）
        try:
            result = self._execute_evolution()
            with self._lock:
                self._results.append({
                    "timestamp": datetime.now().isoformat(),
                    "result": result,
                })
                # 只保留最近 100 条结果
                if len(self._results) > 100:
                    self._results = self._results[-100:]
        except Exception as e:
            with self._lock:
                self._results.append({
                    "timestamp": datetime.now().isoformat(),
                    "error": str(e),
                })
        finally:
            self._executing = False
            # 调度下一次（如果还在运行）
            with self._lock:
                if self._running:
                    self._schedule_next()

    def _execute_evolution(self) -> Dict[str, Any]:
        """执行一次进化（根据 mode 调用不同方法）"""
        self._run_count += 1
        self._last_run = datetime.now().isoformat()

        if self.mode == MODE_CYCLE:
            result = self.engine.run_cycle()
            self._save_state()
            return result

        if self.mode == MODE_SELF_EVOLUTION:
            if hasattr(self.engine, "run_self_evolution_cycle"):
                result = self.engine.run_self_evolution_cycle()
                self._save_state()
                return result
            return {"status": "skipped", "reason": "self_evolution not available"}

        if self.mode == MODE_CURIOUS:
            if hasattr(self.engine, "run_curious_exploration"):
                result = self.engine.run_curious_exploration()
                self._save_state()
                return result
            return {"status": "skipped", "reason": "curious_exploration not available"}

        if self.mode == MODE_EXPERIENCE:
            if hasattr(self.engine, "run_experience_driven_adjustment"):
                result = self.engine.run_experience_driven_adjustment()
                self._save_state()
                return result
            return {"status": "skipped", "reason": "experience_adjustment not available"}

        if self.mode == MODE_FEEDBACK:
            if hasattr(self.engine, "run_feedback_driven_evolution"):
                result = self.engine.run_feedback_driven_evolution()
                self._save_state()
                return result
            return {"status": "skipped", "reason": "feedback_driven not available"}

        return {"status": "unknown_mode", "mode": self.mode}


# ============================================================
# MultiModeScheduler — 多模式轮转调度器
# ============================================================

class MultiModeScheduler:
    """多模式轮转调度器

    按顺序循环执行多种进化模式，例如：
    [cycle, self_evolution, curious, experience, feedback] → cycle → self_evolution → ...

    用法：
        scheduler = MultiModeScheduler(engine, interval=1800,
                                       modes=["cycle", "curious", "feedback"])
        scheduler.start()
    """

    def __init__(self, engine: Any, interval: int = DEFAULT_INTERVAL,
                 modes: Optional[List[str]] = None):
        if modes is None:
            modes = [MODE_CYCLE, MODE_CURIOUS, MODE_FEEDBACK]

        for m in modes:
            if m not in VALID_MODES:
                raise ValueError(f"无效模式 {m}，可选: {VALID_MODES}")

        self.engine = engine
        self.interval = interval
        self.modes = modes
        self._mode_index = 0

        self._scheduler = EvolutionScheduler(engine, interval, modes[0])
        # 用回调钩子实现多模式轮转（不替换方法，避免 mypy method-assign 错误）
        self._scheduler._callback_override = self._multi_mode_callback

    def _multi_mode_callback(self) -> None:
        """多模式轮转回调"""
        current_mode = self.modes[self._mode_index]
        self._scheduler.mode = current_mode

        try:
            result = self._scheduler._execute_evolution()
            with self._scheduler._lock:
                self._scheduler._results.append({
                    "timestamp": datetime.now().isoformat(),
                    "mode": current_mode,
                    "result": result,
                })
                if len(self._scheduler._results) > 100:
                    self._scheduler._results = self._scheduler._results[-100:]
        except Exception as e:
            with self._scheduler._lock:
                self._scheduler._results.append({
                    "timestamp": datetime.now().isoformat(),
                    "mode": current_mode,
                    "error": str(e),
                })
        finally:
            # 切换到下一个模式
            self._mode_index = (self._mode_index + 1) % len(self.modes)
            with self._scheduler._lock:
                if self._scheduler._running:
                    self._scheduler._schedule_next()

    def start(self) -> bool:
        return self._scheduler.start()

    def stop(self) -> bool:
        return self._scheduler.stop()

    def is_running(self) -> bool:
        return self._scheduler.is_running()

    def run_once(self) -> Dict[str, Any]:
        """手动触发一次（用当前轮转到的模式）"""
        current_mode = self.modes[self._mode_index]
        self._scheduler.mode = current_mode
        result = self._scheduler.run_once()
        self._mode_index = (self._mode_index + 1) % len(self.modes)
        return result

    def get_results(self, limit: int = 10) -> List[Dict[str, Any]]:
        return self._scheduler.get_results(limit)

    def stats(self) -> Dict[str, Any]:
        s = self._scheduler.stats()
        s["modes"] = self.modes
        s["current_mode_index"] = self._mode_index
        s["current_mode"] = self.modes[self._mode_index]
        return s

    def join(self, timeout: Optional[float] = None) -> bool:
        return self._scheduler.join(timeout)
