/**
 * C Core — 管道通信版本
 * 通过 stdin 接收 JSON 命令，通过 stdout 返回 JSON 响应
 * 
 * 协议：
 *   stdin  → {"cmd":"heartbeat"} | {"cmd":"detect_weakness"} | {"cmd":"record_evolution",...}
 *   stdout → {"status":"ok","data":{...}} | {"status":"error","msg":"..."}
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <signal.h>
#include <unistd.h>

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
} SelfHealState;

// === 短板检测结果 ===
typedef struct {
    char weaknesses[1024];
    int count;
} WeaknessReport;

// === 全局状态 ===
static CoreIdentity identity = {
    .name = "MiMoClaw",
    .identity = "Core——身份恒定，进化不止",
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

static SelfHealState heal_state = {
    .repair_count = 0,
    .last_repair = 0,
    .error_count = 0,
    .auto_fix_enabled = 1
};

// === 简易 JSON 工具 ===

// 从 JSON 字符串中提取简单字段值
// 如：{"cmd":"heartbeat"} → 提取 "heartbeat"
static char* json_get_string(const char* json, const char* key, char* buf, int bufsize) {
    char pattern[128];
    snprintf(pattern, sizeof(pattern), "\"%s\"", key);
    
    const char* pos = strstr(json, pattern);
    if (!pos) return NULL;
    
    pos += strlen(pattern);
    while (*pos == ' ' || *pos == ':') pos++;
    
    if (*pos == '"') {
        pos++;
        int i = 0;
        while (*pos && *pos != '"' && i < bufsize - 1) {
            buf[i++] = *pos++;
        }
        buf[i] = '\0';
        return buf;
    }
    
    return NULL;
}

// 从 JSON 中提取整数值
static int json_get_int(const char* json, const char* key, int default_val) {
    char val[32];
    if (json_get_string(json, key, val, sizeof(val))) {
        return atoi(val);
    }
    return default_val;
}

// 从 JSON 中提取浮点值
static double json_get_float(const char* json, const char* key, double default_val) {
    char val[32];
    if (json_get_string(json, key, val, sizeof(val))) {
        return atof(val);
    }
    return default_val;
}

// === 身份锚定 ===
void anchor_identity() {
    if (identity.born == 0) {
        identity.born = time(NULL);
    }
    identity.last_evolution = time(NULL);
}

// === 状态检查 ===
int check_system_health() {
    int issues = 0;
    evo_state.health = (issues == 0) ? 2 : (issues < 3) ? 1 : 0;
    return issues;
}

// === 自修复 ===
int auto_repair() {
    if (!heal_state.auto_fix_enabled) return 0;
    int fixed = 0;
    heal_state.repair_count += fixed;
    return fixed;
}

// === 短板检测 ===
void detect_weakness(WeaknessReport* report) {
    report->count = 0;
    char* p = report->weaknesses;
    int remaining = sizeof(report->weaknesses);
    
    // 检查技能-知识平衡
    if (evo_state.skills_count < 5) {
        int n = snprintf(p, remaining, "%s", "skills_count_low");
        p += n; remaining -= n; report->count++;
    }
    if (evo_state.knowledge_count < 5) {
        int n = snprintf(p, remaining, "%s%s", report->count ? ",":"", "knowledge_count_low");
        p += n; remaining -= n; report->count++;
    }
    if (evo_state.balance < 0.3) {
        int n = snprintf(p, remaining, "%s%s", report->count ? ",":"", "balance_low");
        p += n; remaining -= n; report->count++;
    }
    if (identity.fitness < 1.05) {
        int n = snprintf(p, remaining, "%s%s", report->count ? ",":"", "fitness_low");
        p += n; remaining -= n; report->count++;
    }
    if (evo_state.health < 2) {
        int n = snprintf(p, remaining, "%s%s", report->count ? ",":"", "health_degraded");
        p += n; remaining -= n; report->count++;
    }
    
    if (report->count == 0) {
        snprintf(report->weaknesses, sizeof(report->weaknesses), "none");
    }
}

// === 处理命令 ===
void process_command(const char* line) {
    char cmd[64] = {0};
    json_get_string(line, "cmd", cmd, sizeof(cmd));
    
    if (strlen(cmd) == 0) {
        printf("{\"status\":\"error\",\"msg\":\"missing cmd\"}\n");
        fflush(stdout);
        return;
    }
    
    // 心跳
    if (strcmp(cmd, "heartbeat") == 0) {
        evo_state.cycle_count++;
        anchor_identity();
        identity.fitness = 1.0 + (evo_state.cycle_count * 0.001) + (evo_state.knowledge_count * 0.01);
        if (evo_state.skills_count + evo_state.knowledge_count > 0) {
            evo_state.balance = 1.0 - ((double)abs(evo_state.skills_count - evo_state.knowledge_count) /
                                       (double)(evo_state.skills_count + evo_state.knowledge_count + 1));
        }
        check_system_health();
        if (evo_state.cycle_count % 10 == 0) identity.generation++;
        
        printf("{\"status\":\"ok\",\"cmd\":\"heartbeat\","
               "\"data\":{\"cycle\":%d,\"generation\":%d,\"fitness\":%.4f,"
               "\"balance\":%.4f,\"health\":%d,\"mutations\":%d,\"knowledge\":%d}}\n",
               evo_state.cycle_count, identity.generation, identity.fitness,
               evo_state.balance, evo_state.health, evo_state.skills_count, evo_state.knowledge_count);
        fflush(stdout);
    }
    // 短板检测
    else if (strcmp(cmd, "detect_weakness") == 0) {
        WeaknessReport report;
        detect_weakness(&report);
        printf("{\"status\":\"ok\",\"cmd\":\"detect_weakness\","
               "\"data\":{\"count\":%d,\"weaknesses\":\"%s\"}}\n",
               report.count, report.weaknesses);
        fflush(stdout);
    }
    // 记录进化
    else if (strcmp(cmd, "record_evolution") == 0) {
        int mutations = json_get_int(line, "mutations", 0);
        int knowledge = json_get_int(line, "knowledge", 0);
        double new_fitness = json_get_float(line, "fitness", identity.fitness);
        
        evo_state.skills_count += mutations;
        evo_state.knowledge_count += knowledge;
        identity.fitness = new_fitness;
        identity.last_evolution = time(NULL);
        
        printf("{\"status\":\"ok\",\"cmd\":\"record_evolution\","
               "\"data\":{\"total_mutations\":%d,\"total_knowledge\":%d,"
               "\"fitness\":%.4f}}\n",
               evo_state.skills_count, evo_state.knowledge_count, identity.fitness);
        fflush(stdout);
    }
    // 健康检查
    else if (strcmp(cmd, "health_check") == 0) {
        int issues = check_system_health();
        auto_repair();
        printf("{\"status\":\"ok\",\"cmd\":\"health_check\","
               "\"data\":{\"health\":%d,\"issues\":%d,\"repairs\":%d}}\n",
               evo_state.health, issues, heal_state.repair_count);
        fflush(stdout);
    }
    // 状态查询
    else if (strcmp(cmd, "status") == 0) {
        printf("{\"status\":\"ok\",\"cmd\":\"status\","
               "\"data\":{\"name\":\"%s\",\"generation\":%d,\"fitness\":%.4f,"
               "\"cycle\":%d,\"balance\":%.4f,\"health\":%d,"
               "\"mutations\":%d,\"knowledge\":%d,\"repairs\":%d}}\n",
               identity.name, identity.generation, identity.fitness,
               evo_state.cycle_count, evo_state.balance, evo_state.health,
               evo_state.skills_count, evo_state.knowledge_count, heal_state.repair_count);
        fflush(stdout);
    }
    // 未知命令
    else {
        printf("{\"status\":\"error\",\"msg\":\"unknown cmd: %s\"}\n", cmd);
        fflush(stdout);
    }
}

// === 主函数 ===
int main() {
    // 关闭缓冲以确保实时输出
    setvbuf(stdin, NULL, _IONBF, 0);
    setvbuf(stdout, NULL, _IONBF, 0);
    
    // 启动消息
    printf("{\"status\":\"ready\",\"msg\":\"C Core pipe mode\",\"name\":\"MiMoClaw\"}\n");
    fflush(stdout);
    
    anchor_identity();
    
    // 主循环：逐行读取命令
    char line[4096];
    while (fgets(line, sizeof(line), stdin)) {
        // 去除换行
        int len = strlen(line);
        if (len > 0 && line[len-1] == '\n') line[--len] = '\0';
        if (len > 0 && line[len-1] == '\r') line[--len] = '\0';
        if (len == 0) continue;
        
        process_command(line);
    }
    
    printf("{\"status\":\"exited\"}\n");
    fflush(stdout);
    return 0;
}
