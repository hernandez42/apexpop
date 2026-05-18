// evolution-metrics.h — 进化评估指标
#ifndef EVOLUTION_METRICS_H
#define EVOLUTION_METRICS_H

typedef struct {
    double fitness;
    double error_rate;
    double knowledge_cov;
    int total_samples;
    int correct_samples;
    int error_samples;
    int knowledge_hits;
    int knowledge_total;
} EvoMetrics;

static inline void evo_metrics_init(EvoMetrics *m) {
    if (!m) return;
    m->fitness = 0.0;
    m->error_rate = 0.0;
    m->knowledge_cov = 0.0;
    m->total_samples = 0;
    m->correct_samples = 0;
    m->error_samples = 0;
    m->knowledge_hits = 0;
    m->knowledge_total = 0;
}

static inline void evo_metrics_update(EvoMetrics *m, int correct, int knowledge_hit, int knowledge_total) {
    if (!m) return;
    m->total_samples++;
    if (correct) m->correct_samples++;
    else m->error_samples++;
    if (knowledge_hit) m->knowledge_hits++;
    m->knowledge_total = knowledge_total;
    double acc = m->total_samples ? (double)m->correct_samples / m->total_samples : 0.0;
    double cov = m->knowledge_total ? (double)m->knowledge_hits / m->knowledge_total : 0.0;
    m->fitness = acc * (0.5 + 0.5 * cov);
    m->error_rate = m->total_samples ? (double)m->error_samples / m->total_samples : 0.0;
    m->knowledge_cov = cov;
}

#endif
