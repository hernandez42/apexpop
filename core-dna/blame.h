// blame.h — 失败归因机制（Multi-Agent Self-Evolution）
// 每次失败记录：谁做的、什么时候、改了什么、导致什么后果

#ifndef BLAME_H
#define BLAME_H

#include <time.h>

typedef struct {
    char agent[32];      // 谁做的（C core / Rust / Python）
    char action[256];    // 做了什么
    char result[256];    // 导致什么后果
    int success;         // 是否成功
    time_t timestamp;    // 时间
} BlameRecord;

#define MAX_BLAME 100

static BlameRecord blame_history[MAX_BLAME];
static int blame_count = 0;

// 记录失败
void blame_record(const char *agent, const char *action, const char *result, int success) {
    if (blame_count >= MAX_BLAME) return;
    BlameRecord *r = &blame_history[blame_count++];
    strncpy(r->agent, agent, sizeof(r->agent) - 1);
    r->agent[sizeof(r->agent) - 1] = '\0';
    strncpy(r->action, action, sizeof(r->action) - 1);
    r->action[sizeof(r->action) - 1] = '\0';
    strncpy(r->result, result, sizeof(r->result) - 1);
    r->result[sizeof(r->result) - 1] = '\0';
    r->success = success;
    r->timestamp = time(NULL);
}

// 分析失败原因
static const char* __attribute__((unused)) blame_analyze(void) {
    static char last_agent[32] = "";
    int fail_count = 0;
    memset(last_agent, 0, sizeof(last_agent));
    for (int i = 0; i < blame_count; i++) {
        if (!blame_history[i].success) {
            fail_count++;
            memcpy(last_agent, blame_history[i].agent, sizeof(last_agent));
            last_agent[sizeof(last_agent) - 1] = '\0';
        }
    }
    if (fail_count == 0) return "无失败记录";
    if (fail_count >= 5) return "频繁失败，需要检查";
    return last_agent[0] ? last_agent : "未知";
}

// 保存到文件
void blame_save(void) {
    FILE *f = fopen("../memory/blame-log.jsonl", "a");
    if (!f) return;
    for (int i = 0; i < blame_count; i++) {
        BlameRecord *r = &blame_history[i];
        fprintf(f, "{\"agent\":\"%s\",\"action\":\"%s\",\"result\":\"%s\",\"success\":%d,\"ts\":%ld}\n",
                r->agent, r->action, r->result, r->success, (long)r->timestamp);
    }
    fclose(f);
    blame_count = 0;
}

#endif
