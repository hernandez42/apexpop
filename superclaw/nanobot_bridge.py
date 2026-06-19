"""nanobot 跨机桥接层 —— superclaw 仓与 nanobot 仓的双向同步

角色:
    - 跨机 GET 同步: 127.0.0.1:18790/...
    - 反向桥: 读取 nanobot/_cross_host_inbox.json
    - 9 子系统同步: DriveSystem / Cortex / ReflectionEngine /
                     EvolutionPipeline / MemorySystem / GoalEngine /
                     KnowledgeBase / Perception / SelfModel
    - 落盘: nanobot_sync.jsonl

此模块设计为"可用即启动"，任何失败都不影响 superclaw 主流程。
"""

from __future__ import annotations

import json
import socket
import time
import urllib.request
import urllib.error
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ============================================================
# 常量 / 默认配置
# ============================================================

NANOBOT_HOST_DEFAULT = "127.0.0.1"
NANOBOT_PORT_DEFAULT = 18790
NANOBOT_TIMEOUT_DEFAULT = 2.0  # 秒

# 9 子系统（nanobot 仓顶层设计）
NANOBOT_SUBSYSTEMS: List[str] = [
    "DriveSystem",
    "Cortex",
    "ReflectionEngine",
    "EvolutionPipeline",
    "MemorySystem",
    "GoalEngine",
    "KnowledgeBase",
    "Perception",
    "SelfModel",
]


@dataclass
class NanobotStatus:
    """nanobot 仓健康度快照"""
    reachable: bool
    latency_ms: float
    inbox_items: int
    sync_total: int
    subsystems_detected: List[str] = field(default_factory=list)
    error: Optional[str] = None


@dataclass
class SyncEntry:
    """nanobot_sync.jsonl 的单条记录"""
    ts: float
    direction: str            # "pull_from_nanobot" / "push_to_nanobot"
    subsystem: str
    payload_len: int
    ok: bool
    error: Optional[str] = None


# ============================================================
# HTTP 工具
# ============================================================

def _http_get_json(url: str, timeout: float = NANOBOT_TIMEOUT_DEFAULT) -> Tuple[bool, Optional[Any], Optional[str]]:
    """极简 GET JSON。失败不抛异常，返回 (ok, data_or_None, error_msg)。"""
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "superclaw-nanobot-bridge/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")
            return True, json.loads(raw) if raw.strip() else {}, None
    except (urllib.error.URLError, socket.timeout, ConnectionError, OSError) as e:
        return False, None, f"network: {type(e).__name__}: {e}"
    except json.JSONDecodeError as e:
        return False, None, f"json_decode: {e}"
    except Exception as e:  # pragma: no cover - 防御性兜底
        return False, None, f"unknown: {type(e).__name__}: {e}"


# ============================================================
# NanobotBridge: 对外核心
# ============================================================

class NanobotBridge:
    """superclaw <-> nanobot 跨机桥。

    典型用法:
        bridge = NanobotBridge(workspace="./")
        status = bridge.status()
        if status.reachable:
            inbox = bridge.read_inbox()
            for sub in NANOBOT_SUBSYSTEMS:
                bridge.pull_subsystem(sub)
    """

    def __init__(self,
                 workspace: str = ".",
                 host: str = NANOBOT_HOST_DEFAULT,
                 port: int = NANOBOT_PORT_DEFAULT,
                 timeout: float = NANOBOT_TIMEOUT_DEFAULT,
                 nanobot_local_dir: Optional[str] = None):
        self.workspace = Path(workspace)
        self.host = host
        self.port = int(port)
        self.timeout = float(timeout)
        self.sync_path = self.workspace / "nanobot_sync.jsonl"
        # 本地 nanobot 仓位置：优先显式路径，其次 workspace 下的 ../nanobot
        if nanobot_local_dir:
            self.nanobot_dir = Path(nanobot_local_dir)
        else:
            self.nanobot_dir = self.workspace.parent / "nanobot"
        self.inbox_path = self.nanobot_dir / "_cross_host_inbox.json"

    # --------------------------------------------------------
    # 可用性探测
    # --------------------------------------------------------
    def is_reachable(self) -> bool:
        """HTTP /ping 或 /status 探测；失败就 False，不抛异常"""
        t0 = time.time()
        ok, _, _ = _http_get_json(f"http://{self.host}:{self.port}/status", self.timeout)
        if ok:
            return True
        # 次级探测: /ping
        ok2, _, _ = _http_get_json(f"http://{self.host}:{self.port}/ping", self.timeout)
        if ok2:
            return True
        # 再次级: inbox 文件是否存在（离线模式）
        return self.inbox_path.exists()

    # --------------------------------------------------------
    # 状态 / 健康度
    # --------------------------------------------------------
    def status(self) -> NanobotStatus:
        t0 = time.time()
        reachable, data, err = _http_get_json(
            f"http://{self.host}:{self.port}/status", self.timeout
        )
        lat_ms = int((time.time() - t0) * 1000)

        inbox_count = 0
        try:
            if self.inbox_path.exists():
                inbox_data = json.loads(self.inbox_path.read_text(encoding="utf-8", errors="ignore"))
                if isinstance(inbox_data, list):
                    inbox_count = len(inbox_data)
                elif isinstance(inbox_data, dict) and "items" in inbox_data:
                    items = inbox_data["items"]
                    inbox_count = len(items) if isinstance(items, list) else 0
        except Exception:
            pass

        subs: List[str] = []
        if reachable and isinstance(data, dict):
            for sub in NANOBOT_SUBSYSTEMS:
                if sub in data or sub.lower() in {k.lower(): k for k in data.keys()}:
                    subs.append(sub)

        sync_total = 0
        try:
            if self.sync_path.exists():
                sync_total = sum(1 for _ in open(self.sync_path, "r", encoding="utf-8", errors="ignore") if _.strip())
        except Exception:
            pass

        return NanobotStatus(
            reachable=reachable,
            latency_ms=lat_ms,
            inbox_items=inbox_count,
            sync_total=sync_total,
            subsystems_detected=subs,
            error=err,
        )

    # --------------------------------------------------------
    # 反向桥: 读 _cross_host_inbox.json
    # --------------------------------------------------------
    def read_inbox(self, limit: int = 20) -> List[Dict[str, Any]]:
        """读取 nanobot _cross_host_inbox.json。总是返回 list，从不抛异常。"""
        if not self.inbox_path.exists():
            return []
        try:
            raw = self.inbox_path.read_text(encoding="utf-8", errors="ignore")
            data = json.loads(raw) if raw.strip() else []
            if isinstance(data, list):
                return list(data[-limit:])
            if isinstance(data, dict):
                items = data.get("items", data.get("messages", []))
                if isinstance(items, list):
                    return list(items[-limit:])
                return [data]
        except Exception:
            pass
        return []

    def write_inbox(self, items: List[Dict[str, Any]]) -> bool:
        """把 superclaw 想发给 nanobot 的消息写进 inbox。"""
        try:
            self.inbox_path.parent.mkdir(parents=True, exist_ok=True)
            existing = self.read_inbox()
            merged = existing + items
            self.inbox_path.write_text(
                json.dumps(merged[-500:], ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            return True
        except Exception:
            return False

    # --------------------------------------------------------
    # 跨机 GET: 拉取 9 子系统
    # --------------------------------------------------------
    def pull_subsystem(self, subsystem: str) -> Tuple[bool, Optional[Dict[str, Any]], Optional[str]]:
        """GET /subsystem/{name} 。失败时尝试回落到本地文件读取。"""
        ok, data, err = _http_get_json(
            f"http://{self.host}:{self.port}/subsystem/{subsystem}", self.timeout
        )
        if ok:
            payload = data if isinstance(data, dict) else {"raw": data}
            self._append_sync(SyncEntry(
                ts=time.time(), direction="pull_from_nanobot", subsystem=subsystem,
                payload_len=len(json.dumps(payload, ensure_ascii=False)), ok=True,
            ))
            return True, payload, None

        # 回落: 本地 nanobot 目录下是否有对应的 .json 文件
        fallback = self.nanobot_dir / f"{subsystem}.json"
        if fallback.exists():
            try:
                payload = json.loads(fallback.read_text(encoding="utf-8", errors="ignore"))
                self._append_sync(SyncEntry(
                    ts=time.time(), direction="pull_from_nanobot_local",
                    subsystem=subsystem,
                    payload_len=len(json.dumps(payload, ensure_ascii=False)), ok=True,
                ))
                return True, payload, None
            except Exception as e2:
                return False, None, f"fallback_fail: {e2}"
        # 第二次回落: subsystem 目录下的任意文件
        fallback_dir = self.nanobot_dir / subsystem
        if fallback_dir.exists():
            summary: Dict[str, Any] = {"source": str(fallback_dir), "files": []}
            for f in sorted(fallback_dir.rglob("*"))[:20]:
                if f.is_file():
                    summary["files"].append({"name": str(f.relative_to(self.nanobot_dir)),
                                             "size": f.stat().st_size})
            self._append_sync(SyncEntry(
                ts=time.time(), direction="pull_from_nanobot_dir",
                subsystem=subsystem,
                payload_len=len(json.dumps(summary, ensure_ascii=False)), ok=True,
            ))
            return True, summary, None

        self._append_sync(SyncEntry(
            ts=time.time(), direction="pull_from_nanobot", subsystem=subsystem,
            payload_len=0, ok=False, error=err,
        ))
        return False, None, err

    def pull_all_subsystems(self) -> Dict[str, Any]:
        """一次性拉取 9 子系统快照，返回 {subsystem: {ok, payload_len, error}}"""
        summary: Dict[str, Any] = {"total": 0, "ok": 0, "failed": 0, "subsystems": {}}
        for sub in NANOBOT_SUBSYSTEMS:
            ok, payload, err = self.pull_subsystem(sub)
            summary["total"] += 1
            summary["ok"] += (1 if ok else 0)
            summary["failed"] += (0 if ok else 1)
            summary["subsystems"][sub] = {
                "ok": ok,
                "payload_len": len(json.dumps(payload, ensure_ascii=False)) if payload else 0,
                "error": err,
            }
        return summary

    # --------------------------------------------------------
    # push: superclaw → nanobot
    # --------------------------------------------------------
    def push_event(self, subsystem: str, payload: Dict[str, Any]) -> bool:
        """把 superclaw 的事件推送到 nanobot。
        先尝试 POST（若 nanobot 提供 /event HTTP）；失败则写入 inbox 文件。
        """
        url = f"http://{self.host}:{self.port}/event/{subsystem}"
        try:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            req = urllib.request.Request(
                url, data=body,
                headers={"Content-Type": "application/json",
                         "User-Agent": "superclaw-nanobot-bridge/1.0"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                ok = 200 <= resp.status < 300
                self._append_sync(SyncEntry(
                    ts=time.time(), direction="push_to_nanobot_http",
                    subsystem=subsystem, payload_len=len(body), ok=ok,
                ))
                return ok
        except Exception:
            pass
        # 回落: 文件 inbox
        ok_file = self.write_inbox([{
            "ts": time.time(),
            "source": "superclaw",
            "subsystem": subsystem,
            "payload": payload,
        }])
        self._append_sync(SyncEntry(
            ts=time.time(), direction="push_to_nanobot_inbox",
            subsystem=subsystem,
            payload_len=len(json.dumps(payload, ensure_ascii=False)),
            ok=ok_file,
        ))
        return ok_file

    # --------------------------------------------------------
    # 落盘: nanobot_sync.jsonl
    # --------------------------------------------------------
    def _append_sync(self, entry: SyncEntry) -> None:
        try:
            self.sync_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.sync_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(asdict(entry), ensure_ascii=False) + "\n")
        except Exception:
            pass

    def recent_sync(self, limit: int = 20) -> List[Dict[str, Any]]:
        if not self.sync_path.exists():
            return []
        out: List[Dict[str, Any]] = []
        try:
            with open(self.sync_path, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        out.append(json.loads(line))
                    except Exception:
                        continue
        except Exception:
            pass
        return out[-limit:]


# ============================================================
# 便捷构造
# ============================================================

def get_nanobot_bridge(workspace: str = ".", **kwargs) -> NanobotBridge:
    return NanobotBridge(workspace=workspace, **kwargs)


if __name__ == "__main__":  # pragma: no cover
    b = get_nanobot_bridge()
    print(b.status())
    print("inbox:", len(b.read_inbox()))
    print("9 subsystems pull:", b.pull_all_subsystems()["ok"], "/",
          b.pull_all_subsystems()["total"])
