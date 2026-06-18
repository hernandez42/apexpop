"""
superclaw 配置加载
支持：config.json + 环境变量
优先级：环境变量 > config.json > 默认
"""
import json
import os
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


DEFAULT_CONFIG: Dict[str, Any] = {
    "llm": {
        "provider": "mock",
        "model": "mock-model",
        "api_key": "",
        "base_url": "",
        "temperature": 0.7,
        "max_tokens": 2048,
        "timeout": 60,
    },
    "session": {
        "max_messages": 50,
        "path": "~/.superclaw/sessions",
    },
    "tools": {
        "shell": True,
        "file": True,
        "web": False,
        "think": True,
        "max_tool_iterations": 5,
    },
    "workspace": str(Path.cwd()),
}


@dataclass
class LLMConfig:
    provider: str = "mock"
    model: str = "mock-model"
    api_key: str = ""
    base_url: str = ""
    temperature: float = 0.7
    max_tokens: int = 2048
    timeout: int = 60


@dataclass
class SessionConfig:
    max_messages: int = 50
    path: str = "~/.superclaw/sessions"


@dataclass
class ToolsConfig:
    shell: bool = True
    file: bool = True
    web: bool = False
    think: bool = True
    max_tool_iterations: int = 5


@dataclass
class SuperclawConfig:
    llm: LLMConfig = field(default_factory=LLMConfig)
    session: SessionConfig = field(default_factory=SessionConfig)
    tools: ToolsConfig = field(default_factory=ToolsConfig)
    workspace: str = str(Path.cwd())


def _env_override(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """从环境变量覆盖配置"""
    # LLM Provider: SUPERCLAW_PROVIDER
    provider = os.environ.get("SUPERCLAW_PROVIDER")
    if provider:
        cfg["llm"]["provider"] = provider

    # LLM Model: SUPERCLAW_MODEL
    model = os.environ.get("SUPERCLAW_MODEL")
    if model:
        cfg["llm"]["model"] = model

    # API Key — 从 PROVIDER_API_KEY 读取
    provider_name = cfg["llm"]["provider"].upper()
    api_key = os.environ.get(f"{provider_name}_API_KEY", os.environ.get("API_KEY", ""))
    if api_key:
        cfg["llm"]["api_key"] = api_key

    # Base URL
    base_url = os.environ.get(f"{provider_name}_BASE_URL")
    if base_url:
        cfg["llm"]["base_url"] = base_url

    return cfg


def load_config(config_path: Optional[str] = None) -> SuperclawConfig:
    """加载配置
    查找顺序：
    1. 显式传入的 config_path
    2. ./config.json
    3. ~/.superclaw/config.json
    4. 环境变量
    5. 默认值
    """
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))  # deep copy

    # 从文件加载
    candidates = []
    if config_path:
        candidates.append(Path(config_path))
    candidates.extend([
        Path.cwd() / "config.json",
        Path.cwd() / "config" / "config.json",
        Path.home() / ".superclaw" / "config.json",
    ])

    for path in candidates:
        if path.exists():
            try:
                with open(path) as f:
                    user_cfg = json.load(f)
                # 合并
                for key in cfg:
                    if key in user_cfg:
                        cfg[key].update(user_cfg[key])
                # 从显式路径找到就直接退出
                if config_path and str(Path(config_path).resolve()) == str(path.resolve()):
                    break
            except (json.JSONDecodeError, IOError):
                continue

    # 环境变量覆盖
    cfg = _env_override(cfg)

    # 构造对象
    return SuperclawConfig(
        llm=LLMConfig(**cfg["llm"]),
        session=SessionConfig(**cfg["session"]),
        tools=ToolsConfig(**cfg["tools"]),
        workspace=cfg["workspace"],
    )
