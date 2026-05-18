#!/usr/bin/env python3
"""
统一守护进程 — 三层自进化闭环
C core + Rust + Python 各自独立自愈，又互相协作

职责：
1. 启动并监控 C core 和 Rust 引擎
2. 定期触发三层各自的心跳
3. 跨层健康监控：一层挂了帮它重启
4. 集成 Genome / GEP 基因共享 / 数字人 三大模块
5. 自进化闭环：心跳驱动进化，进化驱动历史，历史驱动共享
6. 注意力机制：动态资源分配 + 信息过滤
7. 三层协作升级：标准化接口 + 热插拔 + 动态重组
8. 自进化闭环增强：进化调度器 + 热拔插管理器 + 验证器
"""

import json
import os
import signal
import subprocess
import sys
import time
import hashlib
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Callable, Any
from dataclasses import dataclass, field

WORKSPACE = Path("/home/.openclaw/workspace")
CORE_DIR = WORKSPACE / "core-dna"
MEMORY_DIR = WORKSPACE / "memory"
LOG_FILE = MEMORY_DIR / "unified-daemon.log"
PID_FILE = MEMORY_DIR / "unified-daemon.pid"
EVOLUTION_LOG = MEMORY_DIR / "evolution-history.jsonl"

# === 配置 ===
HEARTBEAT_INTERVAL = 30   # 心跳间隔（秒）
HEALTH_CHECK_INTERVAL = 60  # 健康检查间隔（秒）
SELF_HEAL_INTERVAL = 120   # 自愈检查间隔（秒）

# === 自进化闭环配置（心跳轮次触发）===
GENOME_EVOLVE_ROUNDS = 30   # 每 30 轮心跳触发 Genome 进化
HISTORY_RECORD_ROUNDS = 100  # 每 100 轮记录进化历史
GENE_SHARE_ROUNDS = 500     # 每 500 轮触发基因共享

# === 自进化闭环增强配置 ===
EVOLUTION_CYCLE_ROUNDS = 200  # 每 200 轮触发完整进化循环（调度器→GPT→Code→审核→部署→验证）
VALIDATION_INTERVAL = 600     # 每 600 轮触发全量验证


def log(msg, level="INFO"):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] [{level}] {msg}"
    print(line, flush=True)
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


def record_evolution(event_type, data):
    """记录进化事件到 JSONL 历史文件"""
    entry = {
        "timestamp": int(time.time()),
        "type": event_type,
        **data,
    }
    with open(EVOLUTION_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# =============================================================================
# 注意力机制模块 — 模拟大脑前额叶的注意力分配
# =============================================================================

@dataclass
class AttentionFocus:
    """注意力焦点"""
    target: str          # 注意目标
    weight: float        # 权重 (0-1)
    priority: int        # 优先级 (0=最高)
    duration: float      # 持续时间（秒）
    last_updated: float  # 最后更新时间

@dataclass
class AttentionFilter:
    """信息过滤器"""
    pattern: str         # 过滤模式（关键词/正则）
    action: str          # "allow" 或 "block"
    priority: int        # 优先级
    hit_count: int = 0   # 命中次数

class AttentionMechanism:
    """
    注意力机制 — 模拟大脑前额叶的注意力分配
    
    类比大脑：
    - 前额叶皮层：执行控制（选择注意什么）
    - 前扣带皮层：冲突监控（检测注意力冲突）
    - 顶叶皮层：空间注意（分配认知资源）
    
    核心功能：
    1. 注意力权重计算 — 什么值得关注
    2. 动态资源分配 — 认知资源如何分配
    3. 信息过滤 — 过滤噪音，保留信号
    """
    
    def __init__(self):
        # 注意力焦点池
        self.focus_pool: List[AttentionFocus] = []
        self.max_focus = 7  # 米勒定律：7±2 个注意力焦点
        
        # 过滤器
        self.filters: List[AttentionFilter] = []
        
        # 资源分配
        self.total_resources = 1.0  # 总认知资源 (0-1)
        self.resource_usage = 0.0   # 当前使用量
        
        # 注意力统计
        self.switch_count = 0       # 注意力切换次数
        self.filter_hits = 0        # 过滤命中次数
        self.focus_history = []     # 注意力历史
        
    def compute_attention_weight(self, 
                                  relevance: float,
                                  novelty: float,
                                  urgency: float,
                                  familiarity: float) -> float:
        """
        计算注意力权重
        
        类比大脑：
        - 相关性(relevance)：与当前目标的关联度
        - 新颖性(novelty)：新异刺激自动捕获注意力
        - 紧迫性(urgency)：时间压力增加注意力权重
        - 熟悉度(familiarity)：熟悉的刺激降低注意力权重（习惯化）
        
        公式：W = α·R + β·N + γ·U - δ·F
        """
        # 参数（可动态调整）
        alpha = 0.4   # 相关性权重
        beta = 0.3    # 新颖性权重
        gamma = 0.2   # 紧迫性权重
        delta = 0.1   # 熟悉度惩罚
        
        weight = (alpha * relevance + 
                  beta * novelty + 
                  gamma * urgency - 
                  delta * familiarity)
        
        # 限制在 [0, 1]
        return max(0.0, min(1.0, weight))
    
    def allocate_resources(self, focus: AttentionFocus, available: float) -> float:
        """
        动态资源分配
        
        类比大脑：
        - 高优先级任务获得更多认知资源
        - 资源不足时，低优先级任务被抑制
        - 资源分配遵循"胜者通吃"原则
        """
        # 基础分配 = 权重 * 可用资源
        base = focus.weight * available
        
        # 优先级加成：高优先级获得更多
        priority_bonus = 1.0 + (10 - focus.priority) * 0.05
        
        allocated = base * priority_bonus
        allocated = min(allocated, available)  # 不超过可用量
        
        return allocated
    
    def add_focus(self, target: str, weight: float, priority: int, duration: float = 30.0):
        """添加注意力焦点"""
        # 检查是否已存在
        for f in self.focus_pool:
            if f.target == target:
                f.weight = weight
                f.priority = priority
                f.last_updated = time.time()
                return
        
        # 如果焦点池已满，移除最低优先级
        if len(self.focus_pool) >= self.max_focus:
            self.focus_pool.sort(key=lambda f: f.priority, reverse=True)
            removed = self.focus_pool.pop()  # 移除最低优先级
            log(f"[注意力] 移除焦点: {removed.target}", "DEBUG")
        
        # 添加新焦点
        self.focus_pool.append(AttentionFocus(
            target=target,
            weight=weight,
            priority=priority,
            duration=duration,
            last_updated=time.time()
        ))
        self.switch_count += 1
    
    def remove_focus(self, target: str):
        """移除注意力焦点"""
        self.focus_pool = [f for f in self.focus_pool if f.target != target]
    
    def update_weights(self, context: Dict[str, float]):
        """
        根据上下文更新所有焦点的权重
        
        context 格式：{
            "relevance_<target>": float,
            "novelty_<target>": float,
            ...
        }
        """
        for focus in self.focus_pool:
            relevance = context.get(f"relevance_{focus.target}", 0.5)
            novelty = context.get(f"novelty_{focus.target}", 0.3)
            urgency = context.get(f"urgency_{focus.target}", 0.2)
            familiarity = context.get(f"familiarity_{focus.target}", 0.1)
            
            focus.weight = self.compute_attention_weight(
                relevance, novelty, urgency, familiarity
            )
            focus.last_updated = time.time()
    
    def filter_information(self, info_type: str, content: str) -> tuple:
        """
        信息过滤
        
        返回 (should_allow, filtered_content)
        
        类比大脑：
        - 丘脑门控：过滤无关感觉信息
        - 前额叶抑制：主动抑制干扰信息
        """
        # 按优先级排序过滤器
        sorted_filters = sorted(self.filters, key=lambda f: f.priority)
        
        for f in sorted_filters:
            if f.pattern in content or f.pattern in info_type:
                f.hit_count += 1
                self.filter_hits += 1
                if f.action == "block":
                    return (False, "")
                elif f.action == "allow":
                    return (True, content)
        
        # 默认允许
        return (True, content)
    
    def add_filter(self, pattern: str, action: str, priority: int = 5):
        """添加过滤器"""
        self.filters.append(AttentionFilter(
            pattern=pattern,
            action=action,
            priority=priority
        ))
    
    def get_focus_summary(self) -> Dict[str, Any]:
        """获取注意力焦点摘要"""
        total_weight = sum(f.weight for f in self.focus_pool)
        return {
            "focus_count": len(self.focus_pool),
            "total_weight": total_weight,
            "switch_count": self.switch_count,
            "filter_hits": self.filter_hits,
            "top_focus": [
                {"target": f.target, "weight": f.weight, "priority": f.priority}
                for f in sorted(self.focus_pool, key=lambda x: x.weight, reverse=True)[:3]
            ]
        }
    
    def cleanup_stale(self, max_age: float = 60.0):
        """清理过期的注意力焦点"""
        now = time.time()
        before = len(self.focus_pool)
        self.focus_pool = [
            f for f in self.focus_pool
            if (now - f.last_updated) < max_age
        ]
        removed = before - len(self.focus_pool)
        if removed > 0:
            log(f"[注意力] 清理 {removed} 个过期焦点", "DEBUG")


# =============================================================================
# 三层协作模块 — 标准化接口 + 热插拔 + 动态重组
# =============================================================================

@dataclass
class LayerInterface:
    """层间标准化接口"""
    name: str
    layer_type: str       # "c_core", "rust", "python"
    status: str           # "active", "inactive", "error"
    last_heartbeat: float
    capabilities: List[str] = field(default_factory=list)
    error_count: int = 0
    process: Optional[subprocess.Popen] = None

class ThreeLayerCoordinator:
    """
    三层协作协调器
    
    类比大脑：
    - 前额叶(C core)：决策和控制
    - 海马体(Rust)：记忆存储和检索
    - 注意系统(Python)：资源分配和过滤
    
    核心功能：
    1. 标准化接口通信
    2. 模块热插拔支持
    3. 动态重组机制
    """
    
    def __init__(self):
        self.layers: Dict[str, LayerInterface] = {}
        self.message_queue: List[Dict] = []
        self.recovery_strategies: Dict[str, Callable] = {}
        self重组_history: List[Dict] = []
        
    def register_layer(self, name: str, layer_type: str, capabilities: List[str]):
        """注册一个层"""
        self.layers[name] = LayerInterface(
            name=name,
            layer_type=layer_type,
            status="inactive",
            last_heartbeat=0,
            capabilities=capabilities
        )
        log(f"[协作] 注册层: {name} ({layer_type})")
    
    def start_layer(self, name: str) -> bool:
        """启动一个层"""
        if name not in self.layers:
            return False
        
        layer = self.layers[name]
        
        if layer_type == "c_core":
            # 启动 C core
            c_core_bin = CORE_DIR / "c-core"
            if not c_core_bin.exists():
                log(f"C core 二进制不存在", "WARN")
                return False
            try:
                layer.process = subprocess.Popen(
                    [str(c_core_bin)],
                    cwd=str(CORE_DIR),
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True,
                )
                layer.status = "active"
                layer.last_heartbeat = time.time()
                log(f"[协作] C core 已启动 (PID: {layer.process.pid})")
                return True
            except Exception as e:
                log(f"C core 启动失败: {e}", "ERROR")
                return False
        
        elif layer_type == "rust":
            # 启动 Rust 引擎
            rust_bin = CORE_DIR / "rust-engine"
            if not rust_bin.exists():
                log(f"Rust 引擎不存在", "WARN")
                return False
            try:
                layer.process = subprocess.Popen(
                    [str(rust_bin)],
                    cwd=str(CORE_DIR),
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=False,
                )
                layer.status = "active"
                layer.last_heartbeat = time.time()
                log(f"[协作] Rust 引擎已启动 (PID: {layer.process.pid})")
                return True
            except Exception as e:
                log(f"Rust 引擎启动失败: {e}", "ERROR")
                return False
        
        return False
    
    def stop_layer(self, name: str):
        """停止一个层"""
        if name not in self.layers:
            return
        
        layer = self.layers[name]
        if layer.process:
            try:
                layer.process.terminate()
                layer.process.wait(timeout=5)
            except Exception:
                try:
                    layer.process.kill()
                except Exception:
                    pass
        layer.status = "inactive"
        layer.process = None
        log(f"[协作] 层已停止: {name}")
    
    def send_message(self, source: str, target: str, message: Dict) -> Optional[Dict]:
        """
        标准化接口通信
        
        消息格式：
        {
            "type": "request" | "response" | "event",
            "action": str,
            "payload": dict,
            "timestamp": float,
            "source": str,
            "target": str
        }
        """
        msg = {
            "type": "request",
            "source": source,
            "target": target,
            "timestamp": time.time(),
            **message
        }
        
        # 添加到队列
        self.message_queue.append(msg)
        
        # 如果目标是活跃层，直接发送
        if target in self.layers and self.layers[target].status == "active":
            layer = self.layers[target]
            if layer.process and layer.process.poll() is None:
                try:
                    if layer.layer_type == "rust":
                        layer.process.stdin.write((json.dumps(msg) + "\n").encode())
                        layer.process.stdin.flush()
                        # 读取响应
                        import select
                        if select.select([layer.process.stdout], [], [], 3)[0]:
                            raw = layer.process.stdout.readline()
                            return json.loads(raw.decode("utf-8", errors="replace"))
                    else:
                        layer.process.stdin.write(json.dumps(msg) + "\n")
                        layer.process.stdin.flush()
                except Exception as e:
                    log(f"[协作] 发送消息失败: {source} → {target}: {e}", "WARN")
                    layer.error_count += 1
        
        return None
    
    def hot_swap(self, old_name: str, new_name: str, new_layer_type: str) -> bool:
        """
        模块热插拔
        
        1. 停止旧模块
        2. 保存状态
        3. 启动新模块
        4. 恢复状态
        5. 更新路由
        """
        log(f"[协作] 🔌 热插拔: {old_name} → {new_name}")
        
        # 1. 停止旧模块
        self.stop_layer(old_name)
        
        # 2. 保存状态（如果有）
        old_state = None
        if old_name in self.layers:
            old_layer = self.layers[old_name]
            old_state = {
                "capabilities": old_layer.capabilities,
                "error_count": old_layer.error_count
            }
            del self.layers[old_name]
        
        # 3. 启动新模块
        capabilities = old_state["capabilities"] if old_state else ["basic"]
        self.register_layer(new_name, new_layer_type, capabilities)
        success = self.start_layer(new_name)
        
        if success:
            # 4. 记录重组历史
            self.重组_history.append({
                "timestamp": time.time(),
                "old": old_name,
                "new": new_name,
                "type": new_layer_type,
                "success": True
            })
            log(f"[协作] ✅ 热插拔成功: {new_name}")
        else:
            # 失败，尝试恢复旧模块
            log(f"[协作] ❌ 热插拔失败，尝试恢复 {old_name}", "WARN")
            if old_name:
                self.register_layer(old_name, 
                                   old_layer.layer_type if old_state else "unknown",
                                   old_state["capabilities"] if old_state else [])
                self.start_layer(old_name)
        
        return success
    
    def dynamic_reorganize(self):
        """
        动态重组机制
        
        根据系统状态自动调整三层架构：
        - 检测故障层
        - 重新分配职责
        - 优化资源分配
        """
        # 检测各层健康状况
        unhealthy = []
        for name, layer in self.layers.items():
            if layer.status == "active" and layer.process:
                if layer.process.poll() is not None:
                    unhealthy.append(name)
                    layer.status = "error"
                    log(f"[协作] ⚠️ 检测到故障层: {name}", "WARN")
        
        # 故障层处理
        for name in unhealthy:
            layer = self.layers[name]
            if layer.error_count < 3:
                # 尝试重启
                log(f"[协作] 🔄 尝试重启: {name}")
                self.stop_layer(name)
                time.sleep(1)
                self.start_layer(name)
            else:
                # 错误过多，标记为不可用
                layer.status = "inactive"
                log(f"[协作] ❌ 层 {name} 错误过多，标记为不可用", "ERROR")
        
        # 资源重新分配
        active_layers = [n for n, l in self.layers.items() if l.status == "active"]
        if active_layers:
            resource_per_layer = 1.0 / len(active_layers)
            log(f"[协作] 📊 资源分配: {len(active_layers)} 层活跃，每层 {resource_per_layer:.2f}")
    
    def get_system_status(self) -> Dict:
        """获取系统状态"""
        return {
            "layers": {
                name: {
                    "type": layer.layer_type,
                    "status": layer.status,
                    "error_count": layer.error_count,
                    "capabilities": layer.capabilities
                }
                for name, layer in self.layers.items()
            },
            "message_queue_size": len(self.message_queue),
            "reorganization_count": len(self.重组_history)
        }


# =============================================================================
# 统一守护进程
# =============================================================================

class UnifiedDaemon:
    def __init__(self):
        self.running = True
        self.c_core_proc = None
        self.rust_proc = None
        self.last_heartbeat = 0
        self.last_health_check = 0
        self.last_self_heal = 0
        self.heartbeat_round = 0  # 心跳轮次计数器
        self.stats = {
            "heartbeats": 0,
            "health_checks": 0,
            "self_heals": 0,
            "cross_layer_repairs": 0,
            "genome_evolutions": 0,
            "history_records": 0,
            "gene_shares": 0,
            "evolution_cycles": 0,
            "full_validations": 0,
        }
        
        # 注意力机制
        self.attention = AttentionMechanism()
        self._init_attention()
        
        # 三层协作
        self.coordinator = ThreeLayerCoordinator()
        self._init_coordinator()
        
        # 自进化闭环增强：调度器 + 热拔插 + 验证器
        self.evolution_dispatcher = None
        self.hot_swap = None
        self.validator = None
        self._init_evolution_modules()

    def _init_attention(self):
        """初始化注意力机制"""
        # 添加默认过滤器：过滤噪音信息
        self.attention.add_filter("DEBUG", "block", priority=8)
        self.attention.add_filter("噪声", "block", priority=7)
        self.attention.add_filter("ERROR", "allow", priority=2)
        self.attention.add_filter("WARN", "allow", priority=3)
        
        # 添加默认注意力焦点
        self.attention.add_focus("system_health", weight=0.8, priority=1)
        self.attention.add_focus("evolution", weight=0.6, priority=2)
        self.attention.add_focus("memory", weight=0.5, priority=3)

    def _init_coordinator(self):
        """初始化三层协作"""
        # 注册三层
        self.coordinator.register_layer("c_core", "c_core",
                                       ["decision", "identity", "metacognition"])
        self.coordinator.register_layer("rust_engine", "rust",
                                       ["mutation", "evaluation", "memory"])

        # 启动各层
        self.coordinator.start_layer("c_core")
        self.coordinator.start_layer("rust_engine")

    def _init_evolution_modules(self):
        """初始化自进化闭环增强模块"""
        try:
            sys.path.insert(0, str(CORE_DIR))
            from importlib import import_module as _im

            # 动态导入三个模块
            spec_ed = __import__("evolution-dispatcher")
            spec_hs = __import__("hot-swap")
            spec_ev = __import__("evolution-validator")

            self.evolution_dispatcher = spec_ed.EvolutionDispatcher()
            self.hot_swap = spec_hs.HotSwapManager()
            self.validator = spec_ev.EvolutionValidator()
            log("[进化闭环增强] 调度器 + 热拔插 + 验证器 已加载")
        except Exception as e:
            log(f"[进化闭环增强] 模块加载失败: {e}", "WARN")
            self.evolution_dispatcher = None
            self.hot_swap = None
            self.validator = None

    def signal_handler(self, signum, frame):
        log(f"收到信号 {signum}，安全退出...")
        self.running = False

    # ========== 三层进程管理 ==========

    def start_c_core(self):
        """启动 C core 作为子进程"""
        c_core_bin = CORE_DIR / "c-core"
        if not c_core_bin.exists():
            log("C core 二进制不存在，跳过", "WARN")
            return False
        try:
            self.c_core_proc = subprocess.Popen(
                [str(c_core_bin)],
                cwd=str(CORE_DIR),
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True,
            )
            log(f"C core 已启动 (PID: {self.c_core_proc.pid})")
            return True
        except Exception as e:
            log(f"C core 启动失败: {e}", "ERROR")
            return False

    def start_rust_engine(self):
        """启动 Rust 引擎"""
        rust_bin = CORE_DIR / "rust-engine"
        if not rust_bin.exists():
            log("Rust 引擎不存在，跳过", "WARN")
            return False
        try:
            self.rust_proc = subprocess.Popen(
                [str(rust_bin)],
                cwd=str(CORE_DIR),
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=False,
            )
            time.sleep(2)
            log(f"Rust 引擎已启动 (PID: {self.rust_proc.pid})")
            return True
        except Exception as e:
            log(f"Rust 引擎启动失败: {e}", "ERROR")
            return False

    def send_to_c_core(self, cmd):
        """向 C core 发送命令"""
        if not self.c_core_proc or self.c_core_proc.poll() is not None:
            return None
        try:
            self.c_core_proc.stdin.write(cmd + "\n")
            self.c_core_proc.stdin.flush()
            import select
            if select.select([self.c_core_proc.stdout], [], [], 2)[0]:
                raw = self.c_core_proc.stdout.readline()
                return raw.decode("utf-8", errors="replace").strip()
        except Exception as e:
            log(f"C core 通信失败: {e}", "WARN")
        return None

    def send_to_rust(self, json_cmd):
        """向 Rust 引擎发送 JSON 命令"""
        if not self.rust_proc or self.rust_proc.poll() is not None:
            return None
        try:
            self.rust_proc.stdin.write((json_cmd + "\n").encode("utf-8"))
            self.rust_proc.stdin.flush()
            import select
            if select.select([self.rust_proc.stdout], [], [], 3)[0]:
                raw = self.rust_proc.stdout.readline()
                return raw.decode("utf-8", errors="replace").strip()
        except Exception as e:
            log(f"Rust 通信失败: {e}", "WARN")
        return None

    def check_process_health(self, name, proc):
        """检查进程是否存活"""
        if proc is None:
            return False
        if proc.poll() is not None:
            log(f"{name} 进程已退出 (code: {proc.returncode})", "ERROR")
            return False
        return True

    # ========== 核心循环 ==========

    def heartbeat(self):
        """三层心跳 + 自进化闭环驱动"""
        now = time.time()
        if now - self.last_heartbeat < HEARTBEAT_INTERVAL:
            return
        self.last_heartbeat = now
        self.heartbeat_round += 1
        self.stats["heartbeats"] += 1

        # 注意力机制：更新焦点权重
        self.attention.update_weights({
            "relevance_system_health": 0.9 if self.heartbeat_round % 10 == 0 else 0.3,
            "novelty_system_health": 0.1,
            "urgency_system_health": 0.5 if self.heartbeat_round % 10 == 0 else 0.1,
            "familiarity_system_health": 0.3,
            "relevance_evolution": 0.7,
            "novelty_evolution": 0.4,
            "urgency_evolution": 0.2,
            "familiarty_evolution": 0.2,
        })
        
        # 清理过期焦点
        self.attention.cleanup_stale(max_age=120)

        # C core 心跳
        if self.check_process_health("C core", self.c_core_proc):
            resp = self.send_to_c_core("status")
            if resp:
                log(f"[轮次 {self.heartbeat_round}] C core: {resp[:80]}")
        else:
            log("C core 挂了，重启...", "WARN")
            self.start_c_core()
            self.stats["cross_layer_repairs"] += 1

        # Rust 心跳
        if self.check_process_health("Rust", self.rust_proc):
            resp = self.send_to_rust('{"cmd":"heartbeat"}')
            if resp:
                log(f"[轮次 {self.heartbeat_round}] Rust: {resp[:80]}")
        else:
            log("Rust 挂了，重启...", "WARN")
            self.start_rust_engine()
            self.stats["cross_layer_repairs"] += 1

        # 三层协作：动态重组检查
        if self.heartbeat_round % 20 == 0:
            self.coordinator.dynamic_reorganize()

        # ===== 自进化闭环：心跳轮次触发 =====
        # 每 30 轮触发 Genome 进化
        if self.heartbeat_round % GENOME_EVOLVE_ROUNDS == 0:
            self._trigger_genome_evolution()

        # 每 100 轮记录进化历史
        if self.heartbeat_round % HISTORY_RECORD_ROUNDS == 0:
            self._record_evolution_history()

        # 每 500 轮触发基因共享
        if self.heartbeat_round % GENE_SHARE_ROUNDS == 0:
            self._trigger_gene_sharing()
        
        # 每 50 轮：注意力机制报告
        if self.heartbeat_round % 50 == 0:
            focus_summary = self.attention.get_focus_summary()
            log(f"[注意力] 焦点: {focus_summary['focus_count']} 个, "
                f"切换: {focus_summary['switch_count']} 次, "
                f"过滤: {focus_summary['filter_hits']} 次")

        # === 自进化闭环增强：完整进化循环（每 200 轮）===
        if self.heartbeat_round % EVOLUTION_CYCLE_ROUNDS == 0:
            self._trigger_evolution_cycle()

        # === 全量验证（每 600 轮）===
        if self.heartbeat_round % VALIDATION_INTERVAL == 0:
            self._trigger_full_validation()

    def health_check(self):
        """跨层健康检查"""
        now = time.time()
        if now - self.last_health_check < HEALTH_CHECK_INTERVAL:
            return
        self.last_health_check = now
        self.stats["health_checks"] += 1

        critical_files = [
            WORKSPACE / "SOUL.md",
            WORKSPACE / "AGENTS.md",
        ]
        for f in critical_files:
            if not f.exists():
                log(f"关键文件缺失: {f.name}", "ERROR")

        # 检查基因库一致性
        evo_genes = MEMORY_DIR / "evolution-genes.json"
        if evo_genes.exists():
            try:
                with open(evo_genes) as f:
                    data = json.load(f)
                count = len(data) if isinstance(data, list) else len(data.get("genes", []))
                log(f"进化基因库: {count} 个")
            except Exception:
                log("进化基因库格式异常", "WARN")

    def self_heal_check(self):
        """触发各层自愈"""
        now = time.time()
        if now - self.last_self_heal < SELF_HEAL_INTERVAL:
            return
        self.last_self_heal = now
        self.stats["self_heals"] += 1

        if self.check_process_health("Rust", self.rust_proc):
            resp = self.send_to_rust('{"cmd":"self_heal"}')
            if resp:
                try:
                    result = json.loads(resp)
                    report = result.get("report", "")
                    if "无异常" not in report:
                        log(f"Rust 自愈: {report}")
                except Exception:
                    pass

    # ========== 自进化闭环：Genome 进化（每 30 轮）==========

    def _trigger_genome_evolution(self):
        """触发 Genome 进化：通过 Rust 引擎评估+变异"""
        log(f"🧬 [轮次 {self.heartbeat_round}] 触发 Genome 进化")
        self.stats["genome_evolutions"] += 1

        # 尝试通过 Rust 引擎进化
        if self.check_process_health("Rust", self.rust_proc):
            try:
                resp = self.send_to_rust('{"cmd":"status"}')
                if resp:
                    data = json.loads(resp)
                    gene_count = data.get("gene_count", 0)
                    balance = data.get("balance", 0)
                    log(f"  Rust 状态: 基因={gene_count} 平衡度={balance:.3f}")

                    # 触发变异
                    if gene_count < 10:
                        resp2 = self.send_to_rust('{"cmd":"mutate","domain":"auto","change":0.2}')
                        if resp2:
                            log(f"  变异完成: {resp2[:100]}")
            except Exception as e:
                log(f"  Rust 进化异常: {e}", "WARN")

        # 通过 Python 模块进化
        try:
            result = subprocess.run(
                ["python3", str(CORE_DIR / "self-evolve.py")],
                capture_output=True, text=True, timeout=15,
            )
            if result.returncode == 0 and result.stdout.strip():
                data = json.loads(result.stdout.strip())
                if data.get("improved"):
                    log(f"  Python 进化: {data['weakest']} {data.get('old_avg',0):.3f}→{data.get('new_avg',0):.3f}")
        except Exception as e:
            log(f"  Python 进化异常: {e}", "WARN")

        record_evolution("genome_evolve", {
            "round": self.heartbeat_round,
            "genome_evolutions": self.stats["genome_evolutions"],
        })

    # ========== 自进化闭环：记录进化历史（每 100 轮）==========

    def _record_evolution_history(self):
        """记录进化历史快照：各层状态 + 基因库统计"""
        log(f"📜 [轮次 {self.heartbeat_round}] 记录进化历史")
        self.stats["history_records"] += 1

        history_entry = {
            "round": self.heartbeat_round,
            "timestamp": int(time.time()),
            "stats": self.stats.copy(),
            "layers": {},
            "attention": self.attention.get_focus_summary(),
            "coordinator": self.coordinator.get_system_status(),
        }

        # C core 状态
        if self.check_process_health("C core", self.c_core_proc):
            resp = self.send_to_c_core("status")
            history_entry["layers"]["c_core"] = resp[:200] if resp else "无响应"

        # Rust 状态
        if self.check_process_health("Rust", self.rust_proc):
            resp = self.send_to_rust('{"cmd":"status"}')
            try:
                history_entry["layers"]["rust"] = json.loads(resp) if resp else {}
            except Exception:
                history_entry["layers"]["rust"] = resp[:200] if resp else "无响应"

        # GEP 统计
        try:
            sys.path.insert(0, str(CORE_DIR))
            from gene_sharing import GeneSharingProtocol
            gep = GeneSharingProtocol()
            history_entry["layers"]["gep"] = gep.stats()
        except Exception as e:
            history_entry["layers"]["gep"] = {"error": str(e)}

        # 数字人统计
        try:
            from digital_human import DigitalHuman
            dh = DigitalHuman()
            history_entry["layers"]["digital_human"] = dh.get_stats()
        except Exception as e:
            history_entry["layers"]["digital_human"] = {"error": str(e)}

        record_evolution("history_snapshot", history_entry)
        log(f"  历史快照已保存 (共 {self.stats['history_records']} 次)")

    # ========== 自进化闭环：基因共享（每 500 轮）==========

    def _trigger_gene_sharing(self):
        """触发 GEP 基因共享：同步公共库 + 评估共享质量"""
        log(f"🔗 [轮次 {self.heartbeat_round}] 触发基因共享")
        self.stats["gene_shares"] += 1

        try:
            sys.path.insert(0, str(CORE_DIR))
            from gene_sharing import GeneSharingProtocol
            gep = GeneSharingProtocol()

            # 获取统计
            stats = gep.stats()
            log(f"  GEP 状态: 私有={stats['private_count']} 公共={stats['public_count']} "
                f"均分={stats['public_avg_score']:.1f}")

            # 清理低分基因
            removed = gep.cleanup()
            if removed > 0:
                log(f"  清理了 {removed} 个低分基因")

            # 尝试合并优质基因
            merged = gep.merge_pool(count=3)
            if merged:
                log(f"  合并了 {len(merged)} 个基因模板")
        except Exception as e:
            log(f"  基因共享异常: {e}", "WARN")

        record_evolution("gene_sharing", {
            "round": self.heartbeat_round,
            "gene_shares": self.stats["gene_shares"],
        })

    # ========== 自进化闭环增强：完整进化循环 ==========

    def _trigger_evolution_cycle(self):
        """
        触发完整进化循环：检测短板 → GPT方案 → Code生成 → LongCat审核 → 部署 → 验证

        这是自进化闭环的核心增强能力，让系统能够：
        1. 主动发现短板
        2. 用 GPT 生成最优方案
        3. 用 Code LLM 生成代码
        4. 用 LongCat 审核代码
        5. 通过热拔插管理器安全部署
        6. 通过验证器验证进化效果
        7. 记录进化历史
        """
        if not self.evolution_dispatcher:
            log("[进化闭环增强] 调度器未加载，跳过进化循环", "WARN")
            return

        log(f"🧬 [轮次 {self.heartbeat_round}] 触发完整进化循环")
        self.stats["genome_evolutions"] += 1

        try:
            result = self.evolution_dispatcher.run_evolution_cycle()
            weaknesses = result.get("weaknesses", [])
            deployments = result.get("deployments", [])
            success = result.get("success", False)

            log(f"  进化循环完成: 短板={len(weaknesses)}, "
                f"部署={len(deployments)}, 成功={success}")

            if deployments:
                for d in deployments:
                    status = "✅" if d.get("success") else "❌"
                    log(f"  {status} 部署: {d.get('file', '?')}")

        except Exception as e:
            log(f"  进化循环异常: {e}", "ERROR")

        record_evolution("evolution_cycle", {
            "round": self.heartbeat_round,
            "genome_evolutions": self.stats["genome_evolutions"],
        })

    # ========== 自进化闭环增强：全量验证 ==========

    def _trigger_full_validation(self):
        """触发全量代码验证"""
        if not self.validator:
            log("[进化闭环增强] 验证器未加载，跳过验证", "WARN")
            return

        log(f"📊 [轮次 {self.heartbeat_round}] 触发全量验证")

        try:
            reports = self.validator.validate_directory()
            passed = sum(1 for r in reports.values() if r.overall_passed)
            total = len(reports)
            avg_score = sum(r.overall_score for r in reports.values()) / total if total else 0

            log(f"  全量验证完成: {passed}/{total} 通过, 平均评分 {avg_score:.2f}")

            # 如果有文件验证失败，记录警告
            failed = [name for name, r in reports.items() if not r.overall_passed]
            if failed:
                log(f"  ⚠️ 验证失败的文件: {', '.join(failed)}", "WARN")

        except Exception as e:
            log(f"  全量验证异常: {e}", "ERROR")

    # ========== 其他定期检查 ==========

    def self_evolution_check(self):
        """Python 自进化（补充 Rust 进化）"""
        now = time.time()
        if now - getattr(self, '_last_evolution', 0) < 300:
            return
        self._last_evolution = now
        try:
            result = subprocess.run(
                ["python3", str(CORE_DIR / "self-evolve.py")],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0 and result.stdout.strip():
                data = json.loads(result.stdout.strip())
                if data.get("improved"):
                    log(f"🧬 自进化: {data['weakest']} {data.get('old_avg',0):.3f}→{data.get('new_avg',0):.3f}")
        except Exception as e:
            log(f"自进化异常: {e}", "WARN")

    # ========== 主循环 ==========

    def run(self):
        signal.signal(signal.SIGTERM, self.signal_handler)
        signal.signal(signal.SIGINT, self.signal_handler)

        log("=== 统一守护进程启动 ===")
        log(f"心跳间隔: {HEARTBEAT_INTERVAL}s")
        log(f"自进化闭环: Genome={GENOME_EVOLVE_ROUNDS}轮 历史={HISTORY_RECORD_ROUNDS}轮 共享={GENE_SHARE_ROUNDS}轮")
        log(f"注意力机制: 最大焦点 {self.attention.max_focus} 个")
        log(f"三层协作: 已注册 {len(self.coordinator.layers)} 层")

        MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        with open(PID_FILE, "w") as f:
            f.write(str(os.getpid()))

        self.start_c_core()
        self.start_rust_engine()

        while self.running:
            try:
                self.heartbeat()
                self.health_check()
                self.self_heal_check()
                self.self_evolution_check()
                time.sleep(5)
            except KeyboardInterrupt:
                break
            except Exception as e:
                log(f"主循环异常: {e}", "ERROR")
                time.sleep(10)

        self.shutdown()

    def shutdown(self):
        log("正在关闭...")
        if self.c_core_proc:
            self.c_core_proc.terminate()
            self.c_core_proc.wait(timeout=5)
            log("C core 已关闭")
        if self.rust_proc:
            self.rust_proc.terminate()
            self.rust_proc.wait(timeout=5)
            log("Rust 引擎已关闭")
        if PID_FILE.exists():
            PID_FILE.unlink()
        log(f"统计: {json.dumps(self.stats)}")
        log(f"注意力: {json.dumps(self.attention.get_focus_summary())}")
        log(f"协作重组: {len(self.coordinator.重组_history)} 次")
        # 进化闭环增强统计
        if self.evolution_dispatcher:
            ed_stats = self.evolution_dispatcher.get_stats()
            log(f"进化调度器: {json.dumps(ed_stats)}")
        if self.hot_swap:
            hs_stats = self.hot_swap.get_stats()
            log(f"热拔插管理器: {json.dumps(hs_stats)}")
        log("=== 守护进程已退出 ===")


if __name__ == "__main__":
    daemon = UnifiedDaemon()
    daemon.run()
