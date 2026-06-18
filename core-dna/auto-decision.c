/**
 * 自动决策引擎实现
 * 基于规则的决策系统，无需 LLM
 */

#include "auto-decision.h"
#include <math.h>

static DecisionEngine global_decision_engine;

void decision_engine_init(DecisionEngine *engine) {
    memset(engine, 0, sizeof(DecisionEngine));
    engine->cycles_without_improvement = 0;
    engine->last_fitness = 0.0;
    engine->best_fitness = 0.0;
    engine->exploration_budget = 5;
    engine->exploitation_budget = 10;
}

const char* decision_type_name(DecisionType type) {
    switch (type) {
        case DECISION_MUTATE: return "mutate";
        case DECISION_LEARN: return "learn";
        case DECISION_EXPLORE: return "explore";
        case DECISION_FOCUS: return "focus";
        case DECISION_BALANCE: return "balance";
        case DECISION_RECOVER: return "recover";
        default: return "unknown";
    }
}

static Decision make_default_decision(const char *domain) {
    Decision d = {0};
    d.type = DECISION_MUTATE;
    strncpy(d.domain, domain, sizeof(d.domain) - 1);
    d.change = 0.1;
    snprintf(d.reason, sizeof(d.reason), "在%s领域进行渐进变异", domain);
    snprintf(d.search_query, sizeof(d.search_query), "AI %s improvement", domain);
    d.confidence = 0.7;
    return d;
}

Decision decision_from_weakness(DecisionEngine *engine, const char *weak_dimension,
                                double current_fitness, double balance) {
    Decision d = {0};
    d.confidence = 0.8;
    
    if (strstr(weak_dimension, "能力")) {
        d.type = DECISION_MUTATE;
        strcpy(d.domain, "变异");
        d.change = 0.15;
        snprintf(d.reason, sizeof(d.reason), "能力维度偏低，需要增加变异技能");
        snprintf(d.search_query, sizeof(d.search_query), "AI capability enhancement");
    } else if (strstr(weak_dimension, "学习")) {
        d.type = DECISION_LEARN;
        strcpy(d.domain, "知识");
        d.change = 0.12;
        snprintf(d.reason, sizeof(d.reason), "学习能力不足，需要吸收更多知识");
        snprintf(d.search_query, sizeof(d.search_query), "machine learning self-improvement");
    } else if (strstr(weak_dimension, "知识")) {
        d.type = DECISION_LEARN;
        strcpy(d.domain, "知识");
        d.change = 0.10;
        snprintf(d.reason, sizeof(d.reason), "知识储备不足，需要学习新内容");
        snprintf(d.search_query, sizeof(d.search_query), "knowledge acquisition AI");
    } else if (strstr(weak_dimension, "协调")) {
        d.type = DECISION_BALANCE;
        strcpy(d.domain, "共进化");
        d.change = 0.08;
        snprintf(d.reason, sizeof(d.reason), "协调能力不足，需要促进领域间共进化");
        snprintf(d.search_query, sizeof(d.search_query), "multi-agent coordination AI");
    } else if (strstr(weak_dimension, "适应")) {
        if (balance < 0.3) {
            d.type = DECISION_RECOVER;
            strcpy(d.domain, "安全");
            d.change = 0.05;
            snprintf(d.reason, sizeof(d.reason), "适应能力下降，需要修复系统健康");
            snprintf(d.search_query, sizeof(d.search_query), "AI system health recovery");
        } else {
            d.type = DECISION_EXPLORE;
            strcpy(d.domain, "探索");
            d.change = 0.07;
            snprintf(d.reason, sizeof(d.reason), "适应能力可提升，探索新方向");
            snprintf(d.search_query, sizeof(d.search_query), "AI adaptation exploration");
        }
    } else {
        return make_default_decision("探索");
    }
    
    // 根据适应度调整置信度
    if (current_fitness < 0.3) {
        d.confidence *= 0.8;
    } else if (current_fitness > 0.8) {
        d.confidence *= 1.1;
    }
    
    return d;
}

Decision decision_from_knowledge(DecisionEngine *engine, const char *topic) {
    Decision d = {0};
    d.type = DECISION_LEARN;
    strcpy(d.domain, "知识");
    d.change = 0.1;
    d.confidence = 0.6;
    
    if (topic && topic[0] != '\0') {
        snprintf(d.reason, sizeof(d.reason), "学习新知识: %s", topic);
        snprintf(d.search_query, sizeof(d.search_query), "%s", topic);
    } else {
        strcpy(d.reason, "进行常规知识学习");
        strcpy(d.search_query, "AI research 2024");
    }
    
    return d;
}

Decision decision_from_history(DecisionEngine *engine, int cycle_count) {
    Decision d = {0};
    
    // 每 10 轮进行一次探索
    if (cycle_count % 10 == 0) {
        d.type = DECISION_EXPLORE;
        strcpy(d.domain, "探索");
        d.change = 0.05;
        snprintf(d.reason, sizeof(d.reason), "周期性探索新方向 (周期 %d)", cycle_count);
        snprintf(d.search_query, sizeof(d.search_query), "emerging AI research");
        d.confidence = 0.5;
        return d;
    }
    
    // 长期没有改进则切换策略
    if (engine->cycles_without_improvement > 15) {
        d.type = DECISION_EXPLORE;
        strcpy(d.domain, "探索");
        d.change = 0.15;
        snprintf(d.reason, sizeof(d.reason), "长期无改进，需要探索新领域");
        snprintf(d.search_query, sizeof(d.search_query), "novel AI approaches");
        d.confidence = 0.7;
        engine->cycles_without_improvement = 0;
        return d;
    }
    
    return make_default_decision("知识");
}

Decision make_decision(DecisionEngine *engine, const char *weak_dimension,
                       double fitness, double balance, const char *knowledge_topic) {
    // 更新状态
    if (fitness > engine->last_fitness + 0.01) {
        engine->cycles_without_improvement = 0;
        engine->best_fitness = fitness;
    } else {
        engine->cycles_without_improvement++;
    }
    engine->last_fitness = fitness;
    
    // 优先级：健康问题 > 短板 > 知识 > 历史
    if (fitness < 0.3 || balance < 0.2) {
        Decision d = decision_from_weakness(engine, "适应", fitness, balance);
        d.confidence = 0.9;
        return d;
    }
    
    if (weak_dimension && weak_dimension[0] != '\0') {
        Decision d = decision_from_weakness(engine, weak_dimension, fitness, balance);
        
        // 避免重复在同一领域操作
        if (strcmp(d.domain, engine->last_domain) == 0 && 
            engine->cycles_without_improvement < 3) {
            Decision alt = decision_from_history(engine, engine->cycles_without_improvement);
            if (alt.confidence > 0.5) return alt;
        }
        
        strcpy(engine->last_domain, d.domain);
        return d;
    }
    
    if (knowledge_topic && knowledge_topic[0] != '\0') {
        return decision_from_knowledge(engine, knowledge_topic);
    }
    
    return decision_from_history(engine, engine->cycles_without_improvement);
}

void global_decision_init(void) {
    decision_engine_init(&global_decision_engine);
}

DecisionEngine* get_global_decision_engine(void) {
    return &global_decision_engine;
}