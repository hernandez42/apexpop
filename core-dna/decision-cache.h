// decision-cache.h — 决策缓存（STIR 机制）
// 常见决策直接用缓存答案，不问 LLM

#ifndef DECISION_CACHE_H
#define DECISION_CACHE_H

#define MAX_CACHE 20

typedef struct {
    char query[128];
    char answer[256];
    int hit_count;
} DecisionCache;

static DecisionCache dcache[MAX_CACHE];
static int dcache_count = 0;

// 查找缓存
const char* cache_lookup(const char *query) {
    for (int i = 0; i < dcache_count; i++) {
        if (strcmp(dcache[i].query, query) == 0) {
            dcache[i].hit_count++;
            return dcache[i].answer;
        }
    }
    return NULL;
}

// 保存到缓存
void cache_save(const char *query, const char *answer) {
    if (dcache_count >= MAX_CACHE) return;
    // 检查是否已存在
    for (int i = 0; i < dcache_count; i++) {
        if (strcmp(dcache[i].query, query) == 0) {
            strncpy(dcache[i].answer, answer, sizeof(dcache[i].answer) - 1);
            return;
        }
    }
    DecisionCache *c = &dcache[dcache_count++];
    strncpy(c->query, query, sizeof(c->query) - 1);
    strncpy(c->answer, answer, sizeof(c->answer) - 1);
    c->hit_count = 1;
}

#endif
