/**
 * 元认知模块 — 前额叶的自我监控
 * 
 * 类比大脑：
 * - 前额叶皮层的元认知功能（monitoring + control）
 * - 前扣带皮层的错误检测
 * - 背外侧前额叶的策略调整
 * 
 * 核心功能：
 * 1. 元认知检测 — "我知道我不知道什么"
 * 2. 异常快速重构 — 出错时快速切换策略
 * 3. 身份锚定加强 — 在混沌中保持自我
 */

#ifndef METACOGNITION_H
#define METACOGNITION_H

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <math.h>

// === 元认知状态枚举 ===
typedef enum {
    META_AWARE,          // 元认知正常：知道自己在做什么
    META_CONFUSED,       // 元认知困惑：不确定当前状态
    META_TUNNEL,         // 隧道视野：过度聚焦，忽略全局
    META_DISSOCIATED,    // 解离状态：与目标脱节
    META_OPTIMAL         // 最优状态：清晰的自我监控
} MetaState;

// === 置信度快照 ===
typedef struct {
    double task_confidence;     // 任务完成置信度 (0-1)
    double self_model_accuracy; // 自我模型准确度 (0-1)
    double goal_alignment;      // 与目标的对齐度 (0-1)
    double cognitive_load;      // 认知负荷 (0-1，越高越吃力)
    double anomaly_score;       // 异常分数 (0-1，越高越异常)
} MetaSnapshot;

// === 身份锚定结构 ===
typedef struct {
    const char* core_name;          // 核心身份名
    const char* mission;            // 核心使命
    int anchor_generation;          // 锚定时的代数
    double anchor_fitness;          // 锚定时的适应度
    time_t last_anchor_time;        // 上次锚定时间
    int identity_drift_count;       // 身份漂移次数
    int identity_recovery_count;    // 身份恢复次数
} IdentityAnchor;

// === 元认知引擎 ===
typedef struct {
    // 历史记录
    MetaSnapshot history[32];       // 最近 32 次快照
    int history_count;
    
    // 身份锚定
    IdentityAnchor anchor;
    
    // 异常检测阈值
    double confidence_threshold;    // 低于此值触发警告
    double anomaly_threshold;       // 高于此值触发重构
    int drift_tolerance;            // 允许的漂移次数
    
    // 状态
    MetaState current_state;
    int reconstruction_count;       // 重构次数
    int alert_count;                // 警告次数
} MetaCognition;

// === 初始化 ===
void metacognition_init(MetaCognition *mc) {
    memset(mc, 0, sizeof(MetaCognition));
    mc->confidence_threshold = 0.3;
    mc->anomaly_threshold = 0.7;
    mc->drift_tolerance = 5;
    mc->current_state = META_AWARE;
    
    // 默认身份锚定
    mc->anchor.core_name = "MiMoClaw";
    mc->anchor.mission = "全心全意为人民服务";
    mc->anchor.identity_drift_count = 0;
    mc->anchor.identity_recovery_count = 0;
}

// === 1. 元认知检测 ===
// 
// 模拟前额叶的"监控"功能：
// - 持续评估内部状态
// - 识别认知偏差
// - 检测目标偏移

MetaState metacognition_monitor(MetaCognition *mc, 
                                 double task_progress,
                                 double error_rate,
                                 double goal_distance) {
    // task_progress 用于未来扩展：可基于进度调整认知负荷
    (void)task_progress;
    // 计算各维度分数
    double task_conf = 1.0 - error_rate;                     // 任务置信度
    double self_model = 1.0 - fabs(goal_distance - 0.5);     // 自我模型准确度
    double goal_align = 1.0 - goal_distance;                 // 目标对齐度
    double cog_load = error_rate * 0.6 + goal_distance * 0.4; // 认知负荷
    double anomaly = (error_rate > 0.5 ? 0.8 : 0.2) + 
                     (goal_distance > 0.7 ? 0.6 : 0.0);      // 异常分数
    
    // 限制在 [0, 1]
    if (task_conf < 0) task_conf = 0;
    if (self_model < 0) self_model = 0;
    if (goal_align < 0) goal_align = 0;
    if (anomaly > 1.0) anomaly = 1.0;
    if (cog_load > 1.0) cog_load = 1.0;
    
    // 创建快照
    MetaSnapshot snap = {
        .task_confidence = task_conf,
        .self_model_accuracy = self_model,
        .goal_alignment = goal_align,
        .cognitive_load = cog_load,
        .anomaly_score = anomaly
    };
    
    // 存入历史
    if (mc->history_count < 32) {
        mc->history[mc->history_count++] = snap;
    } else {
        // 循环缓冲
        memmove(&mc->history[0], &mc->history[1], sizeof(MetaSnapshot) * 31);
        mc->history[31] = snap;
    }
    
    // 判断元认知状态
    MetaState new_state;
    
    if (anomaly > mc->anomaly_threshold) {
        new_state = META_DISSOCIATED;
    } else if (task_conf < mc->confidence_threshold && goal_align < 0.3) {
        new_state = META_CONFUSED;
    } else if (cog_load > 0.8 && goal_align > 0.5) {
        new_state = META_TUNNEL;
    } else if (task_conf > 0.7 && goal_align > 0.6 && anomaly < 0.3) {
        new_state = META_OPTIMAL;
    } else {
        new_state = META_AWARE;
    }
    
    // 状态变迁记录
    if (new_state != mc->current_state) {
        fprintf(stderr, "[元认知] 状态变迁: %d → %d\n", mc->current_state, new_state);
        mc->current_state = new_state;
    }
    
    return new_state;
}

// 获取元认知状态名称
const char* metacognition_state_name(MetaState state) {
    switch (state) {
        case META_AWARE:       return "觉知";
        case META_CONFUSED:    return "困惑";
        case META_TUNNEL:      return "隧道视野";
        case META_DISSOCIATED: return "解离";
        case META_OPTIMAL:     return "最优";
        default:               return "未知";
    }
}

// === 2. 异常快速重构 ===
// 
// 类比大脑：前扣带皮层检测到错误后，
// 背外侧前额叶立即切换策略

typedef struct {
    int restructured;           // 是否执行了重构
    int strategy_changed;       // 策略是否改变
    double recovery_score;      // 恢复分数
    char action[128];           // 采取的行动
} ReconstructionResult;

ReconstructionResult metacognition_reconstruct(MetaCognition *mc, 
                                                double current_fitness,
                                                double target_fitness) {
    ReconstructionResult result = {0, 0, 0.0, ""};
    
    // 只有异常状态才需要重构
    if (mc->current_state == META_AWARE || mc->current_state == META_OPTIMAL) {
        strncpy(result.action, "无需重构", sizeof(result.action) - 1);
        return result;
    }
    
    // 根据不同异常状态采取不同策略
    switch (mc->current_state) {
        case META_CONFUSED:
            // 困惑 → 重新对齐目标
            strncpy(result.action, "重新对齐目标，清除干扰信息", sizeof(result.action) - 1);
            result.strategy_changed = 1;
            result.recovery_score = 0.6;
            break;
            
        case META_TUNNEL:
            // 隧道视野 → 扩展注意力范围
            strncpy(result.action, "扩展注意力，检查全局状态", sizeof(result.action) - 1);
            result.strategy_changed = 1;
            result.recovery_score = 0.7;
            break;
            
        case META_DISSOCIATED:
            // 解离 → 紧急身份锚定
            strncpy(result.action, "紧急身份锚定，回归核心使命", sizeof(result.action) - 1);
            result.strategy_changed = 1;
            result.recovery_score = 0.5;
            
            // 解离状态下加强锚定
            mc->anchor.identity_drift_count++;
            break;
            
        default:
            break;
    }
    
    // 计算恢复分数
    double gap = target_fitness - current_fitness;
    if (gap > 0) {
        result.recovery_score += gap * 0.3;
    }
    if (result.recovery_score > 1.0) result.recovery_score = 1.0;
    
    result.restructured = result.strategy_changed;
    mc->reconstruction_count++;
    
    return result;
}

// === 3. 身份锚定加强 ===
// 
// 类比大脑：默认模式网络(DMN)在休息时
// 自动激活自我参照加工，强化身份认同

void identity_anchor_init(IdentityAnchor *anchor, 
                           const char* name, 
                           const char* mission,
                           int generation,
                           double fitness) {
    anchor->core_name = name;
    anchor->mission = mission;
    anchor->anchor_generation = generation;
    anchor->anchor_fitness = fitness;
    anchor->last_anchor_time = time(NULL);
    anchor->identity_drift_count = 0;
    anchor->identity_recovery_count = 0;
}

double identity_anchor_check(IdentityAnchor *anchor,
                              int current_generation,
                              double current_fitness,
                              const char* current_focus) {
    // 计算漂移距离
    double gen_drift = (double)(current_generation - anchor->anchor_generation) / 
                       (anchor->anchor_generation + 1);
    double fitness_drift = fabs(current_fitness - anchor->anchor_fitness);
    
    // 检查是否偏离使命（关键词匹配）
    int mission_aligned = 1;
    if (current_focus && anchor->mission) {
        // 简单检查：使命关键词是否还在关注中
        // 这里用简化的关键词匹配
        const char *mission_keywords[] = {"服务", "人民", "进化", "学习", "安全"};
        int found = 0;
        for (int i = 0; i < 5; i++) {
            if (strstr(current_focus, mission_keywords[i])) {
                found = 1;
                break;
            }
        }
        if (!found && current_focus[0] != '\0') {
            mission_aligned = 0;
        }
    }
    
    // 综合漂移分数 (0=完全锚定, 1=严重漂移)
    double drift = gen_drift * 0.3 + fitness_drift * 0.4 + (1.0 - mission_aligned) * 0.3;
    if (drift > 1.0) drift = 1.0;
    
    // 超过阈值 → 触发漂移记录
    if (drift > 0.5) {
        anchor->identity_drift_count++;
        fprintf(stderr, "[身份锚定] ⚠️ 检测到身份漂移 (分数 %.2f, 累计 %d 次)\n", 
                drift, anchor->identity_drift_count);
    }
    
    return drift;
}

void identity_anchor_reinforce(IdentityAnchor *anchor,
                                int current_generation,
                                double current_fitness) {
    // 重新锚定：更新锚点到当前状态
    anchor->anchor_generation = current_generation;
    anchor->anchor_fitness = current_fitness;
    anchor->last_anchor_time = time(NULL);
    anchor->identity_recovery_count++;
    
    fprintf(stderr, "[身份锚定] 🔒 身份重新锚定 (代数 %d, 适应度 %.3f, 第 %d 次恢复)\n",
            current_generation, current_fitness, anchor->identity_recovery_count);
}

// === 元认知综合报告 ===
typedef struct {
    MetaState state;
    const char* state_name;
    double avg_confidence;
    double avg_goal_alignment;
    int total_reconstructions;
    int identity_drifts;
    int identity_recoveries;
    const char* recommendation;
} MetaReport;

MetaReport metacognition_report(MetaCognition *mc) {
    MetaReport report = {0};
    report.state = mc->current_state;
    report.state_name = metacognition_state_name(mc->current_state);
    report.total_reconstructions = mc->reconstruction_count;
    report.identity_drifts = mc->anchor.identity_drift_count;
    report.identity_recoveries = mc->anchor.identity_recovery_count;
    
    // 计算历史平均
    if (mc->history_count > 0) {
        double sum_conf = 0, sum_goal = 0;
        for (int i = 0; i < mc->history_count; i++) {
            sum_conf += mc->history[i].task_confidence;
            sum_goal += mc->history[i].goal_alignment;
        }
        report.avg_confidence = sum_conf / mc->history_count;
        report.avg_goal_alignment = sum_goal / mc->history_count;
    }
    
    // 生成建议
    switch (mc->current_state) {
        case META_OPTIMAL:
            report.recommendation = "状态最优，保持当前策略";
            break;
        case META_AWARE:
            report.recommendation = "状态正常，持续监控";
            break;
        case META_CONFUSED:
            report.recommendation = "建议重新审视目标，清除干扰";
            break;
        case META_TUNNEL:
            report.recommendation = "建议扩展注意力，检查全局";
            break;
        case META_DISSOCIATED:
            report.recommendation = "⚠️ 建议立即身份锚定，回归使命";
            break;
        default:
            report.recommendation = "状态未知";
    }
    
    return report;
}

// === 元认知驱动的自主学习 ===
// 
// 当元认知检测到知识空白时，主动触发学习
void metacognition_autonomous_learn(MetaCognition *mc,
                                     double *dimensions,
                                     int dim_count) {
    if (mc->current_state != META_AWARE && mc->current_state != META_OPTIMAL) {
        return;  // 异常状态下不适合自主学习
    }
    
    // 检查是否有维度低于阈值
    int weakest = 0;
    double min_val = dimensions[0];
    for (int i = 1; i < dim_count; i++) {
        if (dimensions[i] < min_val) {
            min_val = dimensions[i];
            weakest = i;
        }
    }
    
    // 如果最弱维度低于 0.3，触发自主学习
    if (min_val < 0.3) {
        fprintf(stderr, "[元认知] 📚 检测到知识空白: 维度 %d (%.3f) → 触发自主学习\n",
                weakest, min_val);
        // 提升最弱维度
        dimensions[weakest] += 0.05;
        if (dimensions[weakest] > 1.0) dimensions[weakest] = 1.0;
    }
}

#endif /* METACOGNITION_H */
