# GPT 修复方案

生成时间: 2026-05-18T10:52:30.495637

下面给出可直接落地的修复代码片段（中文注释），按 5 项分别实现。

---

## 1) 缓存签名校验：`decision-cache.h` 添加 MD5 校验

> 适用于 C/C++ 头文件，给缓存内容加签名校验。  
> 需要在 `.c/.cpp` 中实现 `md5_hex()`。

```c
// decision-cache.h
#pragma once
#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

// 计算字符串 MD5（32位十六进制字符串）
// 由实现文件提供
int md5_hex(const unsigned char* data, size_t len, char out_hex[33]);

// 计算缓存签名：对 payload 做 MD5
static inline int decision_cache_calc_signature(const char* payload, char out_md5[33]) {
    if (!payload || !out_md5) return -1;
    return md5_hex((const unsigned char*)payload, __builtin_strlen(payload), out_md5);
}

// 校验缓存内容与签名是否一致
static inline int decision_cache_verify_signature(const char* payload, const char* expected_md5) {
    char actual[33] = {0};
    if (!payload || !expected_md5) return 0;
    if (decision_cache_calc_signature(payload, actual) != 0) return 0;
    return __builtin_strcmp(actual, expected_md5) == 0;
}

#ifdef __cplusplus
}
#endif
```

---

## 2) 进化评估指标：C core 添加适应度 / 错误率 / 知识覆盖率统计

```c
// evolution_metrics.h
#pragma once
#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    double fitness;         // 适应度
    double error_rate;      // 错误率
    double knowledge_cov;   // 知识覆盖率
    size_t total_samples;   // 样本数
    size_t correct_samples; // 正确数
    size_t error_samples;   // 错误数
    size_t knowledge_hits;  // 命中知识数
    size_t knowledge_total; // 知识总数
} EvoMetrics;

// 初始化
static inline void evo_metrics_init(EvoMetrics* m) {
    if (!m) return;
    *m = (EvoMetrics){0};
}

// 更新统计
static inline void evo_metrics_update(EvoMetrics* m, int correct, int knowledge_hit, size_t knowledge_total) {
    if (!m) return;

    m->total_samples++;
    if (correct) m->correct_samples++;
    else m->error_samples++;

    if (knowledge_hit) m->knowledge_hits++;
    m->knowledge_total = knowledge_total;

    // 适应度：正确率 * 知识覆盖率
    double acc = (m->total_samples == 0) ? 0.0 : (double)m->correct_samples / (double)m->total_samples;
    double cov = (m->knowledge_total == 0) ? 0.0 : (double)m->knowledge_hits / (double)m->knowledge_total;
    m->fitness = acc * cov;

    // 错误率
    m->error_rate = (m->total_samples == 0) ? 0.0 : (double)m->error_samples / (double)m->total_samples;

    // 知识覆盖率
    m->knowledge_cov = cov;
}

#ifdef __cplusplus
}
#endif
```

**用法示例：**
```c
EvoMetrics metrics;
evo_metrics_init(&metrics);

// 每次评估后调用
evo_metrics_update(&metrics, /*correct=*/1, /*knowledge_hit=*/1, /*knowledge_total=*/64);
```

---

## 3) 磁盘监控告警：C core 添加磁盘空间告警逻辑

```c
// disk_monitor.h
#pragma once
#include <stdio.h>

#ifdef __cplusplus
extern "C" {
#endif

// 返回 1 表示告警，0 表示正常
static inline int disk_warn_if_low(const char* mount_path, double warn_pct) {
    if (!mount_path) return 0;

    FILE* fp = popen("df -P / | tail -1", "r");
    if (!fp) return 0;

    char line[256] = {0};
    if (!fgets(line, sizeof(line), fp)) {
        pclose(fp);
        return 0;
    }
    pclose(fp);

    // 解析使用率：示例 " /dev/nvme0n1p3  40G  31G  6.5G  83% /"
    int used = 0;
    if (sscanf(line, "%*s %*s %*s %*s %d%% %*s", &used) != 1) return 0;

    if ((double)used >= warn_pct) {
        fprintf(stderr, "[WARN] 磁盘空间告警：挂载点=%s 使用率=%d%% 阈值=%.1f%%\n",
                mount_path, used, warn_pct);
        return 1;
    }
    return 0;
}

#ifdef __cplusplus
}
#endif
```

**用法示例：**
```c
// 每隔一段时间调用
disk_warn_if_low("/", 80.0);
```

---

## 4) 安全输入过滤：C core 添加输入验证函数

```c
// input_filter.h
#pragma once
#include <ctype.h>
#include <stddef.h>
#include <string.h>

#ifdef __cplusplus
extern "C" {
#endif

// 仅允许可打印字符、长度受限、禁止明显危险字符
static inline int validate_input_safe(const char* s, size_t max_len) {
    if (!s) return 0;

    size_t n = 0;
    for (; s[n] != '\0'; ++n) {
        if (n >= max_len) return 0;

        unsigned char c = (unsigned char)s[n];

        // 禁止控制字符
        if (c < 32 || c == 127) return 0;

        // 禁止常见注入/逃逸字符（按需可放宽）
        if (c == '`' || c == '$' || c == '\\') return 0;
    }

    return n > 0;
}

#ifdef __cplusplus
}
#endif
```

**用法示例：**
```c
if (!validate_input_safe(user_input, 1024)) {
    fprintf(stderr, "[ERROR] 非法输入\n");
    return -1;
}
```

---

## 5) 知识库 TTL：为 `paper-genes.txt` 添加过期检测

> 约定每行格式：  
> `时间戳|gene_id|content`  
> 例如：`2026-05-18T10:48:26|G001|xxx`

```c
// paper_ttl.h
#pragma once
#include <stdio.h>
#include <time.h>
#include <string.h>

#ifdef __cplusplus
extern "C" {
#endif

// 简单解析时间：YYYY-MM-DDTHH:MM:SS
static inline time_t parse_iso_time(const char* s) {
    struct tm tm = {0};
    if (!s) return (time_t)0;

    if (sscanf(s, "%d-%d-%dT%d:%d:%d",
               &tm.tm_year, &tm.tm_mon, &tm.tm_mday,
               &tm.tm_hour, &tm.tm_min, &tm.tm_sec) != 6) {
        return (time_t)0;
    }
    tm.tm_year -= 1900;
    tm.tm_mon  -= 1;
    tm.tm_isdst = -1;
    return mktime(&tm);
}

// 判断单条知识是否过期
static inline int paper_gene_is_expired(const char* iso_time, int ttl_days) {
    time_t t = parse_iso_time(iso_time);
    if (t == (time_t)0) return 1;

    time_t now = time(NULL);
    double diff_days = difftime(now, t) / 86400.0;
    return diff_days > (double)ttl_days;
}

// 扫描 paper-genes.txt，跳过过期项
static inline void paper_genes_scan_with_ttl(const char* file_path, int ttl_days) {
    FILE* fp = fopen(file_path, "r");
    if (!fp) return;

    char line[2048];
    while (fgets(line, sizeof(line), fp)) {
        // 格式：时间戳|gene_id|content
        char ts[64] = {0};
        char gene_id[64] = {0};
        char content[1800] = {0};

        if (sscanf(line, "%63[^|]|%63[^|]|%1799[^\n]", ts, gene_id, content) != 3) {
            continue; // 非法行直接跳过
        }

        if (paper_gene_is_expired(ts, ttl_days)) {
            fprintf(stderr, "[TTL] 过期知识已忽略：%s\n", gene_id);
            continue;
        }

        // TODO: 在这里加载有效知识
        fprintf(stdout, "[TTL] 有效知识：%s\n", gene_id);
    }

    fclose(fp);
}

#ifdef __cplusplus
}
#endif
```

---

## 建议集成方式

在 C core 初始化时加入：

```c
#include "disk_monitor.h"
#include "input_filter.h"
#include "evolution_metrics.h"
#include "paper_ttl.h"

// 启动时检查磁盘
disk_warn_if_low("/", 80.0);

// 读取知识库
paper_genes_scan_with_ttl("paper-genes.txt", 30);

// 处理输入前校验
if (!validate_input_safe(user_input, 1024)) {
    return -1;
}

// 迭代评估时更新指标
evo_metrics_update(&metrics, correct, knowledge_hit, 64);
```

---

如果你愿意，我可以继续把这些代码**整合成一个完整的 C 工程补丁**（包含 `.c/.h` 分文件、Makefile/CMake 集成）。