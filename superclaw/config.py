"""
superclaw 配置加载
支持：config.json + 环境变量
优先级：环境变量 > config.json > 默认

Schema 校验：未知 key 被忽略，必需字段缺失使用默认值。
"""
import json
import logging
import os
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, cast

logger = logging.getLogger(__name__)


# ---- JSON Schema（用于 load_config 校验）----
_CONFIG_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "llm": {
            "type": "object",
            "properties": {
                "provider": {"type": "string"},
                "model": {"type": "string"},
                "api_key": {"type": "string"},
                "base_url": {"type": "string"},
                "temperature": {"type": "number"},
                "max_tokens": {"type": "integer"},
                "timeout": {"type": "integer"},
            },
        },
        "session": {
            "type": "object",
            "properties": {
                "max_messages": {"type": "integer"},
                "path": {"type": "string"},
            },
        },
        "tools": {
            "type": "object",
            "properties": {
                "shell": {"type": "boolean"},
                "file": {"type": "boolean"},
                "web": {"type": "boolean"},
                "github": {"type": "boolean"},
                "think": {"type": "boolean"},
                "max_tool_iterations": {"type": "integer"},
            },
        },
        "workspace": {"type": "string"},
    },
}


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
        "github": False,
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
    github: bool = False  # L3+ 2026-06-19 加 github 工具开关 (github_clone/search/download/pip_install)
    max_tool_iterations: int = 5


@dataclass
class SuperclawConfig:
    llm: LLMConfig = field(default_factory=LLMConfig)
    session: SessionConfig = field(default_factory=SessionConfig)
    tools: ToolsConfig = field(default_factory=ToolsConfig)
    workspace: str = str(Path.cwd())


def _validate_config(cfg: Dict[str, Any], defaults: Dict[str, Any]) -> None:
    """校验配置值类型，拒绝非法类型（静默降级为默认值）"""
    # 工具类型定义（显式标注 value type 避免 mypy 推断为 object）
    type_specs: Dict[str, Dict[str, type]] = {
        "llm": {
            "provider": str, "model": str, "api_key": str,
            "base_url": str, "temperature": float,
            "max_tokens": int, "timeout": int,
        },
        "session": {"max_messages": int, "path": str},
        "tools": {
            "shell": bool, "file": bool, "web": bool, "github": bool,
            "think": bool, "max_tool_iterations": int,
        },
    }

    # workspace 是顶级字段，单独校验
    if "workspace" in cfg and not isinstance(cfg["workspace"], str):
        logger.warning(
            "[Config] 字段 workspace 类型错误 (%s)，使用默认值",
            type(cfg["workspace"]).__name__
        )
        cfg["workspace"] = defaults.get("workspace", str(Path.cwd()))

    def check_section(section: str, data: Dict[str, Any],
                      spec: Dict[str, type]) -> None:
        if not isinstance(data, dict):
            return
        for key, expected in spec.items():
            if key not in data:
                continue
            val = data[key]
            if not isinstance(val, expected):
                logger.warning(
                    "[Config] 字段 %s.%s 类型错误 (%s)，使用默认值",
                    section, key, type(val).__name__
                )
                section_defaults = defaults.get(section, {})
                data[key] = section_defaults.get(key) if isinstance(section_defaults, dict) else None

    for section, spec in type_specs.items():
        if section in cfg:
            check_section(section, cast(Dict[str, Any], cfg[section]), spec)


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

    # Schema 校验（拒绝非法类型，降级为默认值）
    _validate_config(cfg, DEFAULT_CONFIG)

    # 环境变量覆盖
    cfg = _env_override(cfg)

    # 构造对象
    return SuperclawConfig(
        llm=LLMConfig(**cfg["llm"]),
        session=SessionConfig(**cfg["session"]),
        tools=ToolsConfig(**cfg["tools"]),
        workspace=cfg["workspace"],
    )
