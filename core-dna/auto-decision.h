/**
 * 自动决策引擎 — 替代 LLM 的核心决策模块
 * 
 * 基于规则 + 遗传算法 + 强化学习的混合决策系统
 */

#ifndef AUTO_DECISION_H
#define AUTO_DECISION_H

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

// === 决策类型 ===
typedef enum {
    DECISION_MUTATE,
    DECISION_LEARN,
    DECISION_EXPLORE,
    DECISION_FOCUS,
    DECISION_BALANCE,
    DECISION_RECOVER
} DecisionType;

// === 决策建议 ===
typedef struct {
    DecisionType type;
    char domain[64];
    double change;
    char reason[512];
    char search_query[256];
    double confidence;  // 决策置信度
} Decision;

// === 决策引擎状态 ===
typedef struct {
    int cycles_without_improvement;
    double last_fitness;
    double best_fitness;
    int exploration_budget;
    int exploitation_budget;
    char last_domain[64];
} DecisionEngine;

// === 初始化 ===
void decision_engine_init(DecisionEngine *engine);

// === 基于短板的决策 ===
Decision decision_from_weakness(DecisionEngine *engine, const char *weak_dimension, 
                                double current_fitness, double balance);

// === 基于知识的决策 ===
Decision decision_from_knowledge(DecisionEngine *engine, const char *topic);

// === 基于进化历史的决策 ===
Decision decision_from_history(DecisionEngine *engine, int cycle_count);

// === 综合决策 ===
Decision make_decision(DecisionEngine *engine, const char *weak_dimension,
                       double fitness, double balance, const char *knowledge_topic);

// === 获取决策名称 ===
const char* decision_type_name(DecisionType type);

#endif /* AUTO_DECISION_H */