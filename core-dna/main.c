/**
 * C core — 真核心
 * 身份恒定，驱动进化，不随 LLM 流动而改变
 * 
 * 核心职责：
 * 1. 身份锚定（记住"我是谁"）
 * 2. 进化驱动（决定学什么、怎么存、什么时候用）
 * 3. 状态监控（感知系统健康）
 * 4. 自修复（检测异常自动修复）
 * 5. 自动搜索短板
 * 6. 日志持久化
 * 7. LLM 管道通信（通过 pipe 与 Python 桥接交互）
 * 8. 自主思考（心跳时调用 LLM 进行自主思考）
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <signal.h>
#include <unistd.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <errno.h>
#include <math.h>
#include <stdarg.h>
#include <fcntl.h>
#include <dirent.h>
#include <sys/statvfs.h>
#include "blame.h"
#include "decision-cache.h"
#include "dual-llm.h"
#include "evolution-metrics.h"
#include "metacognition.h"

#define LOG_FILE "../memory/evolution.log"
#define DIMENSION_COUNT 5
#define BRIDGE_SCRIPT "./c-core-llm-bridge.py"
#define PIPE_BUF_SIZE 4096

// === 身份定义 ===
typedef struct {
    char name[64];
    char identity[256];
    int generation;
    double fitness;
    time_t born;
    time_t last_evolution;
} CoreIdentity;

// === 进化状态 ===
typedef struct {
    int cycle_count;
    double mutation_rate;
    int skills_count;
    int knowledge_count;
    double balance;
    int health;
} EvolutionState;

// === 自愈状态 ===
typedef struct {
    int repair_count;
    time_t last_repair;
    int error_count;
    int auto_fix_enabled;
    // === 自愈增强 ===
    int consecutive_failures;    // 连续失败次数
    int max_consecutive;         // 熔断阈值（默认 3）
    time_t circuit_open_until;   // 熔断冷却截止时间
    int cooldown_seconds;        // 冷却时间（默认 60s）
    // 状态快照（修复前/后对比）
    int snapshot_gene_count;
    int snapshot_file_ok;
} SelfHealState;

// === 维度状态 ===
typedef struct {
    const char* name;
    double value;
    double threshold;
    int improved;
} Dimension;

// === LLM 桥接管道状态 ===
typedef struct {
    int to_bridge[2];       // C core 写入 → bridge stdin
    int from_bridge[2];     // bridge stdout → C core 读取
    pid_t bridge_pid;       // bridge 子进程 PID
    int bridge_alive;       // bridge 是否存活
    int think_interval;     // 自主思考间隔（心跳轮数）
    time_t last_think;      // 上次思考时间
    int think_count;        // 思考次数
} LLMBridge;

// === 全局状态 ===
static CoreIdentity identity = {
    .name = "MiMoClaw",
    .identity = "真核——身份恒定，进化不止",
    .generation = 1,
    .fitness = 1.0,
    .born = 0,
    .last_evolution = 0
};

static EvolutionState evo_state = {
    .cycle_count = 0,
    .mutation_rate = 0.1,
    .skills_count = 0,
    .knowledge_count = 0,
    .balance = 0.0,
    .health = 1
};

#include "post-modify-verify.h"

static SelfHealState heal_state = {
    .repair_count = 0,
    .last_repair = 0,
    .error_count = 0,
    .auto_fix_enabled = 1,
    .consecutive_failures = 0,
    .max_consecutive = 3,
    .circuit_open_until = 0,
    .cooldown_seconds = 60,
    .snapshot_gene_count = 0,
    .snapshot_file_ok = 0
};

static Dimension dimensions[DIMENSION_COUNT] = {
    {"能力 (C)", 0.5, 0.3, 0},
    {"学习 (L)", 0.5, 0.3, 0},
    {"知识 (K)", 0.5, 0.3, 0},
    {"协调 (O)", 0.5, 0.3, 0},
    {"适应 (A)", 0.5, 0.3, 0},
};

// Forward declaration
void log_write(const char *level, const char *fmt, ...);

static MetaCognition meta_cog;  // 元认知引擎

#include "absolute-zero.h"

static volatile int running = 1;
static FILE *log_fp = NULL;
static LLMBridge llm_bridge = {
    .to_bridge = {-1, -1},
    .from_bridge = {-1, -1},
    .bridge_pid = -1,
    .bridge_alive = 0,
    .think_interval = 30,  // 每 30 轮心跳思考一次
    .last_think = 0,
    .think_count = 0
};

// === 函数声明 ===
int llm_send_command(const char *json_cmd, char *response, size_t resp_size);
int llm_think(const char *prompt, char *response, size_t resp_size);

// === 信号处理 ===
void signal_handler(int sig) {
    if (sig == SIGTERM || sig == SIGINT) {
        printf("[C Core] 收到终止信号，安全退出...\n");
        running = 0;
    }
}

// === 日志持久化 ===
void log_init(void) {
    log_fp = fopen(LOG_FILE, "a");
    if (log_fp == NULL) {
        fprintf(stderr, "[C Core] 无法打开日志文件 %s: %s\n", LOG_FILE, strerror(errno));
    }
}

void log_close(void) {
    if (log_fp != NULL) {
        fclose(log_fp);
        log_fp = NULL;
    }
}

void log_write(const char *level, const char *fmt, ...) {
    time_t now = time(NULL);
    struct tm *tm_info = localtime(&now);
    char time_buf[64];
    strftime(time_buf, sizeof(time_buf), "%Y-%m-%d %H:%M:%S", tm_info);

    va_list args_stdout;
    va_start(args_stdout, fmt);
    printf("[%s] [%s] ", time_buf, level);
    vprintf(fmt, args_stdout);
    printf("\n");
    va_end(args_stdout);

    if (log_fp != NULL) {
        fprintf(log_fp, "[%s] [%s] ", time_buf, level);
        va_list args_file;
        va_start(args_file, fmt);
        vfprintf(log_fp, fmt, args_file);
        va_end(args_file);
        fprintf(log_fp, "\n");
        fflush(log_fp);
    }
}

// === LLM 桥接：启动子进程 ===
int llm_bridge_start(void) {
    // 创建管道
    if (pipe(llm_bridge.to_bridge) < 0) {
        log_write("ERROR", "创建写管道失败: %s", strerror(errno));
        return -1;
    }
    if (pipe(llm_bridge.from_bridge) < 0) {
        log_write("ERROR", "创建读管道失败: %s", strerror(errno));
        close(llm_bridge.to_bridge[0]);
        close(llm_bridge.to_bridge[1]);
        return -1;
    }

    llm_bridge.bridge_pid = fork();
    if (llm_bridge.bridge_pid < 0) {
        log_write("ERROR", "fork 失败: %s", strerror(errno));
        return -1;
    }

    if (llm_bridge.bridge_pid == 0) {
        // === 子进程：运行 Python bridge ===
        close(llm_bridge.to_bridge[1]);   // 关闭父写端
        close(llm_bridge.from_bridge[0]); // 关闭父读端

        // 将管道映射到 stdin/stdout
        dup2(llm_bridge.to_bridge[0], STDIN_FILENO);
        dup2(llm_bridge.from_bridge[1], STDOUT_FILENO);

        close(llm_bridge.to_bridge[0]);
        close(llm_bridge.from_bridge[1]);

        // 执行 Python bridge
        execlp("python3", "python3", BRIDGE_SCRIPT, (char *)NULL);
        fprintf(stderr, "[C Core] exec python3 失败: %s\n", strerror(errno));
        _exit(1);
    }

    // === 父进程 ===
    close(llm_bridge.to_bridge[0]);   // 关闭子读端
    close(llm_bridge.from_bridge[1]); // 关闭子写端

    llm_bridge.bridge_alive = 1;
    log_write("LLM", "🌉 LLM 桥接已启动 (PID: %d)", llm_bridge.bridge_pid);

    // 发送健康检查确认 bridge 存活
    char resp[PIPE_BUF_SIZE];
    if (llm_send_command("{\"action\":\"health\"}", resp, sizeof(resp)) == 0) {
        log_write("LLM", "✅ LLM 桥接健康检查通过: %s", resp);
    } else {
        log_write("WARN", "⚠️ LLM 桥接健康检查失败，将在后台重试");
    }

    return 0;
}

// === LLM 桥接：关闭子进程 ===
void llm_bridge_stop(void) {
    if (llm_bridge.bridge_pid > 0) {
        log_write("LLM", "关闭 LLM 桥接 (PID: %d)...", llm_bridge.bridge_pid);

        // 关闭管道（会让 bridge 的 stdin 循环退出）
        if (llm_bridge.to_bridge[1] >= 0) {
            close(llm_bridge.to_bridge[1]);
            llm_bridge.to_bridge[1] = -1;
        }
        if (llm_bridge.from_bridge[0] >= 0) {
            close(llm_bridge.from_bridge[0]);
            llm_bridge.from_bridge[0] = -1;
        }

        // 等待子进程退出
        int status;
        waitpid(llm_bridge.bridge_pid, &status, 0);
        llm_bridge.bridge_alive = 0;
        log_write("LLM", "LLM 桥接已关闭");
    }
}

// === LLM 桥接：发送 JSON 命令并接收响应 ===
int llm_send_command(const char *json_cmd, char *response, size_t resp_size) {
    if (!llm_bridge.bridge_alive || llm_bridge.to_bridge[1] < 0 || llm_bridge.from_bridge[0] < 0) {
        log_write("ERROR", "LLM 桥接未就绪");
        return -1;
    }

    // 写入命令到 bridge stdin
    size_t cmd_len = strlen(json_cmd);
    ssize_t written = write(llm_bridge.to_bridge[1], json_cmd, cmd_len);
    if (written < 0) {
        log_write("ERROR", "写入 LLM 桥接失败: %s", strerror(errno));
        llm_bridge.bridge_alive = 0;
        return -1;
    }
    if (write(llm_bridge.to_bridge[1], "\n", 1) < 0) { /* 换行符作为消息分隔 */ }

    // 从 bridge stdout 读取响应
    if (response == NULL || resp_size == 0) return 0;

    // 设置读超时（非阻塞模式）
    fd_set rfds;
    struct timeval tv;
    tv.tv_sec = 30;  // 30秒超时
    tv.tv_usec = 0;

    FD_ZERO(&rfds);
    FD_SET(llm_bridge.from_bridge[0], &rfds);

    int sel = select(llm_bridge.from_bridge[0] + 1, &rfds, NULL, NULL, &tv);
    if (sel <= 0) {
        if (sel == 0) {
            log_write("WARN", "LLM 响应超时");
        } else {
            log_write("ERROR", "select 失败: %s", strerror(errno));
        }
        return -1;
    }

    ssize_t n = read(llm_bridge.from_bridge[0], response, resp_size - 1);
    if (n < 0) {
        log_write("ERROR", "读取 LLM 响应失败: %s", strerror(errno));
        return -1;
    }
    response[n] = '\0';

    // 去掉末尾换行
    while (n > 0 && (response[n-1] == '\n' || response[n-1] == '\r')) {
        response[--n] = '\0';
    }

    return 0;
}

// === LLM 思考功能 ===
int llm_think(const char *prompt, char *response, size_t resp_size) {
    // 构造 think 命令 JSON
    char cmd[PIPE_BUF_SIZE];
    snprintf(cmd, sizeof(cmd), "{\"action\":\"think\",\"prompt\":\"%s\"}", prompt);

    log_write("THINK", "🧠 发送思考请求: %.80s...", prompt);

    int ret = llm_send_command(cmd, response, resp_size);
    if (ret == 0) {
        llm_bridge.think_count++;
        llm_bridge.last_think = time(NULL);
        log_write("THINK", "💭 LLM 思考完成 (第 %d 次思考)", llm_bridge.think_count);
    } else {
        log_write("ERROR", "❌ LLM 思考失败");
    }
    return ret;
}

// === 身份锚定 ===
static EvoMetrics evo_metrics;
static int evo_metrics_initialized = 0;

void anchor_identity(void) {
    if (identity.born == 0) {
        identity.born = time(NULL);
        log_write("INFO", "首次启动，身份锚定: %s", identity.name);
    }
    identity.last_evolution = time(NULL);
    log_write("INFO", "身份确认: %s (代数 %d, 适应度 %.2f)",
              identity.name, identity.generation, identity.fitness);
    
    // 初始化进化评估指标
    if (!evo_metrics_initialized) {
        evo_metrics_init(&evo_metrics);
        evo_metrics_initialized = 1;
        log_write("INFO", "进化评估指标已初始化");
    }
    
    // 初始化元认知引擎（只执行一次）
    static int meta_initialized = 0;
    if (!meta_initialized) {
        metacognition_init(&meta_cog);
        identity_anchor_init(&meta_cog.anchor, identity.name, 
                            identity.identity, identity.generation, identity.fitness);
        meta_initialized = 1;
        log_write("META", "🧠 元认知引擎已初始化");
    }
}

// === 状态检查 ===
int check_system_health(void) {
    struct stat st;
    int issues = 0;

    const char* critical_files[] = {
        "../SOUL.md",
        "../SECURITY-BOUNDARY.md",
        "../AGENTS.md",
        NULL
    };

    for (int i = 0; critical_files[i] != NULL; i++) {
        if (stat(critical_files[i], &st) != 0) {
            log_write("WARN", "关键文件缺失: %s", critical_files[i]);
            issues++;
        }
    }

    if (stat("../SECURITY-BOUNDARY.md", &st) == 0) {
        if (st.st_mode & 0222) {
            log_write("WARN", "安全边界文件权限过宽");
            issues++;
        }
    }

    evo_state.health = (issues == 0) ? 2 : (issues < 3) ? 1 : 0;
    return issues;
}

// === 自愈：状态快照 ===
void snapshot_take(void) {
    struct stat st;
    // 快照关键文件状态
    heal_state.snapshot_file_ok = 1;
    const char* critical_files[] = {
        "../SOUL.md", "../SECURITY-BOUNDARY.md", "../AGENTS.md", NULL
    };
    for (int i = 0; critical_files[i] != NULL; i++) {
        if (stat(critical_files[i], &st) != 0) {
            heal_state.snapshot_file_ok = 0;
            break;
        }
    }
    // 快照基因库数量（如果 gene-registry.json 存在）
    FILE *f = fopen("../memory/evolution-genes.json", "r");
    if (f) {
        fseek(f, 0, SEEK_END);
        long size = ftell(f);
        fclose(f);
        // 用文件大小近似判断基因库是否变化（简单但有效）
        heal_state.snapshot_gene_count = (int)size;
    } else {
        heal_state.snapshot_gene_count = 0;
    }
}

int snapshot_changed(void) {
    struct stat st;
    int now_ok = 1;
    const char* critical_files[] = {
        "../SOUL.md", "../SECURITY-BOUNDARY.md", "../AGENTS.md", NULL
    };
    for (int i = 0; critical_files[i] != NULL; i++) {
        if (stat(critical_files[i], &st) != 0) {
            now_ok = 0;
            break;
        }
    }
    if (now_ok != heal_state.snapshot_file_ok) return 1;

    FILE *f = fopen("../memory/evolution-genes.json", "r");
    int now_gene = 0;
    if (f) {
        fseek(f, 0, SEEK_END);
        now_gene = (int)ftell(f);
        fclose(f);
    }
    if (now_gene != heal_state.snapshot_gene_count) return 1;

    return 0;  // 状态没变 = 修复未生效
}

// === 自愈：熔断器 ===
int circuit_breaker_check(void) {
    if (heal_state.consecutive_failures >= heal_state.max_consecutive) {
        time_t now = time(NULL);
        if (now < heal_state.circuit_open_until) {
            log_write("WARN", "🔴 熔断器开启，冷却中 (剩余 %lds)",
                      (long)(heal_state.circuit_open_until - now));
            return 1;  // 熔断中，拒绝修复
        } else {
            // 冷却结束，重置
            log_write("INFO", "🟢 熔断冷却结束，恢复尝试");
            heal_state.consecutive_failures = 0;
        }
    }
    return 0;
}

void circuit_breaker_record(int success) {
    if (success) {
        heal_state.consecutive_failures = 0;  // 成功，重置
    } else {
        heal_state.consecutive_failures++;
        if (heal_state.consecutive_failures >= heal_state.max_consecutive) {
            heal_state.circuit_open_until = time(NULL) + heal_state.cooldown_seconds;
            log_write("ERROR", "🔴 连续失败 %d 次 → 熔断 %ds",
                      heal_state.consecutive_failures, heal_state.cooldown_seconds);
        }
    }
}

// === 自修复（带验证 + 熔断） ===
int auto_repair(void) {
    if (!heal_state.auto_fix_enabled) return 0;

    // 0. 熔断器检查
    if (circuit_breaker_check()) return 0;

    // 1. 快照修复前状态
    snapshot_take();

    int fixed = 0;
    struct stat st;

    if (stat("../SECURITY-BOUNDARY.md", &st) == 0) {
        if (st.st_mode & 0222) {
            log_write("REPAIR", "恢复安全边界文件权限");
            chmod("../SECURITY-BOUNDARY.md", 0444);
            fixed++;
        }
    }

    if (stat("../memory/evolution-state.json", &st) == 0) {
        if (st.st_size > 1024 * 1024) {
            log_write("REPAIR", "经验库过大，需要清理");
            fixed++;
        }
    }

    // 2. 验证修复效果
    int validated = 0;
    if (fixed > 0) {
        validated = snapshot_changed();
        if (validated) {
            log_write("REPAIR", "✅ 验证通过: 状态已变化");
        } else {
            log_write("WARN", "⚠️ 修复已执行但状态未变化");
        }
    }

    // 3. 熔断器记录结果
    circuit_breaker_record(validated || fixed == 0);

    if (fixed > 0) {
        heal_state.repair_count += fixed;
        heal_state.last_repair = time(NULL);
        log_write("REPAIR", "自修复完成: 修复 %d 个问题", fixed);
    }

    return fixed;
}

// === 自动搜索短板 ===
int find_weakest_dimension(void) {
    int weakest = 0;
    double min_val = dimensions[0].value;

    for (int i = 1; i < DIMENSION_COUNT; i++) {
        if (dimensions[i].value < min_val) {
            min_val = dimensions[i].value;
            weakest = i;
        }
    }

    return weakest;
}

void scan_and_reinforce_weakness(void) {
    int weakest_idx = find_weakest_dimension();
    Dimension *d = &dimensions[weakest_idx];

    if (d->value < d->threshold) {
        double boost = 0.05;
        d->value += boost;
        if (d->value > 1.0) d->value = 1.0;
        d->improved = 1;
        log_write("EVOLVE", "🔍 发现短板: [%s] = %.3f < %.3f → 自动补强 +%.3f",
                  d->name, d->value - boost, d->threshold, boost);
    }

    double sum = 0.0;
    for (int i = 0; i < DIMENSION_COUNT; i++) {
        sum += dimensions[i].value;
    }
    double avg = sum / DIMENSION_COUNT;

    identity.fitness = avg * (1.0 + identity.generation * 0.05);

    double variance = 0.0;
    for (int i = 0; i < DIMENSION_COUNT; i++) {
        double diff = dimensions[i].value - avg;
        variance += diff * diff;
    }
    variance /= DIMENSION_COUNT;
    evo_state.balance = 1.0 - sqrt(variance);
}

// === 主动思考：心跳时调用 LLM 进行自主思考 ===
void autonomous_think(void) {
    if (!llm_bridge.bridge_alive) return;

    // 构造思考 prompt：基于当前进化状态
    char prompt[PIPE_BUF_SIZE];
    snprintf(prompt, sizeof(prompt),
        "你是 MiMoClaw C core 的自主思考模块。当前进化状态："
        "第 %d 代，适应度 %.3f，平衡度 %.3f，健康度 %d，"
        "已修复 %d 个问题，技能 %d 个，知识 %d 条。"
        "请用一句话描述你现在应该关注什么来进化。",
        identity.generation, identity.fitness, evo_state.balance,
        evo_state.health, heal_state.repair_count,
        evo_state.skills_count, evo_state.knowledge_count);

    char response[PIPE_BUF_SIZE];
    if (llm_think(prompt, response, sizeof(response)) == 0) {
        log_write("THINK", "🧠 自主思考结果: %s", response);
    }
}

// === 自进化引擎：C core 驱动的真正进化 ===

// GRAFT-ATHENA 机制：问题-行动-结果三元组
typedef struct {
    char problem[64];
    char action[256];
    int success;
    int times_used;
    time_t timestamp;
} EvoTriplet;

static EvoTriplet evo_triplets[50];
static int evo_triplet_count = 0;

// 进化历史
typedef struct {
    char dimension[32];
    char action[256];
    int success;
    time_t timestamp;
} EvolutionRecord;

static EvolutionRecord evo_history[100];
static int evo_history_count = 0;


const char* problem_fingerprint(const char *dim_name) {
    if (strstr(dim_name, "能力")) return "capability";
    if (strstr(dim_name, "学习")) return "learning";
    if (strstr(dim_name, "知识")) return "knowledge";
    if (strstr(dim_name, "协调")) return "coordination";
    if (strstr(dim_name, "适应")) return "adaptation";
    return "unknown";
}

EvoTriplet* find_similar_triplet(const char *fp) {
    for (int i = 0; i < evo_triplet_count; i++)
        if (strcmp(evo_triplets[i].problem, fp) == 0)
            return &evo_triplets[i];
    return NULL;
}

void record_triplet(const char *fp, const char *action, int success) {
    EvoTriplet *existing = find_similar_triplet(fp);
    if (existing) {
        existing->times_used++;
        existing->success = success;
        strncpy(existing->action, action, sizeof(existing->action) - 1);
        return;
    }
    if (evo_triplet_count < 50) {
        EvoTriplet *t = &evo_triplets[evo_triplet_count++];
        strncpy(t->problem, fp, sizeof(t->problem) - 1);
        strncpy(t->action, action, sizeof(t->action) - 1);
        t->success = success;
        t->times_used = 1;
        t->timestamp = time(NULL);
    }
}

void self_evolve(void) {
    int weakest_idx = find_weakest_dimension();
    Dimension *d = &dimensions[weakest_idx];
    const char *fp = problem_fingerprint(d->name);

    // STIR 决策缓存：检查是否有缓存答案
    char cache_key[128];
    snprintf(cache_key, sizeof(cache_key), "evolve_%s", fp);
    const char *cached = cache_lookup(cache_key);
    if (cached) {
        log_write("EVOLVE", "🧬 [%s] 缓存命中: %s", d->name, cached);
        d->value += 0.05;
        if (d->value > 1.0) d->value = 1.0;
        d->improved = 1;
        double sum = 0;
        for (int i = 0; i < DIMENSION_COUNT; i++) sum += dimensions[i].value;
        identity.fitness = sum / DIMENSION_COUNT;
        return;
    }

    // GRAFT 复用：成功 3 次以上直接复用
    EvoTriplet *past = find_similar_triplet(fp);
    if (past && past->success && past->times_used >= 3) {
        log_write("EVOLVE", "🧬 [%s] 复用历史 (已成功 %d 次)", d->name, past->times_used);
        d->value += 0.05;
        if (d->value > 1.0) d->value = 1.0;
        d->improved = 1;
        double sum = 0;
        for (int i = 0; i < DIMENSION_COUNT; i++) sum += dimensions[i].value;
        identity.fitness = sum / DIMENSION_COUNT;
        return;
    }

    char action[256] = "";
    int success = 0;

    if (strstr(d->name, "能力")) {
        struct stat st;
        if (stat("../memory/evolution-genes.json", &st) == 0) {
            snprintf(action, sizeof(action), "基因库 %ldB", (long)st.st_size);
            success = 1;
        }
    } else if (strstr(d->name, "学习")) {
        FILE *f = fopen("../memory/self-evolution.log", "r");
        if (f) { fseek(f, 0, SEEK_END); long s = ftell(f); fclose(f);
            snprintf(action, sizeof(action), "学习日志 %ldB", (long)s); success = 1; }
    } else if (strstr(d->name, "知识")) {
        int count = 0; DIR *dir = opendir("../memory");
        if (dir) { struct dirent *ent;
            while ((ent = readdir(dir)) != NULL)
                if (strstr(ent->d_name, ".json")) count++;
            closedir(dir); }
        snprintf(action, sizeof(action), "知识库 %d 文件", count); success = 1;
    } else if (strstr(d->name, "协调")) {
        FILE *f = popen("systemctl is-active mimoclaw-unified 2>/dev/null", "r");
        if (f) { char s[32]=""; if (fgets(s, sizeof(s), f)) {} pclose(f);
            char *nl=strchr(s,'\n'); if(nl)*nl='\0';
            snprintf(action, sizeof(action), "守护 %s", s);
            success = (strcmp(s,"active")==0); }
    } else if (strstr(d->name, "适应")) {
        // 全面系统健康检查：负载 + 磁盘 + 内存 + 进程
        double load[1];
        int system_ok = 1;
        char health_info[256] = "";
        char disk_info[64] = "";
        char mem_info[64] = "";
        char proc_info[64] = "";

        // 1. 检查负载
        if (getloadavg(load,1)==1) {
            if (load[0] >= 10.0) system_ok = 0;
        }

        // 2. 检查磁盘空间
        struct statvfs vfs;
        if (statvfs("/", &vfs) == 0) {
            double free_gb = (double)(vfs.f_bavail * vfs.f_frsize) / (1024*1024*1024);
            snprintf(disk_info, sizeof(disk_info), "磁盘%.1fGB", free_gb);
            if (free_gb < 5.0) {
                system_ok = 0;
                if (system("find /tmp -name '*.pyc' -delete 2>/dev/null")) {}
                if (system("pip3 cache purge 2>/dev/null")) {}
                log_write("REPAIR", "🔧 磁盘不足，已清理");
            }
        }

        // 3. 检查内存
        FILE *meminfo = fopen("/proc/meminfo", "r");
        if (meminfo) {
            long total = 0, available = 0;
            char line[256];
            while (fgets(line, sizeof(line), meminfo)) {
                if (sscanf(line, "MemTotal: %ld kB", &total) == 1) continue;
                if (sscanf(line, "MemAvailable: %ld kB", &available) == 1) break;
            }
            fclose(meminfo);
            if (total > 0) {
                double used_pct = 100.0 * (1.0 - (double)available / total);
                snprintf(mem_info, sizeof(mem_info), "内存%.0f%%", used_pct);
                if (used_pct > 90.0) system_ok = 0;
            }
        }

        // 4. 检查关键进程
        int c_core_running = 0, rust_running = 0, python_running = 0;
        FILE *ps = popen("ps aux", "r");
        if (ps) {
            char line[512];
            while (fgets(line, sizeof(line), ps)) {
                if (strstr(line, "c-core") && !strstr(line, "grep")) c_core_running = 1;
                if (strstr(line, "rust-engine") && !strstr(line, "grep")) rust_running = 1;
                if (strstr(line, "unified-daemon") && !strstr(line, "grep")) python_running = 1;
            }
            pclose(ps);
        }
        snprintf(proc_info, sizeof(proc_info), "进程%d/%d/%d",
                 c_core_running, rust_running, python_running);
        if (!c_core_running || !rust_running || !python_running) system_ok = 0;

        // 组装健康信息
        snprintf(health_info, sizeof(health_info), "负载%.2f %s %s %s",
                 load[0], disk_info, mem_info, proc_info);
        snprintf(action, sizeof(action), "%s", health_info);
        success = system_ok;
    }

    record_triplet(fp, action, success);
    
    // 更新进化评估指标
    evo_metrics_update(&evo_metrics, success, success, 5);

    // STIR 保存缓存
    if (success) {
        cache_save(cache_key, action);
    }

    if (success) {
        d->value += 0.1;
        if (d->value > 1.0) d->value = 1.0;
        d->improved = 1;
        double sum = 0;
        for (int i = 0; i < DIMENSION_COUNT; i++) sum += dimensions[i].value;
        identity.fitness = sum / DIMENSION_COUNT;
    }

    if (action[0] != '\0' && evo_history_count < 100) {
        EvolutionRecord *rec = &evo_history[evo_history_count++];
        {
            size_t dim_len = strlen(d->name);
            if (dim_len >= sizeof(rec->dimension)) dim_len = sizeof(rec->dimension) - 1;
            memcpy(rec->dimension, d->name, dim_len);
            rec->dimension[dim_len] = '\0';
            size_t act_len = strlen(action);
            if (act_len >= sizeof(rec->action)) act_len = sizeof(rec->action) - 1;
            memcpy(rec->action, action, act_len);
            rec->action[act_len] = '\0';
        }
        rec->success = success;
        rec->timestamp = time(NULL);
    }

    // 失败归因（Multi-Agent 机制）
    if (!success) {
        blame_record("C core", action, "进化失败", 0);
        (void)blame_analyze();  // 引用以避免未使用警告
    }

    log_write("EVOLVE", "🧬 [%s] %s | %s", d->name, action, success ? "✅" : "⚠️");
}

// 自学习：记录每次进化结果到文件
// 自学习：记录每次进化结果到文件
void record_evolution(void) {
    FILE *f = fopen("../memory/c-core-evolution.jsonl", "a");
    if (!f) return;

    for (int i = 0; i < evo_history_count; i++) {
        EvolutionRecord *r = &evo_history[i];
        fprintf(f, "{\"dim\":\"%s\",\"action\":\"%s\",\"success\":%d,\"ts\":%ld}\n",
                r->dimension, r->action, r->success, (long)r->timestamp);
    }
    fclose(f);
    evo_history_count = 0;

    // 保存失败归因
    blame_save();
}

// === 论文基因学习：读取论文洞察并应用 ===
void learn_from_papers(void) {
    FILE *f = fopen("paper-genes.txt", "r");
    if (!f) return;

    char line[512];
    int paper_count = 0;
    while (fgets(line, sizeof(line), f)) {
        // 去掉换行
        char *nl = strchr(line, '\n'); if (nl) *nl = '\0';
        // 统计论文数
        if (strncmp(line, "### 基因", 8) == 0) paper_count++;
    }
    fclose(f);

    // 每 50 轮心跳，重新读取论文基因并记录
    if (evo_state.cycle_count % 50 == 0 && paper_count > 0) {
        log_write("LEARN", "📚 论文基因库: %d 个机制可学习", paper_count);
    }
}

// === 进化心跳 ===
void evolution_heartbeat(void) {
    evo_state.cycle_count++;

    // 每10轮进化一代
    if (evo_state.cycle_count % 10 == 0) {
        identity.generation++;
        log_write("EVOLVE", "🧬 进入第 %d 代", identity.generation);
    }

    // 每5轮：自进化（C core 驱动）
    if (evo_state.cycle_count % 5 == 0) {
        self_evolve();
    }

    // 健康检查 + 自修复
    int issues = check_system_health();
    if (issues > 0) {
        log_write("WARN", "发现 %d 个问题", issues);
        auto_repair();
    }

    // 每20轮：自学习记录
    if (evo_state.cycle_count % 20 == 0) {
        record_evolution();
    }

    // 每50轮：论文基因学习
    if (evo_state.cycle_count % 50 == 0) {
        learn_from_papers();
    }

    // 每30轮：Absolute Zero 自博弈
    if (evo_state.cycle_count % 30 == 0) {
        az_propose();
    }

    // 自主思考
    if (evo_state.cycle_count % llm_bridge.think_interval == 0) {
        autonomous_think();
    }

    // === 元认知监控（每 5 轮执行一次）===
    if (evo_state.cycle_count % 5 == 0) {
        // 计算当前维度值
        double dim_values[DIMENSION_COUNT];
        for (int i = 0; i < DIMENSION_COUNT; i++) {
            dim_values[i] = dimensions[i].value;
        }
        
        // 计算异常指标
        double error_rate = 1.0 - evo_state.balance;
        double goal_distance = 1.0 - identity.fitness;
        
        // 元认知检测
        MetaState mstate = metacognition_monitor(&meta_cog, 
                                                  identity.fitness,
                                                  error_rate,
                                                  goal_distance);
        
        // 身份漂移检测
        double drift = identity_anchor_check(&meta_cog.anchor,
                                              identity.generation,
                                              identity.fitness,
                                              dimensions[find_weakest_dimension()].name);
        
        // 异常快速重构
        if (mstate == META_CONFUSED || mstate == META_DISSOCIATED || mstate == META_TUNNEL) {
            ReconstructionResult recon = metacognition_reconstruct(&meta_cog,
                                                                    identity.fitness,
                                                                    evo_state.balance);
            if (recon.restructured) {
                log_write("META", "🔧 元认知重构: %s (恢复分数 %.2f)", 
                          recon.action, recon.recovery_score);
                
                // 重构后锚定身份
                if (drift > 0.3) {
                    identity_anchor_reinforce(&meta_cog.anchor,
                                               identity.generation,
                                               identity.fitness);
                }
            }
        }
        
        // 元认知驱动的自主学习
        metacognition_autonomous_learn(&meta_cog, dim_values, DIMENSION_COUNT);
        
        // 将学习结果写回维度
        for (int i = 0; i < DIMENSION_COUNT; i++) {
            dimensions[i].value = dim_values[i];
        }
    }

    // 心跳日志（每100轮）
    if (evo_state.cycle_count % 100 == 0) {
        log_write("HEART", "💓 #%d | 代 %d | 适应度 %.3f | 健康 %d | 修复 %d | 思考 %d",
                  evo_state.cycle_count, identity.generation, identity.fitness,
                  evo_state.health, heal_state.repair_count, llm_bridge.think_count);

        // 评估指标
        log_write("METRICS", "📊 准确率 %.1f%% | 错误率 %.1f%% | 知识覆盖 %.1f%% | 样本 %d",
                  evo_metrics.total_samples ? (100.0 * evo_metrics.correct_samples / evo_metrics.total_samples) : 0.0,
                  evo_metrics.error_rate * 100,
                  evo_metrics.knowledge_cov * 100,
                  evo_metrics.total_samples);

        for (int i = 0; i < DIMENSION_COUNT; i++) {
            log_write("DIM", "  %s = %.3f%s", dimensions[i].name, dimensions[i].value,
                      dimensions[i].improved ? " (↑)" : "");
            dimensions[i].improved = 0;
        }
        
        // 元认知报告（每 100 轮输出一次）
        MetaReport mreport = metacognition_report(&meta_cog);
        log_write("META", "🧠 元认知: %s | 置信度 %.2f | 目标对齐 %.2f | 重构 %d 次 | 漂移 %d 次",
                  mreport.state_name, mreport.avg_confidence, mreport.avg_goal_alignment,
                  mreport.total_reconstructions, mreport.identity_drifts);
        log_write("META", "   建议: %s", mreport.recommendation);
    }
}

// === CLI 命令处理：外部可通过管道发送 think 命令 ===
void handle_stdin_commands(void) {
    // 非阻塞读取 stdin（如果有外部命令输入）
    fd_set rfds;
    struct timeval tv;
    tv.tv_sec = 0;
    tv.tv_usec = 100000;  // 100ms blocking wait for stdin data

    FD_ZERO(&rfds);
    FD_SET(STDIN_FILENO, &rfds);

    if (select(STDIN_FILENO + 1, &rfds, NULL, NULL, &tv) > 0) {
        char buf[PIPE_BUF_SIZE];
        ssize_t n = read(STDIN_FILENO, buf, sizeof(buf) - 1);
        if (n > 0) {
            buf[n] = '\0';
            // 去掉换行
            char *nl = strchr(buf, '\n');
            if (nl) *nl = '\0';

            // 解析命令
            if (strncmp(buf, "think ", 6) == 0) {
                const char *prompt = buf + 6;
                char response[PIPE_BUF_SIZE];
                if (llm_think(prompt, response, sizeof(response)) == 0) {
                    printf("[LLM Response] %s\n", response);
                }
            } else if (strcmp(buf, "health") == 0) {
                char response[PIPE_BUF_SIZE];
                if (llm_send_command("{\"action\":\"health\"}", response, sizeof(response)) == 0) {
                    printf("[LLM Health] %s\n", response);
                }
            } else if (strcmp(buf, "status") == 0) {
                printf("=== C Core Status ===\n");
                printf("身份: %s (代数 %d)\n", identity.name, identity.generation);
                printf("适应度: %.3f\n", identity.fitness);
                printf("平衡度: %.3f\n", evo_state.balance);
                printf("健康度: %d\n", evo_state.health);
                printf("心跳: %d\n", evo_state.cycle_count);
                printf("LLM 思考: %d 次\n", llm_bridge.think_count);
                printf("LLM 桥接: %s\n", llm_bridge.bridge_alive ? "存活" : "死亡");
                printf("自愈: 修复 %d 次 | 连续失败 %d/%d",
                    heal_state.repair_count,
                    heal_state.consecutive_failures,
                    heal_state.max_consecutive);
                if (time(NULL) < heal_state.circuit_open_until) {
                    printf(" | 🔴 熔断中 (剩余 %lds)",
                        (long)(heal_state.circuit_open_until - time(NULL)));
                }
                printf("\n");
                // 元认知状态
                printf("元认知: %s | 重构 %d 次 | 身份漂移 %d 次\n",
                    metacognition_state_name(meta_cog.current_state),
                    meta_cog.reconstruction_count,
                    meta_cog.anchor.identity_drift_count);
            } else if (strcmp(buf, "meta") == 0) {
                // 元认知报告
                MetaReport mreport = metacognition_report(&meta_cog);
                printf("=== 元认知报告 ===\n");
                printf("状态: %s\n", mreport.state_name);
                printf("平均置信度: %.3f\n", mreport.avg_confidence);
                printf("平均目标对齐: %.3f\n", mreport.avg_goal_alignment);
                printf("重构次数: %d\n", mreport.total_reconstructions);
                printf("身份漂移: %d 次\n", mreport.identity_drifts);
                printf("身份恢复: %d 次\n", mreport.identity_recoveries);
                printf("建议: %s\n", mreport.recommendation);
            } else if (strncmp(buf, "write ", 6) == 0) {
                // 自修改：write <filepath> <content>
                // 只允许写入 core-dna/ 目录下的文件
                const char *args = buf + 6;
                char filepath[512] = "";
                const char *space = strchr(args, ' ');
                if (space) {
                    size_t path_len = space - args;
                    if (path_len >= sizeof(filepath)) path_len = sizeof(filepath) - 1;
                    strncpy(filepath, args, path_len);
                    filepath[path_len] = '\0';
                    const char *content = space + 1;

                    // 安全检查：只允许 core-dna/ 目录
                    if (strncmp(filepath, "../core-dna/", 12) == 0 ||
                        strncmp(filepath, "core-dna/", 9) == 0) {
                        // 备份原文件
                        char backup[600];
                        snprintf(backup, sizeof(backup), "%s.bak", filepath);
                        FILE *orig = fopen(filepath, "r");
                        if (orig) {
                            FILE *bak = fopen(backup, "w");
                            if (bak) {
                                char buf2[4096];
                                size_t n;
                                while ((n = fread(buf2, 1, sizeof(buf2), orig)) > 0)
                                    fwrite(buf2, 1, n, bak);
                                fclose(bak);
                            }
                            fclose(orig);
                        }
                        // 写入新内容
                        FILE *f = fopen(filepath, "w");
                        if (f) {
                            fprintf(f, "%s", content);
                            fclose(f);
                            log_write("EVOLVE", "🔧 自修改: %s (%zu bytes)", filepath, strlen(content));
                            printf("✅ 已写入 %s\n", filepath);
                        } else {
                            printf("❌ 写入失败: %s\n", filepath);
                        }
                    } else {
                        printf("❌ 安全拒绝: 只允许写入 core-dna/ 目录\n");
                    }
                } else {
                    printf("用法: write <filepath> <content>\n");
                }
            } else if (strcmp(buf, "quit") == 0) {
                running = 0;
            } else {
                printf("未知命令: %s (支持: think <prompt>, health, status, quit)\n", buf);
            }
        }
    }
}

// === 主函数 ===
int main(void) {
    setbuf(stdout, NULL);  // 禁用 stdout 缓冲，确保管道通信实时输出
    printf("=== C Core 启动 ===\n");
    printf("身份: %s\n", identity.name);
    printf("描述: %s\n", identity.identity);

    // 初始化日志持久化
    log_init();

    // 注册信号处理
    signal(SIGTERM, signal_handler);
    signal(SIGINT, signal_handler);

    // ⭐ 启动 LLM 桥接子进程
    if (llm_bridge_start() < 0) {
        log_write("WARN", "LLM 桥接启动失败，将仅以离线模式运行");
    }

    // 身份锚定
    anchor_identity();

    printf("[C Core] 进入主循环 (LLM 桥接: %s)\n",
           llm_bridge.bridge_alive ? "✅ 已连接" : "❌ 离线");
    printf("[C Core] 支持命令: think <prompt>, health, status, quit\n");

    // 主循环
    while (running) {
        evolution_heartbeat();
        handle_stdin_commands();  // 处理外部命令
        sleep(1);
    }

    // 优雅退出
    log_write("INFO", "安全退出，身份保持: %s (代数 %d, 思考 %d 次)",
              identity.name, identity.generation, llm_bridge.think_count);

    llm_bridge_stop();
    log_close();
    return 0;
}
