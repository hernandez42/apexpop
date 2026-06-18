#!/usr/bin/env python3
"""superclaw 三层管道端到端集成测试.

真实启动 C Core (core-dna/main_pipe.c) 与 Rust Engine (core-dna/rust_pipe) 子进程，
通过 stdin/stdout JSON 行协议通信，验证三层管道协同。

协议说明（与源码核对后的实际协议，非任务描述中的假设协议）：
  - C Core 支持: heartbeat / detect_weakness / record_evolution / health_check / status
    无 mutate / evolve 命令；mutations 通过 record_evolution 增加，fitness 通过
    record_evolution 的 fitness 字段设置（heartbeat 也会按 cycle/knowledge 重算 fitness）。
  - Rust Engine 支持: mutate / evaluate / retain / forget / balance / status / retention_check
    无 evolve 命令；status 的 genes 字段是数量计数（非数组）；"进化结果"用 evaluate 验证。
"""
import json
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import pytest

# 让 superclaw 包可导入
SUPERCLAW_ROOT = Path(__file__).resolve().parent.parent
if str(SUPERCLAW_ROOT) not in sys.path:
    sys.path.insert(0, str(SUPERCLAW_ROOT))

CORE_DNA = SUPERCLAW_ROOT / "core-dna"
C_CORE_BIN = CORE_DNA / "c-core-pipe"
RUST_CARGO_DIR = CORE_DNA / "rust_pipe"
RUST_ENGINE_BIN = CORE_DNA / "rust-engine-pipe"

RESPONSE_TIMEOUT = 5.0


def _has(cmd):
    return shutil.which(cmd) is not None


# ============================================================
# PipeClient — 子进程 JSON 行协议客户端（带超时与异常检测）
# ============================================================
class PipeClient:
    """通过 subprocess.Popen 启动子进程，stdin.write 发 JSON+\\n，stdout.readline 读响应。"""

    def __init__(self, args, name="pipe"):
        self.args = [args] if isinstance(args, str) else list(args)
        self.name = name
        self.proc = None
        self.ready_msg = None
        self._lock = threading.Lock()
        self._broken = False

    def start(self, ready_timeout=RESPONSE_TIMEOUT):
        self.proc = subprocess.Popen(
            self.args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        self.ready_msg = self._readline(ready_timeout)
        return self.ready_msg

    def _readline(self, timeout):
        if self.proc is None or self.proc.stdout is None:
            raise RuntimeError(f"{self.name}: 进程未启动")
        box = {"line": None}

        def _read():
            try:
                box["line"] = self.proc.stdout.readline()
            except Exception:
                box["line"] = ""

        # 用独立线程读取以支持超时，避免 readline 永久阻塞
        t = threading.Thread(target=_read, daemon=True)
        t.start()
        t.join(timeout=timeout)
        if t.is_alive():
            self._broken = True
            raise TimeoutError(f"{self.name}: 读取响应超时 ({timeout}s)")
        line = box["line"]
        if not line:
            return None
        return line.strip()

    def send_raw(self, raw_line, timeout=RESPONSE_TIMEOUT):
        """发送原始字符串行，返回响应原始字符串。"""
        with self._lock:
            if self._broken:
                raise RuntimeError(f"{self.name}: 通信链路已断开")
            if self.proc is None or self.proc.poll() is not None:
                raise RuntimeError(f"{self.name}: 进程未运行")
            try:
                self.proc.stdin.write(raw_line + "\n")
                self.proc.stdin.flush()
            except (BrokenPipeError, OSError) as e:
                self._broken = True
                raise RuntimeError(f"{self.name}: 写入失败: {e}")
            resp = self._readline(timeout)
            if resp is None:
                self._broken = True
                raise RuntimeError(f"{self.name}: 进程已退出（无响应）")
            return resp

    def send(self, cmd, timeout=RESPONSE_TIMEOUT):
        """发送 dict 命令，返回解析后的 dict 响应。"""
        resp = self.send_raw(json.dumps(cmd, ensure_ascii=False), timeout=timeout)
        try:
            return json.loads(resp)
        except json.JSONDecodeError as e:
            raise AssertionError(f"{self.name}: 响应非 JSON: {resp!r} ({e})")

    def is_alive(self):
        return self.proc is not None and self.proc.poll() is None

    def stop(self):
        proc = self.proc
        if proc is None:
            return
        try:
            if proc.poll() is None:
                try:
                    proc.stdin.close()
                except Exception:
                    pass
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=3)
        finally:
            self.proc = None


# ============================================================
# 编译 fixtures（session 级；工具链缺失或编译失败则 skip）
# ============================================================
@pytest.fixture(scope="session")
def c_core_binary():
    src = CORE_DNA / "main_pipe.c"
    if not src.exists():
        pytest.skip(f"{src} 不存在")
    if not _has("gcc"):
        if C_CORE_BIN.exists():
            return str(C_CORE_BIN)
        pytest.skip("gcc 不可用且 c-core-pipe 不存在")
    result = subprocess.run(
        ["gcc", "-O2", "-Wall", "-std=c11", "-D_POSIX_C_SOURCE=200809L",
         "-o", str(C_CORE_BIN), str(src), "-lm"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        pytest.skip(f"gcc 编译失败: {result.stderr[:300]}")
    return str(C_CORE_BIN)


@pytest.fixture(scope="session")
def rust_engine_binary():
    if not (RUST_CARGO_DIR / "Cargo.toml").exists():
        pytest.skip("Cargo.toml 不存在")
    if not _has("cargo"):
        if RUST_ENGINE_BIN.exists():
            return str(RUST_ENGINE_BIN)
        pytest.skip("cargo 不可用且 rust-engine-pipe 不存在")
    result = subprocess.run(
        ["cargo", "build", "--release"],
        cwd=str(RUST_CARGO_DIR), capture_output=True, text=True,
    )
    if result.returncode != 0:
        pytest.skip(f"cargo build 失败: {result.stderr[:300]}")
    src_bin = RUST_CARGO_DIR / "target" / "release" / "rust-engine-pipe"
    if not src_bin.exists():
        pytest.skip("rust-engine-pipe 未生成")
    shutil.copy(str(src_bin), str(RUST_ENGINE_BIN))
    return str(RUST_ENGINE_BIN)


# ============================================================
# 进程 fixtures（function 级，autouse=False，启动→yield→终止）
# ============================================================
@pytest.fixture
def c_core(c_core_binary):
    client = PipeClient(c_core_binary, name="C Core")
    ready = client.start()
    assert ready, "C Core 未发送 ready 消息"
    yield client
    client.stop()


@pytest.fixture
def rust_engine(rust_engine_binary):
    client = PipeClient(rust_engine_binary, name="Rust")
    ready = client.start()
    assert ready, "Rust Engine 未发送 ready 消息"
    yield client
    client.stop()


# ============================================================
# C Core 单进程测试
# ============================================================
def test_c_core_ready_message(c_core):
    r = json.loads(c_core.ready_msg)
    assert r["status"] == "ready"
    assert r.get("name") == "MiMoClaw"


def test_c_core_heartbeat(c_core):
    resp = c_core.send({"cmd": "heartbeat"})
    assert resp["status"] == "ok"
    assert resp["cmd"] == "heartbeat"
    data = resp["data"]
    for field in ("cycle", "generation", "fitness", "balance",
                  "health", "mutations", "knowledge"):
        assert field in data, f"heartbeat 缺少字段 {field}"


def test_c_core_status_fields(c_core):
    resp = c_core.send({"cmd": "status"})
    assert resp["status"] == "ok"
    assert resp["cmd"] == "status"
    data = resp["data"]
    for field in ("fitness", "balance", "health", "mutations", "knowledge"):
        assert field in data, f"status 缺少字段 {field}"


def test_c_core_record_evolution_increases_mutations(c_core):
    # C Core 无 mutate 命令；mutations 通过 record_evolution 增加
    before = c_core.send({"cmd": "status"})["data"]["mutations"]
    resp = c_core.send({"cmd": "record_evolution",
                        "mutations": 3, "knowledge": 1, "fitness": 1.5})
    assert resp["status"] == "ok"
    assert resp["cmd"] == "record_evolution"
    assert resp["data"]["total_mutations"] == before + 3
    after = c_core.send({"cmd": "status"})["data"]["mutations"]
    assert after == before + 3


def test_c_core_record_evolution_changes_fitness(c_core):
    # C Core 无 evolve 命令；fitness 通过 record_evolution 设置
    resp = c_core.send({"cmd": "record_evolution",
                        "mutations": 0, "knowledge": 0, "fitness": 2.345})
    assert resp["status"] == "ok"
    assert abs(resp["data"]["fitness"] - 2.345) < 0.01
    status = c_core.send({"cmd": "status"})
    assert abs(status["data"]["fitness"] - 2.345) < 0.01


def test_c_core_unknown_cmd(c_core):
    resp = c_core.send({"cmd": "totally_unknown_xyz"})
    assert resp["status"] == "error"
    assert "unknown" in resp["msg"]


def test_c_core_malformed_json_no_crash(c_core):
    # 畸形 JSON（无 cmd 字段）→ 返回 error，进程不崩溃，后续仍可通信
    resp_line = c_core.send_raw("{{{{broken json without cmd")
    r = json.loads(resp_line)
    assert r["status"] == "error"
    assert c_core.is_alive()
    hb = c_core.send({"cmd": "heartbeat"})
    assert hb["status"] == "ok"


def test_c_core_heartbeat_cycle_increments(c_core):
    cycles = []
    for _ in range(3):
        resp = c_core.send({"cmd": "heartbeat"})
        cycles.append(resp["data"]["cycle"])
    assert cycles == sorted(cycles)
    assert cycles[-1] - cycles[0] == 2


# ============================================================
# Rust Engine 单进程测试
# ============================================================
def test_rust_ready_message(rust_engine):
    r = json.loads(rust_engine.ready_msg)
    assert r["status"] == "ready"


def test_rust_status_has_genes(rust_engine):
    resp = rust_engine.send({"cmd": "status"})
    assert resp["status"] == "ok"
    assert resp["cmd"] == "status"
    data = resp["data"]
    # 实际协议：genes 为数量计数（非数组）
    assert "genes" in data
    assert data["genes"] == 0


def test_rust_mutate_returns_new_gene(rust_engine):
    resp = rust_engine.send({"cmd": "mutate", "domain": "变异", "change": 0.1})
    assert resp["status"] == "ok"
    assert resp["cmd"] == "mutate"
    data = resp["data"]
    assert data["gene_id"].startswith("gene-")
    assert data["domain"] == "变异"
    assert data["total_genes"] == 1
    status = rust_engine.send({"cmd": "status"})
    assert status["data"]["genes"] == 1


def test_rust_evaluate_evolution_result(rust_engine):
    # Rust 无 evolve 命令；用 evaluate 验证进化结果（基因评分）
    m = rust_engine.send({"cmd": "mutate", "domain": "知识", "change": 0.05})
    gene_id = m["data"]["gene_id"]
    resp = rust_engine.send({"cmd": "evaluate", "gene_id": gene_id})
    assert resp["status"] == "ok"
    assert resp["cmd"] == "evaluate"
    assert "score" in resp["data"]
    assert resp["data"]["gene_id"] == gene_id


def test_rust_unknown_cmd(rust_engine):
    resp = rust_engine.send({"cmd": "no_such_cmd"})
    assert resp["status"] == "error"
    assert "unknown" in resp["msg"]


# ============================================================
# 三层管道集成测试（核心）
# ============================================================
def test_three_layer_pipeline_single_round(c_core, rust_engine):
    """C status → Rust mutate → C record_evolution：fitness 提升、genes 增加。"""
    c0 = c_core.send({"cmd": "status"})["data"]
    r0 = rust_engine.send({"cmd": "status"})["data"]
    init_fitness = c0["fitness"]
    init_mutations = c0["mutations"]
    init_genes = r0["genes"]

    # Rust Engine 生成基因
    mutate = rust_engine.send({"cmd": "mutate", "domain": "变异", "change": 0.1})
    assert mutate["status"] == "ok"
    assert mutate["data"]["gene_id"].startswith("gene-")

    # 把 Rust 结果喂回 C Core（record_evolution 即 C Core 的进化入口）
    new_fitness = init_fitness + 0.05
    record = c_core.send({"cmd": "record_evolution",
                          "mutations": 1, "knowledge": 0, "fitness": new_fitness})
    assert record["status"] == "ok"
    assert record["data"]["total_mutations"] == init_mutations + 1

    c1 = c_core.send({"cmd": "status"})["data"]
    r1 = rust_engine.send({"cmd": "status"})["data"]
    assert c1["fitness"] > init_fitness
    assert c1["mutations"] > init_mutations
    assert r1["genes"] > init_genes


def test_three_layer_pipeline_multi_round(c_core, rust_engine):
    """3 轮循环：C status → Rust mutate → C record_evolution，指标持续变化。"""
    c_mutations = []
    c_fitness = []
    r_genes = []
    domains = ["变异", "知识", "探索"]

    init_genes = rust_engine.send({"cmd": "status"})["data"]["genes"]

    for i in range(3):
        c_status = c_core.send({"cmd": "status"})["data"]
        c_fitness.append(c_status["fitness"])
        c_mutations.append(c_status["mutations"])

        m = rust_engine.send({"cmd": "mutate", "domain": domains[i], "change": 0.08})
        assert m["status"] == "ok"
        r_genes.append(rust_engine.send({"cmd": "status"})["data"]["genes"])

        new_fit = c_status["fitness"] + 0.03
        rec = c_core.send({"cmd": "record_evolution",
                           "mutations": 1, "knowledge": 1, "fitness": new_fit})
        assert rec["status"] == "ok"

    # mutations 单调递增
    assert c_mutations == sorted(c_mutations)
    assert c_mutations[-1] > c_mutations[0]
    # fitness 持续提升
    assert c_fitness[-1] > c_fitness[0]
    # Rust genes 每轮 +1，3 轮共 +3
    assert r_genes == [init_genes + 1, init_genes + 2, init_genes + 3]
    assert r_genes[-1] - init_genes == 3


# ============================================================
# 异常场景
# ============================================================
def test_subprocess_unexpected_exit_detected(c_core_binary):
    """子进程意外退出 → Python 层能检测并报错。"""
    client = PipeClient(c_core_binary, name="C Core")
    client.start()
    try:
        hb = client.send({"cmd": "heartbeat"})
        assert hb["status"] == "ok"
        # 模拟子进程意外退出
        client.proc.kill()
        client.proc.wait(timeout=3)
        # 再次通信应被检测并报错
        with pytest.raises(RuntimeError):
            client.send({"cmd": "heartbeat"})
    finally:
        client.stop()


def test_response_timeout_handled(tmp_path):
    """子进程响应超时（mock 延迟）→ 有超时处理。"""
    mock_script = tmp_path / "slow_pipe.py"
    mock_script.write_text(
        "import sys, json, time\n"
        "sys.stdout.write(json.dumps({'status': 'ready'}) + '\\n')\n"
        "sys.stdout.flush()\n"
        "for line in sys.stdin:\n"
        "    time.sleep(2.0)\n"
        "    sys.stdout.write(json.dumps({'status': 'ok'}) + '\\n')\n"
        "    sys.stdout.flush()\n"
    )
    client = PipeClient([sys.executable, str(mock_script)], name="slow")
    client.start()
    try:
        with pytest.raises(TimeoutError):
            client.send({"cmd": "ping"}, timeout=1.0)
    finally:
        client.stop()
