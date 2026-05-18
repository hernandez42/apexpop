#!/usr/bin/env python3
"""
Glue Layer — Python 粘合层
连接 C Core、Rust Engine 和 LLM 的协调层

架构：
  ┌─────────────────────────────────────────────────┐
  │                  glue.py (Python)               │
  │         胶水层 — 协调一切，自身无状态             │
  ├─────────────────────────────────────────────────┤
  │                                                 │
  │   ┌──────────┐   ┌──────────┐   ┌──────────┐  │
  │   │ C Core   │   │ Rust Eng │   │  LLM     │  │
  │   │ (identity│   │ (mutate/ │   │ (decide/ │  │
  │   │  health) │   │  eval)   │   │  think)  │  │
  │   └────┬─────┘   └────┬─────┘   └────┬─────┘  │
  │        │  pipe stdin/  │  pipe stdin/  │  API   │
  │        │  stdout       │  stdout       │        │
  │        ▼              ▼              ▼        │
  │   ┌──────────────────────────────────────────┐ │
  │   │           Echo Wall (知识循环增强)        │ │
  │   └──────────────────────────────────────────┘ │
  └─────────────────────────────────────────────────┘

通信协议：
  - C Core / Rust Engine：stdin/stdout JSON 行协议
  - LLM：OpenAI 兼容 API（可配 mock 模式测试）
  - Echo Wall：Python 直接调用

进化主循环（每个心跳周期）：
  1. C Core 心跳 + 健康检查
  2. C Core 检测短板
  3. Python 调用 LLM 分析短板
  4. LLM 决定搜索/变异方向
  5. Python 调用 Rust Engine 执行变异
  6. Rust Engine 评估结果
  7. Python 调用 Echo Wall 回响知识
  8. C Core 记录进化状态
"""

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# === 路径配置 ===
BASE_DIR = Path(__file__).parent
C_CORE_BIN = str(BASE_DIR / "c-core-pipe")
RUST_ENGINE_BIN = str(BASE_DIR / "rust-engine-pipe")
ECHO_WALL_SCRIPT = str(Path.home() / ".openclaw/workspace/scripts/echo-wall.py")
MEMORY_DIR = Path.home() / ".openclaw/workspace/memory"
EVOLUTION_LOG = MEMORY_DIR / "evolution-cycles.jsonl"

# === LLM 配置 ===
LLM_API_KEY = os.environ.get("MIMO_API_KEY", "")
LLM_BASE_URL = os.environ.get("MIMO_BASE_URL", "https://api.mimo.xiaomi.com/v1")
LLM_MODEL = os.environ.get("MIMO_OMNI_MODEL", "mimo-v2.5")
LLM_MOCK = os.environ.get("GLUE_MOCK_LLM", "1") == "1"  # 默认 mock 模式


# === 子进程管理 ===
@dataclass
class SubprocessBridge:
    """管道通信桥接器 — 管理子进程的 stdin/stdout"""
    name: str
    binary: str
    process: Optional[subprocess.Popen] = field(default=None, init=False)
    ready: bool = field(default=False, init=False)

    def start(self) -> bool:
        """启动子进程"""
        try:
            self.process = subprocess.Popen(
                [self.binary],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,  # 行缓冲
            )
            # 读取 ready 消息
            line = self.process.stdout.readline().strip()
            if line:
                msg = json.loads(line)
                if msg.get("status") == "ready":
                    self.ready = True
                    print(f"[Glue] ✅ {self.name} 就绪: {msg.get('msg', '')}")
                    return True
            print(f"[Glue] ❌ {self.name} 未就绪: {line}")
            return False
        except Exception as e:
            print(f"[Glue] ❌ {self.name} 启动失败: {e}")
            return False

    def send(self, cmd: dict) -> dict:
        """发送命令并接收响应"""
        if not self.process or not self.ready:
            return {"status": "error", "msg": f"{self.name} 未就绪"}

        try:
            line = json.dumps(cmd, ensure_ascii=False)
            self.process.stdin.write(line + "\n")
            self.process.stdin.flush()

            response = self.process.stdout.readline().strip()
            if response:
                return json.loads(response)
            return {"status": "error", "msg": "空响应"}
        except Exception as e:
            return {"status": "error", "msg": str(e)}

    def stop(self):
        """停止子进程"""
        if self.process:
            try:
                self.process.stdin.close()
                self.process.terminate()
                self.process.wait(timeout=5)
            except Exception:
                self.process.kill()
            self.ready = False
            print(f"[Glue] 🔴 {self.name} 已停止")


# === LLM 接口 ===
class LLMInterface:
    """LLM 接口 — 支持 mock 模式和真实 API"""

    def __init__(self, mock: bool = True):
        self.mock = mock
        if not mock:
            try:
                import urllib.request
                self._request = urllib.request
            except ImportError:
                self.mock = True

    def analyze_weakness(self, weaknesses: dict, c_core_state: dict) -> dict:
        """分析短板，返回 LLM 的决策"""
        prompt = self._build_analysis_prompt(weaknesses, c_core_state)

        if self.mock:
            return self._mock_analyze(weaknesses, c_core_state)

        return self._real_analyze(prompt)

    def _build_analysis_prompt(self, weaknesses: dict, state: dict) -> str:
        return f"""你是一个进化引擎的大脑。分析以下短板并给出决策。

当前状态：
- 进化代数: {state.get('generation', 0)}
- 适应度: {state.get('fitness', 0):.4f}
- 平衡度: {state.get('balance', 0):.4f}
- 技能数: {state.get('mutations', 0)}
- 知识数: {state.get('knowledge', 0)}

检测到的短板: {json.dumps(weaknesses, ensure_ascii=False)}

请返回 JSON 格式的决策：
{{
  "action": "mutate",
  "domain": "选择的领域",
  "change": 0.1,
  "reason": "选择理由",
  "search_direction": "搜索方向描述"
}}

领域可选: 变异, 安全, 共进化, 协议, 探索, 知识, 记忆, 评估"""

    def _mock_analyze(self, weaknesses: dict, state: dict) -> dict:
        """Mock LLM 分析 — 根据短板返回合理决策"""
        count = weaknesses.get("count", 0)
        weakness_str = weaknesses.get("weaknesses", "none")
        skills = state.get("mutations", 0)
        knowledge = state.get("knowledge", 0)

        # 根据短板类型决定领域
        if "skills_count_low" in weakness_str:
            domain = "变异"
            change = 0.15
            reason = "技能数量不足，需要增加变异技能"
        elif "knowledge_count_low" in weakness_str:
            domain = "知识"
            change = 0.12
            reason = "知识储备不足，需要吸收更多知识"
        elif "balance_low" in weakness_str:
            domain = "共进化"
            change = 0.08
            reason = "平衡度低，需要促进领域间共进化"
        elif "fitness_low" in weakness_str:
            domain = "评估"
            change = 0.10
            reason = "适应度低，需要加强评估能力"
        elif "health_degraded" in weakness_str:
            domain = "安全"
            change = 0.05
            reason = "健康状态异常，需要修复"
        else:
            # 无明显短板，探索新方向
            import random
            domains = ["探索", "记忆", "协议", "共进化"]
            domain = random.choice(domains)
            change = 0.06
            reason = "无明显短板，探索新方向以预防退化"

        return {
            "status": "ok",
            "action": "mutate",
            "domain": domain,
            "change": change,
            "reason": reason,
            "search_direction": f"在{domain}领域进行渐进变异",
        }

    def _real_analyze(self, prompt: str) -> dict:
        """真实 LLM 调用（OpenAI 兼容 API）"""
        try:
            import urllib.request
            import urllib.error

            payload = json.dumps({
                "model": LLM_MODEL,
                "messages": [
                    {"role": "system", "content": "你是一个进化引擎的决策大脑。只返回 JSON，不要其他内容。"},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.7,
                "max_tokens": 512,
            }).encode()

            req = urllib.request.Request(
                f"{LLM_BASE_URL}/chat/completions",
                data=payload,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {LLM_API_KEY}",
                },
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read())
                content = data["choices"][0]["message"]["content"]
                # 提取 JSON
                if "```json" in content:
                    content = content.split("```json")[1].split("```")[0]
                return json.loads(content.strip())
        except Exception as e:
            print(f"[Glue] ⚠️ LLM 调用失败: {e}，回退 mock")
            return self._mock_analyze({}, {})


# === 回音壁集成 ===
def run_echo_wall(domain: str, content: str, strength: float = 0.5) -> dict:
    """调用 echo-wall.py 进行知识回响"""
    try:
        result = subprocess.run(
            [sys.executable, "-c", f"""
import sys
sys.path.insert(0, "{str(Path(ECHO_WALL_SCRIPT).parent)}")
from importlib.util import spec_from_file_location, module_from_spec
spec = spec_from_file_location("echo_wall", "{ECHO_WALL_SCRIPT}")
ew = module_from_spec(spec)
spec.loader.exec_module(ew)

k = ew.add_knowledge("{content}", "{domain}", {strength})
result = ew.run_echo_cycle()
import json
print(json.dumps(result if result else {{"status": "ok", "echoed": 1}}, ensure_ascii=False))
"""],
            capture_output=True, text=True, timeout=10
        )
        if result.stdout.strip():
            return json.loads(result.stdout.strip())
        return {"status": "ok", "msg": "echo wall silent"}
    except Exception as e:
        return {"status": "error", "msg": str(e)}


# === 进化主循环 ===
class EvolutionLoop:
    """进化主循环 — 每个心跳周期执行一次完整循环"""

    def __init__(self, max_cycles: int = 3, mock_llm: bool = True):
        self.max_cycles = max_cycles
        self.c_core = SubprocessBridge("C Core", C_CORE_BIN)
        self.rust_engine = SubprocessBridge("Rust Engine", RUST_ENGINE_BIN)
        self.llm = LLMInterface(mock=mock_llm)
        self.cycle_log = []

    def start(self) -> bool:
        """启动所有组件"""
        print("=" * 60)
        print("  Glue Layer — 进化主循环启动")
        print("=" * 60)

        c_ok = self.c_core.start()
        r_ok = self.rust_engine.start()

        if not c_ok or not r_ok:
            print("[Glue] ❌ 组件启动失败，无法运行")
            return False

        print("[Glue] ✅ 所有组件就绪，开始进化循环\n")
        return True

    def run_cycle(self, cycle_num: int) -> dict:
        """执行一个完整的进化循环"""
        print(f"--- 循环 #{cycle_num} ---")
        result = {"cycle": cycle_num, "steps": []}

        # Step 1: C Core 心跳
        print(f"  [1] C Core 心跳...")
        heartbeat = self.c_core.send({"cmd": "heartbeat"})
        result["steps"].append({"step": "heartbeat", "result": heartbeat.get("status")})
        print(f"      → cycle={heartbeat.get('data',{}).get('cycle')}, "
              f"fitness={heartbeat.get('data',{}).get('fitness',0):.4f}")
        time.sleep(0.1)

        # Step 2: C Core 检测短板
        print(f"  [2] 检测短板...")
        weakness = self.c_core.send({"cmd": "detect_weakness"})
        result["steps"].append({"step": "detect_weakness", "result": weakness.get("status")})
        count = weakness.get("data", {}).get("count", 0)
        w_list = weakness.get("data", {}).get("weaknesses", "none")
        print(f"      → 发现 {count} 个短板: {w_list}")
        time.sleep(0.1)

        # Step 3: LLM 分析短板
        print(f"  [3] LLM 分析...")
        c_state = heartbeat.get("data", {})
        llm_decision = self.llm.analyze_weakness(
            weakness.get("data", {}),
            c_state,
        )
        result["steps"].append({"step": "llm_analyze", "result": llm_decision.get("status")})
        print(f"      → 决策: {llm_decision.get('domain', '?')} "
              f"(change={llm_decision.get('change', 0):.3f})")
        print(f"      → 理由: {llm_decision.get('reason', '?')}")
        time.sleep(0.1)

        # Step 4: Rust Engine 执行变异
        print(f"  [4] Rust Engine 变异...")
        mutate_result = self.rust_engine.send({
            "cmd": "mutate",
            "domain": llm_decision.get("domain", "探索"),
            "change": llm_decision.get("change", 0.1),
        })
        result["steps"].append({"step": "mutate", "result": mutate_result.get("status")})
        gene_id = mutate_result.get("data", {}).get("gene_id", "")
        print(f"      → 基因: {gene_id}")
        time.sleep(0.1)

        # Step 5: Rust Engine 评估
        print(f"  [5] Rust Engine 评估...")
        if gene_id:
            eval_result = self.rust_engine.send({"cmd": "evaluate", "gene_id": gene_id})
            result["steps"].append({"step": "evaluate", "result": eval_result.get("status")})
            score = eval_result.get("data", {}).get("score", 0)
            print(f"      → 评估分: {score:.4f}")

            # 高分保留
            if score > 0.5:
                retain = self.rust_engine.send({"cmd": "retain", "gene_id": gene_id})
                print(f"      → 保留: {retain.get('status')}")
        else:
            print(f"      → 无基因可评估")
        time.sleep(0.1)

        # Step 6: Echo Wall 知识回响
        print(f"  [6] Echo Wall 回响...")
        echo_result = run_echo_wall(
            domain=llm_decision.get("domain", "探索"),
            content=llm_decision.get("reason", "进化变异"),
            strength=llm_decision.get("change", 0.1),
        )
        result["steps"].append({"step": "echo_wall", "result": echo_result.get("status")})
        print(f"      → 回响: {echo_result}")
        time.sleep(0.1)

        # Step 7: C Core 记录进化
        print(f"  [7] 记录进化状态...")
        record = self.c_core.send({
            "cmd": "record_evolution",
            "mutations": 1,
            "knowledge": 1,
            "fitness": c_state.get("fitness", 1.0) + llm_decision.get("change", 0.1) * 0.01,
        })
        result["steps"].append({"step": "record_evolution", "result": record.get("status")})
        print(f"      → 记录完成\n")

        return result

    def run(self):
        """运行完整进化循环"""
        if not self.start():
            return

        try:
            for i in range(1, self.max_cycles + 1):
                cycle_result = self.run_cycle(i)
                self.cycle_log.append(cycle_result)

            # 最终状态
            print("=" * 60)
            print("  进化循环完成 — 最终状态")
            print("=" * 60)

            status = self.c_core.send({"cmd": "status"})
            print(f"  C Core: {json.dumps(status.get('data', {}), indent=2, ensure_ascii=False)}")

            status_r = self.rust_engine.send({"cmd": "status"})
            print(f"  Rust:   {json.dumps(status_r.get('data', {}), indent=2, ensure_ascii=False)}")

            # 保存日志
            MEMORY_DIR.mkdir(parents=True, exist_ok=True)
            with open(EVOLUTION_LOG, "a") as f:
                for entry in self.cycle_log:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            print(f"\n  日志已保存: {EVOLUTION_LOG}")

        finally:
            self.c_core.stop()
            self.rust_engine.stop()


# === 入口 ===
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Glue Layer — 进化粘合层")
    parser.add_argument("--cycles", type=int, default=3, help="进化循环次数")
    parser.add_argument("--mock-llm", action="store_true", default=True, help="使用 mock LLM")
    parser.add_argument("--real-llm", action="store_true", help="使用真实 LLM API")
    args = parser.parse_args()

    mock = not args.real_llm
    loop = EvolutionLoop(max_cycles=args.cycles, mock_llm=mock)
    loop.run()
