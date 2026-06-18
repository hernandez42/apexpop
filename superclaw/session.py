"""
superclaw 会话管理
简单、真实的多轮对话记忆 — 参考 nanobot 的 SessionManager
"""
import json
from pathlib import Path
from typing import Any, Dict, List, Optional


class Session:
    """单个会话 — 一个用户的对话历史"""

    def __init__(self, key: str, max_messages: int = 50):
        self.key = key
        self.max_messages = max_messages
        self.messages: List[Dict[str, Any]] = []
        self.metadata: Dict[str, Any] = {}

    def add(self, role: str, content: str, **extra) -> None:
        """添加一条消息
        role: 'user' | 'assistant' | 'system' | 'tool'
        """
        msg = {"role": role, "content": content}
        msg.update(extra)
        self.messages.append(msg)

        # 截断：保留 max_messages 条最新消息
        if len(self.messages) > self.max_messages:
            # 保留第一条 system prompt + 最新消息
            system = [m for m in self.messages if m["role"] == "system"][:1]
            recent = [m for m in self.messages if m["role"] != "system"][-(self.max_messages - 1):]
            self.messages = system + recent

    def to_messages(self, system_prompt: str = "") -> List[Dict[str, Any]]:
        """生成给 LLM 的消息列表"""
        msgs = []
        if system_prompt:
            msgs.append({"role": "system", "content": system_prompt})
        msgs.extend(self.messages)
        return msgs

    def last_user(self) -> str:
        for m in reversed(self.messages):
            if m["role"] == "user":
                return m["content"]
        return ""

    def __len__(self) -> int:
        return len(self.messages)


class SessionManager:
    """管理多个会话"""

    def __init__(self, storage_path: Optional[str] = None, max_messages: int = 50):
        self.max_messages = max_messages
        self.storage_path = Path(storage_path).expanduser() if storage_path else None
        if self.storage_path:
            self.storage_path.mkdir(parents=True, exist_ok=True)
        self._sessions: Dict[str, Session] = {}

    def get(self, key: str) -> Session:
        """获取或创建会话"""
        if key not in self._sessions:
            self._sessions[key] = Session(key, self.max_messages)
            # 尝试从磁盘加载
            self._load(key)
        return self._sessions[key]

    def save(self, key: str) -> bool:
        """保存会话到磁盘"""
        if not self.storage_path:
            return False
        session = self._sessions.get(key)
        if not session:
            return False
        try:
            path = self.storage_path / f"{key}.json"
            with open(path, "w", encoding="utf-8") as f:
                json.dump({
                    "key": key,
                    "messages": session.messages,
                    "metadata": session.metadata,
                }, f, ensure_ascii=False, indent=2)
            return True
        except Exception:
            return False

    def _load(self, key: str) -> bool:
        if not self.storage_path:
            return False
        path = self.storage_path / f"{key}.json"
        if not path.exists():
            return False
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            session = self._sessions[key]
            session.messages = data.get("messages", [])
            session.metadata = data.get("metadata", {})
            return True
        except Exception:
            return False

    def keys(self) -> List[str]:
        return sorted(self._sessions.keys())

    def clear(self, key: str) -> None:
        """清除指定会话"""
        if key in self._sessions:
            self._sessions[key] = Session(key, self.max_messages)
            # 清除磁盘文件
            if self.storage_path:
                path = self.storage_path / f"{key}.json"
                if path.exists():
                    try:
                        path.unlink()
                    except Exception:
                        pass
