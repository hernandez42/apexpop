#!/usr/bin/env python3
"""
superclaw Glue Layer — 三层通信核心 + APEX 自进化框架

架构：
    ┌──────────────────────────────────────────────────┐
    │                  Python Glue                      │
    │  ┌─────────┐   ┌──────────┐   ┌────────────┐   │
    │  │ C Core  │←→│ Rust Eng │←→│ LLM Brain  │   │
    │  │ (id/health) │ (genome) │   │ (decide) │   │
    │  └─────────┘   └──────────┘   └────────────┘   │
    │              ↑ APEX 公式驱动 ↑                  │
    │  Φ_APEX = (base * ev * an * nv) / harm_rate    │
    └──────────────────────────────────────────────────┘

工作流程（每个进化循环）：
    1. C Core heartbeat → 获取 fitness/balance/状态
    2. C Core detect_weakness → 发现短板
    3. 计算 APEX 指标 Φ_APEX
    4. LLM analyze → 基于 Φ_APEX 和短板决定进化方向
    5. Rust Engine mutate → 生成基因
    6. Rust Engine evaluate → 评估基因
    7. C Core record_evolution → 保存进化记录
    8. 更新 APEX 指标，循环...
"""

import json
import os
import sys
import time
import subprocess
import re
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, Optional, List

# 路径
SUPERCLAW_ROOT = Path(__file__).parent.resolve()
CORE_DNA = SUPERCLAW_ROOT / "core-dna"
MEMORY_DIR = SUPERCLAW_ROOT / "memory"
LOG_DIR = SUPERCLAW_ROOT / "logs"
APEX_DIR = SUPERCLAW_ROOT / "apex-state"
for d in [MEMORY_DIR, LOG_DIR, APEX_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# 添加项目路径
sys.path.insert(0, str(SUPERCLAW_ROOT))

# 可执行文件路径
C_CORE_BIN = str(CORE_DNA / "c-core-pipe")
RUST_ENGINE_BIN = str(CORE_DNA / "rust-engine-pipe")


# ============================================================
# APEX 自进化框架
# ============================================================

class ApexState:
    """APEX 进化状态
    公式: Φ_APEX = (base * ev * an * nv) / harm_rate

    - base: 基础能力（来自 C Core fitness）
    - ev:   进化速度（mutations 增长率）
    - an:   适应度（Rust Engine balance）
    - nv:   新颖性（不同 domain 数量）
    - harm_rate: 损耗率（health 问题 + 错误数）
    """

    def __init__(self):
        self.base = 1.0
        self.ev = 0.1
        self.an = 0.0
        self.nv = 1
        self.harm_rate = 1.0
        self.phi_history: List[float] = []
        self.tier = 1  # T1~T5

    def update(self, c_status: Dict, r_status: Dict, mutations_delta: int = 0):
        """根据 C/Rust 状态更新 APEX 指标"""
        self.base = max(0.1, c_status.get("fitness", 1.0))

        # ev: 进化速度 = 最近 mutations 增量 + 0.01 平滑
        self.ev = max(0.01, mutations_delta * 0.1 + 0.01)

        # an: 适应度 = Rust Engine balance
        self.an = max(0.0, r_status.get("balance", 0.0))

        # nv: 新颖性 = 不同 domain 数量
        domains = r_status.get("domains", {})
        self.nv = max(1, len(domains))

        # harm_rate: 损耗率 = (5 - health) + errors
        health = c_status.get("health", 0)
        self.harm_rate = max(0.1, (2 - health) + 1.0)

    @property
    def phi(self) -> float:
        """计算 Φ_APEX"""
        if self.harm_rate <= 0:
            return 0.0
        return (self.base * self.ev * self.an * self.nv) / self.harm_rate

    def tier_level(self) -> int:
        """根据 Φ 值判定 Tier (T1~T5)"""
        p = self.phi
        if p < 0.1:
            return 1
        elif p < 1.0:
            return 2
        elif p < 10.0:
            return 3
        elif p < 100.0:
            return 4
        else:
            return 5

    def to_dict(self) -> Dict[str, Any]:
        return {
            "phi_apex": round(self.phi, 6),
            "base": round(self.base, 4),
            "ev": round(self.ev, 4),
            "an": round(self.an, 4),
            "nv": self.nv,
            "harm_rate": round(self.harm_rate, 4),
            "tier": self.tier_level(),
            "history_len": len(self.phi_history),
        }

    def save(self):
        """持久化 APEX 状态"""
        self.phi_history.append(self.phi)
        # 只保留最近 1000 条
        if len(self.phi_history) > 1000:
            self.phi_history = self.phi_history[-1000:]

        state_file = APEX_DIR / "apex-state.json"
        try:
            with open(state_file, "w") as f:
                json.dump({
                    "current": self.to_dict(),
                    "history": self.phi_history[-100:],
                }, f, indent=2, ensure_ascii=False)
        except IOError:
            pass


# ============================================================
# LLM Provider（极简，支持 mock + OpenAI 兼容）
# ============================================================

class LLMProvider:
    """LLM 调用 — 支持多 Provider"""

    def __init__(self, provider: str = "mock", api_key: str = "",
                 model: str = "", base_url: str = ""):
        self.provider = provider
        self.api_key = api_key
        self.model = model
        self.base_url = base_url

    def call(self, prompt: str, system: str = "") -> str:
        if self.provider == "mock":
            return self._mock_response(prompt)

        # OpenAI 兼容 API
        if not self.api_key:
            return f"[LLM Error] {self.provider.upper()}_API_KEY 未配置"

        import urllib.request
        import urllib.error

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload = json.dumps({
            "model": self.model or "default",
            "messages": messages,
            "temperature": 0.7,
            "max_tokens": 1024,
        }).encode("utf-8")

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        if self.provider == "openrouter":
            headers["HTTP-Referer"] = "https://superclaw.local"
            headers["X-Title"] = "superclaw"

        url = self.base_url or self._default_url()
        if not url:
            return f"[LLM Error] {self.provider} base_url 未配置"

        req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return data["choices"][0]["message"]["content"]
        except Exception as e:
            return f"[LLM Error] {e}"

    def _default_url(self) -> str:
        urls = {
            "deepseek": "https://api.deepseek.com/chat/completions",
            "groq": "https://api.groq.com/openai/v1/chat/completions",
            "openrouter": "https://openrouter.ai/api/v1/chat/completions",
            "openai": "https://api.openai.com/v1/chat/completions",
        }
        return urls.get(self.provider, "")

    def _mock_response(self, prompt: str) -> str:
        """Mock LLM — 基于 prompt 关键词生成决策"""
        # 从 prompt 提取状态信息
        weaknesses = re.search(r'短板[:：]\s*([^\n]+)', prompt)
        weakness_str = weaknesses.group(1) if weaknesses else "unknown"

        # 根据短板决定 domain
        domain = "变异"
        change = 0.1
        reason = "默认策略"

        if "knowledge" in weakness_str or "知识" in weakness_str:
            domain = "知识"
            change = 0.12
            reason = "知识储备不足，优先学习"
        elif "balance" in weakness_str or "平衡" in weakness_str:
            domain = "共进化"
            change = 0.08
            reason = "平衡度低，加强共进化"
        elif "fitness" in weakness_str or "适应" in weakness_str:
            domain = "变异"
            change = 0.15
            reason = "适应度低，加强变异"
        elif "skills" in weakness_str or "技能" in weakness_str:
            domain = "探索"
            change = 0.1
            reason = "技能不足，探索新领域"

        return json.dumps({
            "domain": domain,
            "change": change,
            "reason": reason,
        }, ensure_ascii=False)


def get_llm_from_env() -> LLMProvider:
    """从环境变量创建 LLM Provider"""
    provider = os.environ.get("SUPERCLAW_LLM", "mock")
    api_key = os.environ.get(f"{provider.upper()}_API_KEY", "")
    model = os.environ.get("SUPERCLAW_MODEL", "")
    base_url = os.environ.get(f"{provider.upper()}_BASE_URL", "")
    return LLMProvider(provider, api_key, model, base_url)


# ============================================================
# Pipe Process — 管理子进程
# ============================================================

class PipeProcess:
    """管理一个子进程 — 通过 stdin/stdout JSON 行通信"""

    def __init__(self, name: str, bin_path: str):
        self.name = name
        self.bin_path = bin_path
        self.process: Optional[subprocess.Popen] = None
        self.ready = False

    def start(self) -> bool:
        if not os.path.exists(self.bin_path):
            print(f"[{self.name}] ✗ 二进制不存在: {self.bin_path}")
            return False

        try:
            self.process = subprocess.Popen(
                [self.bin_path],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
            # 读取 ready 消息
            line = self.process.stdout.readline().strip()
            if line:
                try:
                    msg = json.loads(line)
                    if msg.get("status") == "ready":
                        self.ready = True
                        return True
                except json.JSONDecodeError:
                    pass
            # 即使 ready 消息异常也继续
            self.ready = True
            return True
        except Exception as e:
            print(f"[{self.name}] ✗ 启动失败: {e}")
            return False

    def send(self, cmd: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not self.process or not self.ready:
            return {"status": "error", "msg": f"{self.name} 未就绪"}

        try:
            line = json.dumps(cmd, ensure_ascii=False)
            self.process.stdin.write(line + "\n")
            self.process.stdin.flush()

            response = self.process.stdout.readline().strip()
            if response:
                try:
                    return json.loads(response)
                except json.JSONDecodeError:
                    return {"status": "error", "msg": f"JSON解析失败: {response}"}
            return {"status": "error", "msg": "空响应"}
        except (BrokenPipeError, OSError) as e:
            self.ready = False
            return {"status": "error", "msg": f"通信失败: {e}"}
        except Exception as e:
            return {"status": "error", "msg": str(e)}

    def stop(self):
        if self.process:
            try:
                self.process.stdin.close()
                self.process.terminate()
                self.process.wait(timeout=5)
            except Exception:
                self.process.kill()
            self.ready = False


# ============================================================
# 进化循环 — 集成 APEX
# ============================================================

SYSTEM_PROMPT = """你是 superclaw 的进化决策大脑。

你的任务：基于系统状态和 APEX 指标，决定下一步进化方向。

可用领域: 变异, 知识, 探索, 共进化, 评估
变异强度范围: 0.05 ~ 0.2

输出格式（严格 JSON）:
{"domain": "领域名", "change": 0.1, "reason": "简短理由"}
"""


class EvolutionLoop:
    """进化主循环 — 三层 + APEX"""

    def __init__(self, llm: LLMProvider, max_cycles: int = 5, verbose: bool = True):
        self.llm = llm
        self.max_cycles = max_cycles
        self.verbose = verbose
        self.c_core = PipeProcess("C Core", C_CORE_BIN)
        self.rust_engine = PipeProcess("Rust Engine", RUST_ENGINE_BIN)
        self.apex = ApexState()
        self.cycle_count = 0
        self.log: List[Dict[str, Any]] = []

    def start(self) -> bool:
        print("=" * 60)
        print("  🧬 superclaw 进化系统启动 (APEX 框架)")
        print("=" * 60)
        print(f"  LLM Provider: {self.llm.provider}")
        print(f"  C Core:       {C_CORE_BIN}")
        print(f"  Rust Engine:  {RUST_ENGINE_BIN}")
        print(f"  APEX State:   {APEX_DIR / 'apex-state.json'}")
        print()

        c_ok = self.c_core.start()
        r_ok = self.rust_engine.start()

        if c_ok:
            print("  [C Core] ✅ Ready")
        else:
            print("  [C Core] ❌ 启动失败")
            return False

        if r_ok:
            print("  [Rust]   ✅ Ready")
        else:
            print("  [Rust]   ❌ 启动失败")
            return False

        print()
        return True

    def run_cycle(self) -> Dict[str, Any]:
        self.cycle_count += 1
        print(f"{'─' * 60}")
        print(f"  🔄 进化循环 #{self.cycle_count}")
        print(f"{'─' * 60}")

        result = {
            "cycle": self.cycle_count,
            "timestamp": datetime.now().isoformat(),
            "steps": {},
        }

        # Step 1: C Core 心跳
        print("\n[1] C Core 心跳...")
        hb = self.c_core.send({"cmd": "heartbeat"})
        result["steps"]["heartbeat"] = hb
        c_data = hb.get("data", {})
        print(f"    → fitness={c_data.get('fitness', 0):.4f}, "
              f"balance={c_data.get('balance', 0):.4f}, "
              f"health={c_data.get('health', 0)}")

        # Step 2: 检测短板
        print("\n[2] 检测短板...")
        weakness = self.c_core.send({"cmd": "detect_weakness"})
        result["steps"]["detect_weakness"] = weakness
        w_data = weakness.get("data", {})
        weaknesses = w_data.get("weaknesses", "none")
        print(f"    → {w_data.get('count', 0)} 项: {weaknesses}")

        # Step 3: Rust Engine 状态
        print("\n[3] Rust Engine 状态...")
        r_status = self.rust_engine.send({"cmd": "status"})
        result["steps"]["rust_status"] = r_status
        r_data = r_status.get("data", {})
        print(f"    → genes={r_data.get('genes', 0)}, "
              f"balance={r_data.get('balance', 0):.4f}")

        # Step 4: Rust Engine 平衡详情
        print("\n[4] Rust Engine 平衡...")
        balance_resp = self.rust_engine.send({"cmd": "balance"})
        result["steps"]["balance"] = balance_resp
        balance_data = balance_resp.get("data", {})
        domains = balance_data.get("domains", {})
        print(f"    → domains: {json.dumps(domains, ensure_ascii=False)}")

        # Step 5: 计算 APEX 指标
        print("\n[5] 计算 APEX 指标...")
        self.apex.update(c_data, balance_data, mutations_delta=1)
        self.apex.save()
        apex_dict = self.apex.to_dict()
        result["steps"]["apex"] = apex_dict
        print(f"    → Φ_APEX = {apex_dict['phi_apex']}")
        print(f"    → base={apex_dict['base']}, ev={apex_dict['ev']}, "
              f"an={apex_dict['an']}, nv={apex_dict['nv']}, "
              f"harm={apex_dict['harm_rate']}")
        print(f"    → Tier: T{apex_dict['tier']}")

        # Step 6: LLM 分析决策
        print("\n[6] LLM 分析进化方向...")
        prompt = (
            f"当前系统状态: {json.dumps(c_data, ensure_ascii=False)}\n"
            f"检测到的短板: {weaknesses}\n"
            f"Rust 基因组: {json.dumps(r_data, ensure_ascii=False)}\n"
            f"平衡详情: {json.dumps(domains, ensure_ascii=False)}\n"
            f"APEX 指标: Φ={apex_dict['phi_apex']}, Tier=T{apex_dict['tier']}\n"
            f"请决定在哪个领域进化。"
        )
        llm_response = self.llm.call(prompt, system=SYSTEM_PROMPT)
        decision = self._parse_decision(llm_response)
        result["steps"]["llm_decision"] = {
            "raw": llm_response[:300],
            "parsed": decision,
        }
        print(f"    → LLM: {llm_response[:100]}")
        print(f"    → 决策: domain={decision['domain']}, change={decision['change']}")

        # Step 7: Rust Engine 变异
        print("\n[7] Rust Engine 生成基因...")
        mutate_result = self.rust_engine.send({
            "cmd": "mutate",
            "domain": decision["domain"],
            "change": decision["change"],
        })
        result["steps"]["mutate"] = mutate_result
        gene_id = ""
        if mutate_result.get("status") == "ok":
            gene_id = mutate_result.get("data", {}).get("gene_id", "")
            strength = mutate_result.get("data", {}).get("strength", 0)
            print(f"    → 基因: {gene_id}, 强度: {strength}")
        else:
            print(f"    → 变异失败: {mutate_result}")

        # Step 8: 评估基因
        print("\n[8] Rust Engine 评估...")
        if gene_id:
            eval_result = self.rust_engine.send({"cmd": "evaluate", "gene_id": gene_id})
            result["steps"]["evaluate"] = eval_result
            if eval_result.get("status") == "ok":
                score = eval_result.get("data", {}).get("score", 0)
                print(f"    → 评分: {score:.4f}")
            else:
                print(f"    → 评估失败: {eval_result}")

        # Step 9: C Core 记录进化
        print("\n[9] C Core 记录进化...")
        fitness_boost = decision["change"] * 0.5
        new_fitness = c_data.get("fitness", 1.0) + fitness_boost
        knowledge_delta = 1 if decision["domain"] == "知识" else 0
        record = self.c_core.send({
            "cmd": "record_evolution",
            "mutations": 1,
            "knowledge": knowledge_delta,
            "fitness": new_fitness,
        })
        result["steps"]["record"] = record
        print(f"    → 新 fitness: {new_fitness:.4f}")

        # Step 10: 最终状态
        print("\n[10] 最终状态...")
        final_c = self.c_core.send({"cmd": "status"})
        final_r = self.rust_engine.send({"cmd": "status"})
        result["final_state"] = {
            "c_core": final_c.get("data", {}),
            "rust": final_r.get("data", {}),
            "apex": self.apex.to_dict(),
        }
        print(f"    → C Core: fitness={final_c.get('data', {}).get('fitness', 0):.4f}")
        print(f"    → Rust:   genes={final_r.get('data', {}).get('genes', 0)}")
        print(f"    → APEX:   Φ={self.apex.phi:.6f} (T{self.apex.tier_level()})")

        # 保存日志
        self.log.append(result)
        self._save_log()

        return result

    def _parse_decision(self, llm_response: str) -> Dict[str, Any]:
        """从 LLM 响应中提取决策 JSON"""
        # 尝试从 ```json 代码块提取
        try:
            if "```json" in llm_response:
                json_str = llm_response.split("```json")[1].split("```")[0].strip()
                return json.loads(json_str)
            elif "```" in llm_response:
                json_str = llm_response.split("```")[1].split("```")[0].strip()
                return json.loads(json_str)
            else:
                start = llm_response.find("{")
                end = llm_response.rfind("}")
                if start >= 0 and end > start:
                    return json.loads(llm_response[start:end + 1])
        except (json.JSONDecodeError, IndexError):
            pass

        # 回退：基于关键词推断
        domain = "变异"
        change = 0.1
        if "知识" in llm_response or "knowledge" in llm_response.lower():
            domain = "知识"
        elif "探索" in llm_response or "explore" in llm_response.lower():
            domain = "探索"
        elif "平衡" in llm_response or "balance" in llm_response.lower():
            domain = "共进化"
        elif "评估" in llm_response or "evaluate" in llm_response.lower():
            domain = "评估"

        num_match = re.search(r'0?\.\d+', llm_response)
        if num_match:
            try:
                change = float(f"0.{num_match.group(1)}")
                change = max(0.03, min(change, 0.2))
            except ValueError:
                pass

        return {"domain": domain, "change": change, "reason": "fallback"}

    def _save_log(self):
        log_file = LOG_DIR / "evolution-log.jsonl"
        try:
            with open(log_file, "a") as f:
                f.write(json.dumps(self.log[-1], ensure_ascii=False) + "\n")
        except IOError as e:
            if self.verbose:
                print(f"    ⚠️ 日志保存失败: {e}")

    def run(self):
        if not self.start():
            return 1

        try:
            for _ in range(self.max_cycles):
                self.run_cycle()
                time.sleep(0.1)

            # 最终摘要
            print(f"\n{'=' * 60}")
            print(f"  ✅ 进化完成 ({self.cycle_count} 循环)")
            print(f"{'=' * 60}")
            final_c = self.c_core.send({"cmd": "status"})
            final_r = self.rust_engine.send({"cmd": "status"})
            print(f"  C Core: {json.dumps(final_c.get('data', {}), ensure_ascii=False, indent=2)}")
            print(f"  Rust:   {json.dumps(final_r.get('data', {}), ensure_ascii=False, indent=2)}")
            print(f"  APEX:   {json.dumps(self.apex.to_dict(), ensure_ascii=False, indent=2)}")
            print(f"\n  日志: {LOG_DIR / 'evolution-log.jsonl'}")
            print(f"  APEX: {APEX_DIR / 'apex-state.json'}")
            return 0

        finally:
            self.c_core.stop()
            self.rust_engine.stop()


# ============================================================
# CLI 入口
# ============================================================

def main():
    import argparse
    parser = argparse.ArgumentParser(description="superclaw Glue Layer + APEX")
    parser.add_argument("--cycles", "-n", type=int, default=5, help="进化循环数")
    parser.add_argument("--provider", "-p", default=None,
                       choices=["mock", "deepseek", "groq", "openrouter", "openai", "ollama"],
                       help="LLM Provider (默认从 SUPERCLAW_LLM 环境变量)")
    parser.add_argument("--quiet", "-q", action="store_true", help="安静模式")
    args = parser.parse_args()

    # 创建 LLM
    if args.provider:
        llm = LLMProvider(args.provider,
                          os.environ.get(f"{args.provider.upper()}_API_KEY", ""),
                          os.environ.get("SUPERCLAW_MODEL", ""),
                          os.environ.get(f"{args.provider.upper()}_BASE_URL", ""))
    else:
        llm = get_llm_from_env()

    loop = EvolutionLoop(llm, max_cycles=args.cycles, verbose=not args.quiet)
    sys.exit(loop.run())


if __name__ == "__main__":
    main()
