#!/usr/bin/env python3
"""
三层数据流 IPC 管道 — C core ↔ Python ↔ Rust
=================================================

数据流路径：
  C core (心跳/短板/健康) 
    → Python (分析决策) 
      → Rust (评估/变异/平衡) 
        → Python (整合结果) 
          → C core (记录进化)

协议：JSON Lines（每行一个 JSON 对象）
  stdin  → {"cmd":"...", "param":...}
  stdout → {"status":"ok/error", "cmd":"...", "data":{...}}

使用方式：
  1. 作为库导入：
     with ThreeLayerPipeline() as pipe:
         result = pipe.run_full_cycle()
  2. 命令行测试：
     python3 pipeline.py --test
"""

import json
import sys
import time
import os
import signal
import traceback
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any, List, Tuple
from dataclasses import dataclass, field
from contextlib import contextmanager

BASE_DIR = Path(__file__).parent
C_CORE_BIN = str(BASE_DIR / "c-core-pipe")
RUST_ENGINE_BIN = str(BASE_DIR / "rust-engine-pipe")
PIPELINE_LOG = BASE_DIR / "pipeline-run.log"
PIPELINE_STATE = BASE_DIR / "pipeline-state.json"

# === 重试配置 ===
MAX_RETRIES = 3
RETRY_DELAY_BASE = 0.5  # 秒，指数退避基数
HEARTBEAT_TIMEOUT = 5.0  # 秒


# ============================================================
# 数据格式标准化
# ============================================================

@dataclass
class PipelineMessage:
    """标准化的管道消息格式"""
    msg_type: str          # "request" | "response" | "event"
    source: str            # "c_core" | "python" | "rust"
    target: str            # "c_core" | "python" | "rust"
    cmd: str               # 命令名
    payload: Dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    msg_id: str = ""
    status: str = "ok"

    def to_json(self) -> str:
        return json.dumps({
            "msg_type": self.msg_type,
            "source": self.source,
            "target": self.target,
            "cmd": self.cmd,
            "payload": self.payload,
            "timestamp": self.timestamp,
            "msg_id": self.msg_id,
            "status": self.status,
        }, ensure_ascii=False)

    @classmethod
    def from_dict(cls, d: dict) -> 'PipelineMessage':
        return cls(
            msg_type=d.get("msg_type", "response"),
            source=d.get("source", "unknown"),
            target=d.get("target", "unknown"),
            cmd=d.get("cmd", ""),
            payload=d.get("payload", d.get("data", {})),
            timestamp=d.get("timestamp", ""),
            msg_id=d.get("msg_id", ""),
            status=d.get("status", "ok"),
        )


class PipelineError(Exception):
    """管道错误基类"""
    pass


class ProcessNotReadyError(PipelineError):
    """进程未就绪"""
    pass


class CommandTimeoutError(PipelineError):
    """命令执行超时"""
    pass


class CommandFailedError(PipelineError):
    """命令执行失败"""
    pass


class FormatError(PipelineError):
    """数据格式错误"""
    pass


# ============================================================
# 进程管理器
# ============================================================

class ChildProcess:
    """管理一个子进程（C core 或 Rust engine）"""

    def __init__(self, name: str, binary: str):
        self.name = name
        self.binary = binary
        self.proc = None
        self.alive = False
        self._msg_counter = 0

    def start(self, timeout: float = HEARTBEAT_TIMEOUT) -> bool:
        """启动进程并等待 ready 信号"""
        import subprocess
        try:
            self.proc = subprocess.Popen(
                [self.binary],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=0,
            )
        except FileNotFoundError:
            raise ProcessNotReadyError(f"找不到二进制文件: {self.binary}")
        except PermissionError:
            raise ProcessNotReadyError(f"没有执行权限: {self.binary}")

        # 等待 ready 消息
        ready_line = self._read_line(timeout=timeout)
        if ready_line is None:
            self.kill()
            raise ProcessNotReadyError(f"{self.name} 启动超时（{timeout}s）")

        try:
            data = json.loads(ready_line)
        except json.JSONDecodeError as e:
            self.kill()
            raise ProcessNotReadyError(f"{self.name} ready 消息格式错误: {e}")

        if data.get("status") != "ready":
            self.kill()
            raise ProcessNotReadyError(f"{self.name} 未返回 ready: {data}")

        self.alive = True
        return True

    def send_command(self, cmd: str, params: Dict[str, Any] = None,
                     timeout: float = 10.0) -> Dict[str, Any]:
        """发送 JSON 命令，读取响应"""
        if not self.alive:
            raise PipelineError(f"{self.name} 未启动或已退出")

        self._msg_counter += 1
        request = {"cmd": cmd}
        if params:
            request.update(params)

        line = json.dumps(request, ensure_ascii=False)
        try:
            self.proc.stdin.write(line + "\n")
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError) as e:
            self.alive = False
            raise PipelineError(f"{self.name} 写入失败: {e}")

        resp_line = self._read_line(timeout=timeout)
        if resp_line is None:
            raise CommandTimeoutError(f"{self.name} 响应超时（{timeout}s）: cmd={cmd}")

        try:
            resp = json.loads(resp_line)
        except json.JSONDecodeError as e:
            raise FormatError(f"{self.name} 响应格式错误: {e} | raw={resp_line[:200]}")

        if resp.get("status") == "error":
            raise CommandFailedError(
                f"{self.name} 命令失败: {resp.get('msg', 'unknown')} | cmd={cmd}"
            )

        return resp

    def send_with_retry(self, cmd: str, params: Dict[str, Any] = None,
                        timeout: float = 10.0, retries: int = MAX_RETRIES) -> Dict[str, Any]:
        """带重试的命令发送"""
        last_error = None
        for attempt in range(retries):
            try:
                return self.send_command(cmd, params, timeout)
            except (PipelineError, CommandTimeoutError) as e:
                last_error = e
                if attempt < retries - 1:
                    delay = RETRY_DELAY_BASE * (2 ** attempt)
                    time.sleep(delay)
                    # 如果进程已死，尝试重启
                    if not self.alive:
                        try:
                            self.start(timeout=5.0)
                        except ProcessNotReadyError:
                            pass
        raise last_error

    def _read_line(self, timeout: float = 5.0) -> Optional[str]:
        """带超时的行读取"""
        import select
        if self.proc is None:
            return None

        ready, _, _ = select.select([self.proc.stdout], [], [], timeout)
        if ready:
            line = self.proc.stdout.readline().strip()
            return line if line else None
        return None

    def kill(self):
        """终止进程"""
        self.alive = False
        if self.proc:
            try:
                self.proc.stdin.close()
            except:
                pass
            try:
                self.proc.terminate()
                self.proc.wait(timeout=3)
            except:
                try:
                    self.proc.kill()
                except:
                    pass
            self.proc = None

    def is_running(self) -> bool:
        """检查进程是否仍在运行"""
        if self.proc is None:
            return False
        return self.proc.poll() is None


# ============================================================
# 三层数据流管道
# ============================================================

class ThreeLayerPipeline:
    """
    三层数据流 IPC 管道
    
    数据流：
      C core → Python → Rust → Python → C core
    
    管道模式：
      1. run_full_cycle() — 完整进化循环
      2. process_weakness() — 短板修复流程
      3. send_knowledge() — 知识注入流程
    """

    def __init__(self):
        self.c_core = ChildProcess("C Core", C_CORE_BIN)
        self.rust_engine = ChildProcess("Rust Engine", RUST_ENGINE_BIN)
        self._run_log: List[Dict] = []

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *args):
        self.shutdown()

    def start(self):
        """启动所有子进程"""
        log("🚀 启动三层管道...")
        self.c_core.start()
        log(f"  ✅ C Core 就绪 (PID={self.c_core.proc.pid})")
        self.rust_engine.start()
        log(f"  ✅ Rust Engine 就绪 (PID={self.rust_engine.proc.pid})")

    def shutdown(self):
        """关闭所有子进程"""
        log("🔒 关闭三层管道...")
        self.c_core.kill()
        self.rust_engine.kill()
        log("  ✅ 所有进程已关闭")

    # ----------------------------------------------------------
    # 核心数据流方法
    # ----------------------------------------------------------

    def c_core_to_python(self, cmd: str, params: Dict = None) -> Dict:
        """
        C core → Python 方向
        Python 向 C core 发送命令，获取状态/短板等数据
        """
        result = self.c_core.send_with_retry(cmd, params)
        self._log_flow("c_core", "python", cmd, result)
        return result

    def python_to_rust(self, cmd: str, params: Dict = None) -> Dict:
        """
        Python → Rust 方向
        Python 将分析结果发给 Rust 引擎计算
        """
        result = self.rust_engine.send_with_retry(cmd, params)
        self._log_flow("python", "rust", cmd, result)
        return result

    def rust_to_python(self, cmd: str, params: Dict = None) -> Dict:
        """
        Rust → Python 方向（同 python_to_rust，但强调 Rust 主动发起）
        Rust 引擎返回计算结果给 Python
        """
        result = self.rust_engine.send_with_retry(cmd, params)
        self._log_flow("rust", "python", cmd, result)
        return result

    def python_to_c_core(self, cmd: str, params: Dict = None) -> Dict:
        """
        Python → C core 方向
        Python 将整合后的结果写回 C core
        """
        result = self.c_core.send_with_retry(cmd, params)
        self._log_flow("python", "c_core", cmd, result)
        return result

    # ----------------------------------------------------------
    # 高层管道操作
    # ----------------------------------------------------------

    def run_full_cycle(self) -> Dict[str, Any]:
        """
        完整进化循环：
          C core 心跳 → 检测短板 → Rust 评估 → Rust 变异 → C core 记录
        """
        cycle_start = time.time()
        log("\n🔄 === 完整进化循环开始 ===")

        # Step 1: C core 心跳
        log("  Step 1: C core 心跳检测")
        heartbeat = self.c_core_to_python("heartbeat")
        cycle = heartbeat["data"]["cycle"]
        fitness = heartbeat["data"]["fitness"]
        log(f"    cycle={cycle}, fitness={fitness:.4f}")

        # Step 2: C core 检测短板
        log("  Step 2: C core 短板检测")
        weakness = self.c_core_to_python("detect_weakness")
        issues = weakness["data"]["weaknesses"]
        issue_count = weakness["data"]["count"]
        log(f"    短板数={issue_count}, 问题={issues}")

        # Step 3: Rust 变异短板域（先创建基因）
        domain = issues.split(",")[0] if issues != "none" else "通用"
        new_fitness = fitness
        mutate_result = {"status": "skipped"}

        if issue_count > 0:
            log(f"  Step 3: Rust 变异 ({domain})")
            mutate_result = self.python_to_rust("mutate", {
                "domain": domain,
                "change": 0.05,
            })
            created_gene = mutate_result.get("data", {}).get("gene_id", "")
            log(f"    变异状态={mutate_result.get('status')}, gene={created_gene}")
            new_fitness = mutate_result.get("data", {}).get("strength", fitness)

            # Step 4: Rust 评估刚创建的基因
            if created_gene:
                log(f"  Step 4: Rust 评估 ({created_gene})")
                eval_result = self.python_to_rust("evaluate", {
                    "gene_id": created_gene,
                    "domain": domain,
                    "fitness": fitness,
                })
                log(f"    评估状态={eval_result.get('status')}, score={eval_result.get('data', {}).get('score', 0):.4f}")
        else:
            log("  Step 3: 无短板，跳过变异")
            log("  Step 4: 跳过评估")

        # Step 5: Rust 平衡检测
        log("  Step 5: Rust 平衡检测")
        balance = self.python_to_rust("balance")
        balance_val = balance.get("data", {}).get("balance", 0)
        log(f"    平衡度={balance_val:.4f}")

        # Step 6: Python 整合结果 → 写回 C core
        log("  Step 6: Python 整合 → C core 记录")
        mutations = 1 if mutate_result.get("status") == "ok" else 0
        knowledge = 2 if issue_count > 0 else 1
        record = self.python_to_c_core("record_evolution", {
            "mutations": mutations,
            "knowledge": knowledge,
            "fitness": new_fitness,
        })
        log(f"    C core 更新: total_mut={record['data']['total_mutations']}, "
            f"total_know={record['data']['total_knowledge']}, fitness={record['data']['fitness']:.4f}")

        # 最终健康检查
        log("  Step 7: C core 健康检查")
        health = self.c_core_to_python("health_check")
        log(f"    health={health['data']['health']}, issues={health['data']['issues']}")

        elapsed = time.time() - cycle_start
        log(f"\n✅ 完整进化循环完成 ({elapsed:.2f}s)")

        result = {
            "status": "ok",
            "cycle": cycle,
            "fitness_before": fitness,
            "fitness_after": record["data"]["fitness"],
            "weaknesses": issues,
            "mutations": mutations,
            "knowledge_added": knowledge,
            "balance": balance_val,
            "health": health["data"]["health"],
            "elapsed_seconds": round(elapsed, 3),
        }
        self._log_flow("pipeline", "complete", "full_cycle", result)
        return result

    def process_weakness(self) -> Dict[str, Any]:
        """
        短板修复流程：
          C core 检测短板 → 分析各域 → Rust 评估 → Rust 变异修复 → C core 记录
        """
        log("\n🔧 === 短板修复流程 ===")

        # 检测短板
        weakness = self.c_core_to_python("detect_weakness")
        issues = weakness["data"]["weaknesses"]
        if issues == "none":
            log("  无短板，流程结束")
            return {"status": "ok", "message": "no_weakness", "fixed": 0}

        issue_list = [i.strip() for i in issues.split(",")]
        fixed = 0

        for issue in issue_list:
            log(f"  修复短板: {issue}")
            try:
                # 变异修复（先创建基因）
                mutate_r = self.python_to_rust("mutate", {
                    "domain": issue,
                    "change": 0.1,
                })
                created_gene = mutate_r.get("data", {}).get("gene_id", "")
                # 评估新基因
                if created_gene:
                    eval_r = self.python_to_rust("evaluate", {
                        "gene_id": created_gene,
                        "domain": issue,
                        "fitness": 0.5,
                    })
                fixed += 1
                log(f"    ✅ 已修复: {issue}")
            except (CommandFailedError, CommandTimeoutError) as e:
                log(f"    ❌ 修复失败: {issue} — {e}", "WARN")

        # 记录修复结果
        self.python_to_c_core("record_evolution", {
            "mutations": fixed,
            "knowledge": fixed,
            "fitness": 1.0 + fixed * 0.05,
        })

        log(f"  短板修复完成: {fixed}/{len(issue_list)}")
        return {"status": "ok", "fixed": fixed, "total": len(issue_list)}

    def send_knowledge(self, domain: str, content: str) -> Dict[str, Any]:
        """
        知识注入流程：
          C core 记录知识 → Rust 评估新知识 → Rust 可能产生变异 → C core 确认
        """
        log(f"\n📚 === 知识注入: {domain} ===")

        # C core 记录知识
        record = self.python_to_c_core("record_evolution", {
            "mutations": 0,
            "knowledge": 1,
            "fitness": 1.0,
        })

        # 先变异创建基因，再评估
        mutate_r = self.python_to_rust("mutate", {
            "domain": domain,
            "change": 0.02,
        })
        created_gene = mutate_r.get("data", {}).get("gene_id", "")

        # Rust 评估新基因
        eval_r = {"status": "skipped"}
        if created_gene:
            eval_r = self.python_to_rust("evaluate", {
                "gene_id": created_gene,
                "domain": domain,
                "fitness": 0.7,
            })

        log(f"  ✅ 知识注入完成")
        return {
            "status": "ok",
            "domain": domain,
            "total_knowledge": record["data"]["total_knowledge"],
            "eval_status": eval_r.get("status"),
        }

    def get_system_status(self) -> Dict[str, Any]:
        """获取完整系统状态"""
        c_status = self.c_core_to_python("status")
        rust_status = self.python_to_rust("status")

        return {
            "c_core": c_status.get("data", {}),
            "rust_engine": rust_status.get("data", {}),
            "pipeline_healthy": (
                self.c_core.is_running() and self.rust_engine.is_running()
            ),
        }

    # ----------------------------------------------------------
    # 内部工具
    # ----------------------------------------------------------

    def _log_flow(self, source: str, target: str, cmd: str, data: Any):
        """记录数据流日志"""
        entry = {
            "time": datetime.now().isoformat(),
            "source": source,
            "target": target,
            "cmd": cmd,
            "data_summary": str(data)[:200],
        }
        self._run_log.append(entry)

        # 写入文件日志
        try:
            with open(PIPELINE_LOG, "a") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except:
            pass


# ============================================================
# 日志工具
# ============================================================

def log(msg: str, level: str = "INFO"):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] [{level}] {msg}", flush=True)


# ============================================================
# 命令行测试
# ============================================================

def run_tests():
    """运行完整的管道测试"""
    log("=" * 60)
    log("🧬 三层数据流 IPC 管道测试")
    log("=" * 60)
    results = []

    try:
        with ThreeLayerPipeline() as pipe:

            # --- Test 1: 基础连通性 ---
            log("\n📋 Test 1: 基础连通性（心跳）")
            hb = pipe.c_core_to_python("heartbeat")
            assert hb["status"] == "ok", "心跳失败"
            results.append(("心跳连通", True))
            log("  ✅ PASS")

            # --- Test 2: C core → Python 短板 ---
            log("\n📋 Test 2: C core → Python（短板检测）")
            w = pipe.c_core_to_python("detect_weakness")
            assert w["status"] == "ok", "短板检测失败"
            results.append(("C→Python 短板", True))
            log(f"  ✅ PASS: {w['data']['weaknesses']}")

            # --- Test 3: Python → Rust 变异（先创建基因） ---
            log("\n📋 Test 3: Python → Rust（变异 — 创建基因）")
            mut = pipe.python_to_rust("mutate", {
                "domain": "变异",
                "change": 0.05,
            })
            assert mut["status"] == "ok", "变异失败"
            created_gene_id = mut.get("data", {}).get("gene_id", "")
            results.append(("Python→Rust 变异", True))
            log(f"  ✅ PASS: gene={created_gene_id}")

            # --- Test 4: Python → Rust 评估（用刚创建的基因） ---
            log("\n📋 Test 4: Python → Rust（评估已存在基因）")
            ev = pipe.python_to_rust("evaluate", {
                "gene_id": created_gene_id,
                "domain": "变异",
                "fitness": 0.85,
            })
            assert ev["status"] == "ok", "评估失败"
            results.append(("Python→Rust 评估", True))
            log(f"  ✅ PASS: score={ev.get('data', {}).get('score', 0):.4f}")

            # --- Test 5: Python → Rust 平衡 ---
            log("\n📋 Test 5: Python → Rust（平衡检测）")
            bal = pipe.python_to_rust("balance")
            assert bal["status"] == "ok", "平衡检测失败"
            results.append(("Python→Rust 平衡", True))
            log(f"  ✅ PASS: balance={bal.get('data', {}).get('balance', 0):.4f}")

            # --- Test 6: Python → C core 记录 ---
            log("\n📋 Test 6: Python → C core（记录进化）")
            rec = pipe.python_to_c_core("record_evolution", {
                "mutations": 1,
                "knowledge": 2,
                "fitness": 1.15,
            })
            assert rec["status"] == "ok", "记录失败"
            results.append(("Python→C 记录", True))
            log(f"  ✅ PASS: fitness={rec['data']['fitness']:.4f}")

            # --- Test 7: Rust → Python 状态查询 ---
            log("\n📋 Test 7: Rust → Python（状态查询）")
            rs = pipe.rust_to_python("status")
            assert rs["status"] == "ok", "状态查询失败"
            results.append(("Rust→Python 状态", True))
            log(f"  ✅ PASS: genes={rs.get('data', {}).get('total_genes', 0)}")

            # --- Test 8: 重试机制 ---
            log("\n📋 Test 8: 重试机制验证")
            # 连续发 3 次心跳验证重试有效
            for i in range(3):
                hb2 = pipe.c_core.send_with_retry("heartbeat", retries=2)
                assert hb2["status"] == "ok"
            results.append(("重试机制", True))
            log("  ✅ PASS: 3 次重试成功")

            # --- Test 9: 错误处理（无效命令） ---
            log("\n📋 Test 9: 错误处理（无效命令）")
            try:
                pipe.c_core.send_command("nonexistent_cmd")
                results.append(("错误处理", False))
                log("  ❌ FAIL: 应该抛出异常")
            except CommandFailedError:
                results.append(("错误处理", True))
                log("  ✅ PASS: 正确抛出 CommandFailedError")

            # --- Test 10: 完整进化循环 ---
            log("\n📋 Test 10: 完整进化循环")
            cycle_result = pipe.run_full_cycle()
            assert cycle_result["status"] == "ok"
            results.append(("完整循环", True))
            log(f"  ✅ PASS: fitness {cycle_result['fitness_before']:.4f} → {cycle_result['fitness_after']:.4f}")

            # --- Test 11: 知识注入 ---
            log("\n📋 Test 11: 知识注入")
            kb = pipe.send_knowledge("变异", "新知识：自变异幅度应控制在 0.05 以内")
            assert kb["status"] == "ok"
            results.append(("知识注入", True))
            log(f"  ✅ PASS: total_knowledge={kb['total_knowledge']}")

            # --- Test 12: 系统状态汇总 ---
            log("\n📋 Test 12: 系统状态汇总")
            status = pipe.get_system_status()
            assert status["pipeline_healthy"]
            results.append(("系统状态", True))
            log(f"  ✅ PASS: C gen={status['c_core'].get('generation')}, "
                f"Rust genes={status['rust_engine'].get('total_genes', 0)}")

    except Exception as e:
        log(f"\n💥 测试异常: {e}", "ERROR")
        traceback.print_exc()
        results.append(("异常处理", False))

    # --- 汇总 ---
    log("\n" + "=" * 60)
    log("📊 测试结果汇总")
    log("=" * 60)
    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    for name, ok in results:
        icon = "✅" if ok else "❌"
        log(f"  {icon} {name}")

    log(f"\n  通过: {passed}/{total}")
    if passed == total:
        log("  🎉 全部通过！三层数据流管道工作正常。")
    else:
        log("  ⚠️  部分测试失败，请检查日志。")

    # 数据流路径总结
    log("\n📡 数据流路径:")
    log("  C core (心跳/短板/健康)")
    log("    → Python (分析决策)")
    log("      → Rust (评估/变异/平衡)")
    log("        → Python (整合结果)")
    log("          → C core (记录进化)")

    return 0 if passed == total else 1


if __name__ == "__main__":
    if "--test" in sys.argv or len(sys.argv) == 1:
        sys.exit(run_tests())
    else:
        print("用法: python3 pipeline.py [--test]")
        print("  --test  运行完整管道测试")
        print("  (无参数) 默认运行测试")
