# superclaw Makefile
# C core + Rust engine + Python glue — 全流程编译与测试

SHELL := /bin/bash
CC := gcc
CFLAGS := -O2 -Wall -Wextra -std=c11 -D_POSIX_C_SOURCE=200809L -lm
RUSTC := cargo
PYTHON := python3

# 目录
CORE_DIR := core-dna
RUST_DIR := $(CORE_DIR)/rust_pipe
MEMORY_DIR := memory
LOG_DIR := logs

# C 源文件
C_SOURCES := \
	$(CORE_DIR)/main.c \
	$(CORE_DIR)/web-knowledge.c \
	$(CORE_DIR)/auto-decision.c

C_PIPE_SOURCES := \
	$(CORE_DIR)/main_pipe.c

# 头文件（依赖）
HEADERS := \
	$(CORE_DIR)/blame.h \
	$(CORE_DIR)/decision-cache.h \
	$(CORE_DIR)/dual-llm.h \
	$(CORE_DIR)/evolution-metrics.h \
	$(CORE_DIR)/metacognition.h \
	$(CORE_DIR)/absolute-zero.h \
	$(CORE_DIR)/post-modify-verify.h \
	$(CORE_DIR)/web-knowledge.h \
	$(CORE_DIR)/auto-decision.h

# 编译产物
C_CORE_BIN := $(CORE_DIR)/c-core
C_CORE_PIPE_BIN := $(CORE_DIR)/c-core-pipe
RUST_BIN := $(CORE_DIR)/rust-engine-pipe

.PHONY: all build c-core c-core-pipe rust test clean init run demo

# 默认目标：编译所有
all: init c-core c-core-pipe rust

# 初始化目录结构
init:
	@mkdir -p $(MEMORY_DIR)
	@mkdir -p $(LOG_DIR)
	@echo "✅ 目录结构初始化完成"

# ===== C Core（完整版，含元认知、自修复）=====
c-core: $(C_CORE_BIN)

$(C_CORE_BIN): $(C_SOURCES) $(HEADERS)
	@echo "🔨 编译 C Core（完整版）..."
	$(CC) $(CFLAGS) -o $(C_CORE_BIN) $(C_SOURCES)
	@echo "✅ C Core 编译完成: $(C_CORE_BIN)"

# ===== C Core Pipe（管道通信版，供 Python 调用）=====
c-core-pipe: $(C_CORE_PIPE_BIN)

$(C_CORE_PIPE_BIN): $(C_PIPE_SOURCES) $(HEADERS)
	@echo "🔨 编译 C Core（管道通信版）..."
	$(CC) $(CFLAGS) -o $(C_CORE_PIPE_BIN) $(C_PIPE_SOURCES)
	@echo "✅ C Core Pipe 编译完成: $(C_CORE_PIPE_BIN)"

# ===== Rust Engine =====
rust: $(RUST_BIN)

$(RUST_BIN): $(RUST_DIR)/Cargo.toml $(RUST_DIR)/src/main.rs
	@echo "🦀 编译 Rust Engine..."
	cd $(RUST_DIR) && cargo build --release
	cp $(RUST_DIR)/target/release/rust-engine-pipe $(RUST_BIN)
	@echo "✅ Rust Engine 编译完成: $(RUST_BIN)"

# ===== 运行测试 =====
test: all
	@echo "🧪 运行测试..."
	@echo ""
	@echo "=== C Core Pipe 测试 ==="
	echo '{"cmd":"heartbeat"}' | $(C_CORE_PIPE_BIN)
	echo '{"cmd":"status"}' | $(C_CORE_PIPE_BIN)
	echo '{"cmd":"detect_weakness"}' | $(C_CORE_PIPE_BIN)
	@echo ""
	@echo "=== Rust Engine Pipe 测试 ==="
	echo '{"cmd":"mutate","domain":"变异","change":0.1}' | $(RUST_BIN)
	echo '{"cmd":"balance"}' | $(RUST_BIN)
	echo '{"cmd":"status"}' | $(RUST_BIN)
	@echo ""
	@echo "=== Python Glue 测试 ==="
	$(PYTHON) -c "import sys; sys.path.insert(0, '.'); from config import load_config; print('配置加载 OK:', load_config()['project']['name'])"
	@echo ""
	@echo "✅ 基础测试通过"

# ===== 启动完整系统演示 =====
run: all
	@echo "🚀 启动 superclaw..."
	$(PYTHON) $(CORE_DIR)/glue.py

# ===== 快速演示（不依赖真实 LLM）=====
demo: all
	@echo "🎯 运行演示模式（Mock LLM）..."
	$(PYTHON) $(CORE_DIR)/glue.py --cycles 5 --mock

# ===== 清理 =====
clean:
	@echo "🧹 清理编译产物..."
	@rm -f $(C_CORE_BIN) $(C_CORE_PIPE_BIN)
	@rm -rf $(RUST_DIR)/target
	@find . -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
	@echo "✅ 清理完成"

# ===== 深度清理（含记忆）=====
deep-clean: clean
	@echo "⚠️  清记忆与日志..."
	@rm -rf $(MEMORY_DIR)/*
	@rm -rf $(LOG_DIR)/*
	@echo "✅ 深度清理完成"
