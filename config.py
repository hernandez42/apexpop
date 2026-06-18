#!/usr/bin/env python3
"""
superclaw 统一配置系统
所有路径、LLM Provider、进化参数都从这里读取
"""

import json
import os
from pathlib import Path
from typing import Dict, Any

# 项目根目录：自动检测
SUPERCLAW_ROOT = Path(__file__).parent.resolve()
CORE_DNA_DIR = SUPERCLAW_ROOT / "core-dna"
MEMORY_DIR = SUPERCLAW_ROOT / "memory"
CONFIG_DIR = SUPERCLAW_ROOT / "config"
LOG_DIR = SUPERCLAW_ROOT / "logs"

for d in [MEMORY_DIR, CONFIG_DIR, LOG_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# 默认配置
DEFAULT_CONFIG: Dict[str, Any] = {
    "project": {
        "name": "superclaw",
        "version": "2.0.0",
        "description": "Self-evolving AI — C core × Rust × Python",
    },
    "paths": {
        "core_dna": str(CORE_DNA_DIR),
        "memory": str(MEMORY_DIR),
        "log_dir": str(LOG_DIR),
        "c_core_bin": str(CORE_DNA_DIR / "c-core-pipe"),
        "rust_engine_bin": str(CORE_DNA_DIR / "rust-engine-pipe"),
        "bridge_script": str(CORE_DNA_DIR / "c-core-llm-bridge.py"),
    },
    "llm": {
        "provider": os.environ.get("SUPERCLAW_LLM", "mock"),
        "api_key_env": {
            "deepseek": "DEEPSEEK_API_KEY",
            "groq": "GROQ_API_KEY",
            "openrouter": "OPENROUTER_API_KEY",
            "openai": "OPENAI_API_KEY",
            "ollama": "",
            "mock": "",
        },
        "base_url": {
            "deepseek": "https://api.deepseek.com/v1/chat/completions",
            "groq": "https://api.groq.com/openai/v1/chat/completions",
            "openrouter": "https://openrouter.ai/api/v1/chat/completions",
            "openai": "https://api.openai.com/v1/chat/completions",
            "ollama": "http://localhost:11434/api/chat",
        },
        "model": {
            "deepseek": "deepseek-chat",
            "groq": "llama-3.1-8b-instant",
            "openrouter": "anthropic/claude-3-haiku",
            "openai": "gpt-4o-mini",
            "ollama": "qwen2.5:3b",
        },
        "timeout": 60,
        "max_tokens": 2048,
    },
    "evolution": {
        "max_cycles_per_run": 10,
        "heartbeat_interval": 2,
        "mutation_rate": 0.1,
        "fitness_growth_rate": 0.01,
        "balance_threshold": 0.3,
        "self_heal": True,
        "meta_cognition": True,
    },
    "dimensions": [
        {"key": "capability", "name": "能力", "threshold": 0.3},
        {"key": "learning", "name": "学习", "threshold": 0.3},
        {"key": "knowledge", "name": "知识", "threshold": 0.3},
        {"key": "coordination", "name": "协调", "threshold": 0.3},
        {"key": "adaptation", "name": "适应", "threshold": 0.3},
    ],
    "cli": {
        "prompt_prefix": "🦖",
    },
}

CONFIG_FILE = CONFIG_DIR / "superclaw.json"


def load_config() -> Dict[str, Any]:
    """加载配置，如果不存在则从默认配置创建"""
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE) as f:
            return json.load(f)
    save_config(DEFAULT_CONFIG)
    return DEFAULT_CONFIG


def save_config(config: Dict[str, Any]) -> None:
    """保存配置"""
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


def get_config() -> Dict[str, Any]:
    """获取当前配置（懒加载）"""
    return load_config()


def update_provider(provider: str) -> None:
    """切换 LLM Provider"""
    cfg = load_config()
    cfg["llm"]["provider"] = provider
    save_config(cfg)


def path(key: str) -> Path:
    """便捷获取路径"""
    cfg = load_config()
    return Path(cfg["paths"].get(key, "."))


if __name__ == "__main__":
    cfg = load_config()
    print(f"项目根目录: {SUPERCLAW_ROOT}")
    print(f"LLM Provider: {cfg['llm']['provider']}")
    print(f"配置文件: {CONFIG_FILE}")
    print(json.dumps(cfg, ensure_ascii=False, indent=2)[:1500])
