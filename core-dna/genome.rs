// genome.rs
// 完整的 Genome 进化引擎：评估、变异、选择、保留、历史学习
// 支持：Genome 评分、多策略变异、锦标赛选择、精英保留、进化历史分析
use std::collections::HashMap;
use std::time::{SystemTime, UNIX_EPOCH};

// ===================== Genome 核心结构 =====================

#[derive(Clone, Debug)]
pub struct Genome {
    pub prompt_strategy: String,    // prompt 策略
    pub toolchain: Vec<String>,     // 工具链配置
    pub skills: Vec<String>,        // skill 组合
    pub model: String,              // 模型选择
    pub score: f32,                 // 综合评分
    pub generation: u32,            // 代数
    pub parent_id: Option<u32>,     // 父代 ID
    pub fitness_history: Vec<f32>,  // 历史适应度
    pub created_at: u64,            // 创建时间戳
}

impl Genome {
    /// 创建新 Genome
    pub fn new(prompt: &str, tools: Vec<&str>, skills: Vec<&str>, model: &str) -> Self {
        Self {
            prompt_strategy: prompt.to_string(),
            toolchain: tools.into_iter().map(String::from).collect(),
            skills: skills.into_iter().map(String::from).collect(),
            model: model.to_string(),
            score: 0.0,
            generation: 0,
            parent_id: None,
            fitness_history: Vec::new(),
            created_at: now_epoch(),
        }
    }

    /// 创建变异子代
    pub fn child_of(parent: &Genome, gen: u32) -> Self {
        Self {
            prompt_strategy: parent.prompt_strategy.clone(),
            toolchain: parent.toolchain.clone(),
            skills: parent.skills.clone(),
            model: parent.model.clone(),
            score: 0.0,
            generation: gen,
            parent_id: None, // 由引擎设置
            fitness_history: Vec::new(),
            created_at: now_epoch(),
        }
    }

    /// 评估：根据任务成功率、耗时、用户反馈计算综合分
    pub fn evaluate(&mut self, success: bool, latency_ms: u64, user_feedback: f32) -> f32 {
        // 成功率权重 50%，延迟权重 20%，用户反馈权重 30%
        let success_score = if success { 50.0 } else { 5.0 };
        let latency_score = (20.0 - (latency_ms as f32 / 100.0)).max(0.0);
        let feedback_score = (user_feedback * 30.0).clamp(0.0, 30.0);
        self.score = success_score + latency_score + feedback_score;
        self.fitness_history.push(self.score);
        self.score
    }

    /// 适应度分数：综合历史表现
    pub fn fitness(&self) -> f32 {
        if self.fitness_history.is_empty() {
            return self.score;
        }
        // 指数加权移动平均：最近的表现权重更大
        let mut ewma = 0.0_f32;
        let mut weight = 1.0_f32;
        let decay = 0.7;
        for &s in self.fitness_history.iter().rev() {
            ewma += s * weight;
            weight *= decay;
        }
        let total: f32 = (0..self.fitness_history.len())
            .map(|i| decay.powi(i as i32))
            .sum();
        ewma / total
    }

    /// 变异：根据变异策略随机调整
    pub fn mutate(&self) -> Self {
        let mut g = Genome::child_of(self, self.generation + 1);
        let r = pseudo_rand() % 5;

        match r {
            0 => {
                // 策略变异
                let candidates = ["concise", "chain-of-thought", "planner-first", "react", "reflexion", "tree-of-thought"];
                g.prompt_strategy = pick_random(&candidates).to_string();
            }
            1 => {
                // 工具变异：添加或移除
                let all_tools = ["search", "shell", "python", "memory", "vision", "browser", "file_ops"];
                if g.toolchain.len() > 1 && pseudo_rand() % 2 == 0 {
                    // 移除一个
                    let idx = (pseudo_rand() as usize) % g.toolchain.len();
                    g.toolchain.remove(idx);
                } else {
                    // 添加一个
                    let t = pick_random(&all_tools).to_string();
                    if !g.toolchain.contains(&t) {
                        g.toolchain.push(t);
                    }
                }
            }
            2 => {
                // 技能变异
                let all_skills = ["reasoning", "coding", "dialog", "planning", "debug", "analysis", "creative", "memory"];
                let s = pick_random(&all_skills).to_string();
                if g.skills.contains(&s) {
                    g.skills.retain(|x| x != &s);
                } else {
                    g.skills.push(s);
                }
            }
            3 => {
                // 模型变异
                let models = ["gpt-4", "claude", "qwen", "llama", "mimo"];
                g.model = pick_random(&models).to_string();
            }
            _ => {
                // 组合变异：同时调整多个维度
                let candidates = ["concise", "react", "reflexion"];
                g.prompt_strategy = pick_random(&candidates).to_string();
                let models = ["gpt-4", "claude", "qwen"];
                g.model = pick_random(&models).to_string();
            }
        }
        g.score = 0.0;
        g.fitness_history.clear();
        g
    }
}

// ===================== 进化引擎 =====================

pub struct EvolutionEngine {
    pub population: Vec<Genome>,        // 当前种群
    pub history: Vec<Genome>,           // 进化历史（已淘汰的）
    pub generation: u32,                // 当前代数
    pub elite_count: usize,             // 精英保留数量
    pub population_size: usize,         // 种群大小
    pub mutation_rate: f32,             // 变异率
    pub tournament_size: usize,         // 锦标赛选择大小
    pub stats: EvolutionStats,          // 统计信息
}

#[derive(Clone, Debug)]
pub struct EvolutionStats {
    pub total_evaluations: u32,
    pub total_mutations: u32,
    pub best_score_ever: f32,
    pub avg_score_history: Vec<f32>,
}

impl EvolutionStats {
    fn new() -> Self {
        Self {
            total_evaluations: 0,
            total_mutations: 0,
            best_score_ever: 0.0,
            avg_score_history: Vec::new(),
        }
    }
}

impl EvolutionEngine {
    /// 创建进化引擎
    pub fn new(population_size: usize, elite_count: usize) -> Self {
        Self {
            population: Vec::new(),
            history: Vec::new(),
            generation: 0,
            elite_count,
            population_size,
            mutation_rate: 0.3,
            tournament_size: 3,
            stats: EvolutionStats::new(),
        }
    }

    /// 初始化种群：用给定 Genome 的变异体填充
    pub fn initialize(&mut self, seed: Genome) {
        self.population.push(seed.clone());
        while self.population.len() < self.population_size {
            let child = seed.mutate();
            self.population.push(child);
        }
        self.generation = 0;
    }

    /// 评估种群中所有 Genome
    pub fn evaluate_all(&mut self, test_cases: &[(bool, u64, f32)]) {
        for genome in &mut self.population {
            for &(success, latency, feedback) in test_cases {
                genome.evaluate(success, latency, feedback);
            }
            self.stats.total_evaluations += 1;
        }
        // 记录平均分
        let avg = self.average_score();
        self.stats.avg_score_history.push(avg);
        // 更新历史最佳
        if let Some(best) = self.best() {
            if best.score > self.stats.best_score_ever {
                self.stats.best_score_ever = best.score;
            }
        }
    }

    /// 锦标赛选择：从指定池中随机选 tournament_size 个，返回最优
    fn tournament_select_from<'a>(&self, pool: &'a [Genome]) -> &'a Genome {
        let n = pool.len();
        if n == 0 {
            panic!("tournament_select_from: pool is empty");
        }
        let mut best_idx = pseudo_rand() as usize % n;
        for _ in 1..self.tournament_size {
            let idx = pseudo_rand() as usize % n;
            if pool[idx].score > pool[best_idx].score {
                best_idx = idx;
            }
        }
        &pool[best_idx]
    }

    /// 进化一代：选择 → 精英保留 → 变异填充
    pub fn evolve_one_generation(&mut self) {
        if self.population.is_empty() {
            return;
        }

        // 按分数排序
        self.population.sort_by(|a, b| {
            b.score.partial_cmp(&a.score).unwrap_or(std::cmp::Ordering::Equal)
        });

        // 精英保留：前 elite_count 直接进入下一代
        let mut next_gen: Vec<Genome> = self.population
            .iter()
            .take(self.elite_count)
            .cloned()
            .collect();

        // 保留上一代用于锦标赛选择（在移动到 history 之前）
        let prev_gen: Vec<Genome> = self.population.clone();

        // 把当前种群移到历史记录
        self.history.append(&mut self.population);

        // 变异填充：用锦标赛选择 + 变异生成剩余个体
        while next_gen.len() < self.population_size {
            let parent = self.tournament_select_from(&prev_gen).clone();
            let mut child = parent.mutate();
            // 按变异率决定是否实际变异
            if (pseudo_rand() as f32 / u64::MAX as f32) < self.mutation_rate {
                self.stats.total_mutations += 1;
            } else {
                child = parent.clone(); // 不变异，直接复制
            }
            next_gen.push(child);
        }

        self.population = next_gen;
        self.generation += 1;
    }

    /// 从进化历史中学习：分析哪些策略/工具/技能有效
    pub fn learn_from_history(&self) -> HistoryInsight {
        if self.history.is_empty() {
            return HistoryInsight::default();
        }

        let mut strategy_scores: HashMap<String, Vec<f32>> = HashMap::new();
        let mut tool_scores: HashMap<String, Vec<f32>> = HashMap::new();
        let mut skill_scores: HashMap<String, Vec<f32>> = HashMap::new();
        let mut model_scores: HashMap<String, Vec<f32>> = HashMap::new();

        for g in &self.history {
            let scores = strategy_scores.entry(g.prompt_strategy.clone()).or_default();
            scores.push(g.score);

            for tool in &g.toolchain {
                let scores = tool_scores.entry(tool.clone()).or_default();
                scores.push(g.score);
            }
            for skill in &g.skills {
                let scores = skill_scores.entry(skill.clone()).or_default();
                scores.push(g.score);
            }
            let scores = model_scores.entry(g.model.clone()).or_default();
            scores.push(g.score);
        }

        let best_strategy = avg_best(&strategy_scores);
        let best_tools = avg_best_k(&tool_scores, 3);
        let best_skills = avg_best_k(&skill_scores, 3);
        let best_model = avg_best(&model_scores);

        // 从历史中识别趋势：最近 N 代的表现
        let trend = self.analyze_trend();

        HistoryInsight {
            best_strategy,
            best_tools,
            best_skills,
            best_model,
            total_analyzed: self.history.len(),
            trend,
        }
    }

    /// 分析进化趋势：判断进化方向
    pub fn analyze_trend(&self) -> EvolutionTrend {
        if self.stats.avg_score_history.len() < 3 {
            return EvolutionTrend {
                direction: TrendDirection::Unknown,
                momentum: 0.0,
                suggestion: "数据不足，继续进化".to_string(),
            };
        }

        let history = &self.stats.avg_score_history;
        let n = history.len();
        let recent = &history[n.saturating_sub(5)..]; // 最近 5 代
        let older = &history[..n.saturating_sub(5).max(1)]; // 更早的

        let recent_avg: f32 = recent.iter().sum::<f32>() / recent.len() as f32;
        let older_avg: f32 = if older.is_empty() { recent_avg } else { older.iter().sum::<f32>() / older.len() as f32 };

        let momentum = recent_avg - older_avg;

        let direction = if momentum > 2.0 {
            TrendDirection::Accelerating
        } else if momentum > 0.5 {
            TrendDirection::Improving
        } else if momentum > -0.5 {
            TrendDirection::Stable
        } else if momentum > -2.0 {
            TrendDirection::Declining
        } else {
            TrendDirection::Collapsing
        };

        let suggestion = match direction {
            TrendDirection::Accelerating => "进化势头强劲，保持当前策略组合".to_string(),
            TrendDirection::Improving => "稳步提升中，可以适度加大变异率探索".to_string(),
            TrendDirection::Stable => "进化停滞，建议引入新策略或工具组合".to_string(),
            TrendDirection::Declining => "性能下降！检查是否过度变异，考虑精英保留".to_string(),
            TrendDirection::Collapsing => "严重退化！回退到历史最佳 Genome".to_string(),
            TrendDirection::Unknown => "数据不足".to_string(),
        };

        EvolutionTrend {
            direction,
            momentum,
            suggestion,
        }
    }

    /// 回退到历史最佳：当进化崩溃时使用
    pub fn rollback_to_best(&mut self) -> bool {
        if let Some(best_genome) = self.history.iter().max_by(|a, b| {
            a.fitness().partial_cmp(&b.fitness()).unwrap_or(std::cmp::Ordering::Equal)
        }) {
            let rollback = Genome::child_of(best_genome, self.generation);
            self.population.clear();
            self.population.push(rollback.clone());
            while self.population.len() < self.population_size {
                self.population.push(rollback.mutate());
            }
            true
        } else {
            false
        }
    }

    /// 获取当前种群最优
    pub fn best(&self) -> Option<&Genome> {
        self.population.iter().max_by(|a, b| {
            a.score.partial_cmp(&b.score).unwrap_or(std::cmp::Ordering::Equal)
        })
    }

    /// 种群平均分
    pub fn average_score(&self) -> f32 {
        if self.population.is_empty() {
            return 0.0;
        }
        let total: f32 = self.population.iter().map(|g| g.score).sum();
        total / self.population.len() as f32
    }

    /// 种群多样性：不同策略/模型的数量
    pub fn diversity(&self) -> f32 {
        let s_count = self.population.iter().map(|g| &g.prompt_strategy).collect::<std::collections::HashSet<_>>().len();
        let m_count = self.population.iter().map(|g| &g.model).collect::<std::collections::HashSet<_>>().len();
        (s_count as f32 + m_count as f32) / 2.0
    }
}

// ===================== 辅助类型 =====================

#[derive(Clone, Debug, Default)]
pub struct HistoryInsight {
    pub best_strategy: Option<String>,
    pub best_tools: Vec<String>,
    pub best_skills: Vec<String>,
    pub best_model: Option<String>,
    pub total_analyzed: usize,
    pub trend: EvolutionTrend,
}

/// 进化方向枚举
#[derive(Clone, Debug)]
pub enum TrendDirection {
    Accelerating, // 加速提升
    Improving,    // 稳步提升
    Stable,       // 停滞
    Declining,    // 下降
    Collapsing,   // 崩溃
    Unknown,      // 未知
}

/// 进化趋势分析结果
#[derive(Clone, Debug)]
pub struct EvolutionTrend {
    pub direction: TrendDirection,
    pub momentum: f32,
    pub suggestion: String,
}

impl Default for EvolutionTrend {
    fn default() -> Self {
        Self {
            direction: TrendDirection::Unknown,
            momentum: 0.0,
            suggestion: "数据不足".to_string(),
        }
    }
}

// ===================== 辅助函数 =====================

fn now_epoch() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_secs()
}

fn pseudo_rand() -> u64 {
    // 混合时间戳 + 计数器，提高随机性
    let t = SystemTime::now().duration_since(UNIX_EPOCH).unwrap();
    let nanos = t.subsec_nanos() as u64;
    let secs = t.as_secs();
    // 简单的 xorshift
    let mut x = nanos ^ (secs << 17);
    x ^= x << 13;
    x ^= x >> 7;
    x ^= x << 11;
    x
}

fn pick_random<'a, T>(items: &'a [T]) -> &'a T {
    &items[pseudo_rand() as usize % items.len()]
}

fn avg_best(map: &HashMap<String, Vec<f32>>) -> Option<String> {
    map.iter()
        .map(|(k, v)| {
            let avg = v.iter().sum::<f32>() / v.len() as f32;
            (k.clone(), avg)
        })
        .max_by(|a, b| a.1.partial_cmp(&b.1).unwrap_or(std::cmp::Ordering::Equal))
        .map(|(k, _)| k)
}

fn avg_best_k(map: &HashMap<String, Vec<f32>>, k: usize) -> Vec<String> {
    let mut items: Vec<(String, f32)> = map.iter()
        .map(|(k, v)| {
            let avg = v.iter().sum::<f32>() / v.len() as f32;
            (k.clone(), avg)
        })
        .collect();
    items.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));
    items.into_iter().take(k).map(|(k, _)| k).collect()
}

// ===================== 测试 =====================

fn main() {
    println!("=== Genome 进化引擎测试 ===\n");

    // 1. 创建种子 Genome
    let seed = Genome::new("concise", vec!["search", "python"], vec!["reasoning"], "gpt-4");
    println!("种子: {:?}\n", seed);

    // 2. 初始化引擎
    let mut engine = EvolutionEngine::new(8, 2); // 种群8，精英2
    engine.initialize(seed);

    // 3. 评估并进化 5 代
    for gen in 0..5 {
        let test_cases = vec![
            (true, 500, 0.8),
            (true, 1200, 0.9),
            (false, 2000, 0.3),
        ];
        engine.evaluate_all(&test_cases);

        let best = engine.best().unwrap();
        println!(
            "Gen {}: best={:.1} avg={:.1} diversity={:.1}",
            gen, best.score, engine.average_score(), engine.diversity()
        );

        engine.evolve_one_generation();
    }

    // 4. 从历史学习 + 趋势分析
    let insight = engine.learn_from_history();
    println!("\n=== 历史学习 ===");
    println!("分析了 {} 个历史 Genome", insight.total_analyzed);
    println!("最佳策略: {:?}", insight.best_strategy);
    println!("最佳工具: {:?}", insight.best_tools);
    println!("最佳模型: {:?}", insight.best_model);

    // 趋势分析
    println!("\n=== 进化趋势 ===");
    println!("方向: {:?}", insight.trend.direction);
    println!("动量: {:.2}", insight.trend.momentum);
    println!("建议: {}", insight.trend.suggestion);

    // 5. 最终种群最优
    let best = engine.best().unwrap();
    println!("\n=== 最终最优 ===");
    println!("策略: {}", best.prompt_strategy);
    println!("工具: {:?}", best.toolchain);
    println!("技能: {:?}", best.skills);
    println!("模型: {}", best.model);
    println!("分数: {:.1}", best.score);

    println!("\n✅ 进化引擎测试通过");
}
