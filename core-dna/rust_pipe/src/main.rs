/**
 * Rust Engine — 管道通信版本
 * 通过 stdin 接收 JSON 命令，通过 stdout 返回 JSON 响应
 * 
 * 协议：
 *   stdin  → {"cmd":"mutate","domain":"...","change":0.1}
 *            {"cmd":"evaluate","gene_id":"..."}
 *            {"cmd":"balance"} | {"cmd":"status"} | {"cmd":"retention_check"}
 *   stdout → {"status":"ok","data":{...}} | {"status":"error","msg":"..."}
 */

use std::collections::HashMap;
use std::io::{self, BufRead, Write};
use std::time::{SystemTime, UNIX_EPOCH};

// === 基因定义 ===
#[derive(Debug, Clone)]
#[allow(dead_code)]
struct Gene {
    id: String,
    domain: String,
    strength: f64,
    generation: u32,
    created_at: u64,
    last_used: u64,
    use_count: u32,
}

// === 进化引擎 ===
struct EvolutionEngine {
    genes: Vec<Gene>,
    balance: f64,
    cycle_count: u32,
    mutation_rate: f64,
    total_mutations: u32,
    total_retentions: u32,
    total_forgets: u32,
}

impl EvolutionEngine {
    fn new() -> Self {
        EvolutionEngine {
            genes: Vec::new(),
            balance: 0.0,
            cycle_count: 0,
            mutation_rate: 0.1,
            total_mutations: 0,
            total_retentions: 0,
            total_forgets: 0,
        }
    }

    fn now() -> u64 {
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_secs()
    }

    // === 洛书平衡计算 ===
    fn calculate_balance(&self) -> f64 {
        let mut domain_counts: HashMap<String, f64> = HashMap::new();
        for gene in &self.genes {
            *domain_counts.entry(gene.domain.clone()).or_insert(0.0) += gene.strength;
        }
        if domain_counts.is_empty() {
            return 0.0;
        }
        let values: Vec<f64> = domain_counts.values().cloned().collect();
        let target = 2.5;
        let deviation: f64 = values.iter().map(|v| (v - target).abs()).sum::<f64>() / values.len() as f64;
        (1.0 - deviation / target).max(0.0)
    }

    // === 变异执行 ===
    fn mutate(&mut self, domain: &str, change: f64) -> String {
        if change.abs() > self.mutation_rate * 10.0 {
            return format!(
                "{{\"status\":\"error\",\"msg\":\"变异幅度过大: {} (限制 {})\"}}",
                change, self.mutation_rate * 10.0
            );
        }
        self.cycle_count += 1;
        let now = Self::now();
        let gene = Gene {
            id: format!("gene-{}-{}", self.cycle_count, self.genes.len()),
            domain: domain.to_string(),
            strength: 1.0 + change,
            generation: self.cycle_count,
            created_at: now,
            last_used: now,
            use_count: 0,
        };
        self.genes.push(gene.clone());
        self.total_mutations += 1;
        self.balance = self.calculate_balance();
        format!(
            "{{\"status\":\"ok\",\"cmd\":\"mutate\",\"data\":{{\"gene_id\":\"{}\",\"domain\":\"{}\",\"strength\":{:.4},\"balance\":{:.4},\"total_genes\":{}}}}}",
            gene.id, gene.domain, gene.strength, self.balance, self.genes.len()
        )
    }

    // === 选择评估 ===
    fn evaluate(&self, gene_id: &str) -> String {
        match self.genes.iter().find(|g| g.id == gene_id) {
            Some(gene) => {
                let balance_contribution = self.balance;
                let use_factor = (gene.use_count as f64).sqrt() * 0.1;
                let score = gene.strength * balance_contribution * (1.0 + use_factor);
                format!(
                    "{{\"status\":\"ok\",\"cmd\":\"evaluate\",\"data\":{{\"gene_id\":\"{}\",\"score\":{:.4},\"strength\":{:.4},\"balance_contrib\":{:.4}}}}}",
                    gene_id, score, gene.strength, balance_contribution
                )
            }
            None => format!(
                "{{\"status\":\"error\",\"msg\":\"gene not found: {}\"}}",
                gene_id
            ),
        }
    }

    // === 保留管理 ===
    fn retain(&mut self, gene_id: &str) -> String {
        if let Some(pos) = self.genes.iter().position(|g| g.id == gene_id) {
            let gene = &mut self.genes[pos];
            gene.use_count += 1;
            gene.last_used = Self::now();
            self.total_retentions += 1;
            format!(
                "{{\"status\":\"ok\",\"cmd\":\"retain\",\"data\":{{\"gene_id\":\"{}\",\"use_count\":{}}}}}",
                gene_id, gene.use_count
            )
        } else {
            format!(
                "{{\"status\":\"error\",\"msg\":\"gene not found: {}\"}}",
                gene_id
            )
        }
    }

    // === 自然遗忘 ===
    fn forget_weak(&mut self, threshold: f64) -> String {
        let before = self.genes.len();
        let now = Self::now();
        self.genes.retain(|gene| {
            let age = now.saturating_sub(gene.created_at);
            let decay = (-(age as f64) / 86400.0f64).exp();
            gene.strength * decay > threshold
        });
        let forgotten = before - self.genes.len();
        self.total_forgets += forgotten as u32;
        self.balance = self.calculate_balance();
        format!(
            "{{\"status\":\"ok\",\"cmd\":\"forget\",\"data\":{{\"forgotten\":{},\"remaining\":{},\"balance\":{:.4}}}}}",
            forgotten, self.genes.len(), self.balance
        )
    }

    // === 平衡查询 ===
    fn balance_status(&self) -> String {
        let mut domain_counts: HashMap<String, f64> = HashMap::new();
        for gene in &self.genes {
            *domain_counts.entry(gene.domain.clone()).or_insert(0.0) += gene.strength;
        }
        let domains: Vec<String> = domain_counts
            .iter()
            .map(|(k, v)| format!("\"{}\":{:.3}", k, v))
            .collect();
        format!(
            "{{\"status\":\"ok\",\"cmd\":\"balance\",\"data\":{{\"balance\":{:.4},\"domains\":{{{}}},\"gene_count\":{}}}}}",
            self.balance,
            domains.join(","),
            self.genes.len()
        )
    }

    // === 完整状态 ===
    fn status(&self) -> String {
        format!(
            "{{\"status\":\"ok\",\"cmd\":\"status\",\"data\":{{\"genes\":{},\"balance\":{:.4},\"cycle\":{},\"mutations\":{},\"retentions\":{},\"forgets\":{}}}}}",
            self.genes.len(), self.balance, self.cycle_count,
            self.total_mutations, self.total_retentions, self.total_forgets
        )
    }

    // === 保留检查（自动清理） ===
    fn retention_check(&self) -> String {
        let now = Self::now();
        let mut weak_count = 0;
        let mut strong_count = 0;
        for gene in &self.genes {
            let age = now.saturating_sub(gene.created_at);
            let decay = (-(age as f64) / 86400.0f64).exp();
            if gene.strength * decay < 0.1 {
                weak_count += 1;
            } else {
                strong_count += 1;
            }
        }
        format!(
            "{{\"status\":\"ok\",\"cmd\":\"retention_check\",\"data\":{{\"strong\":{},\"weak\":{},\"total\":{}}}}}",
            strong_count, weak_count, self.genes.len()
        )
    }
}

// === 简易 JSON 字段提取 ===
fn json_get_string(json: &str, key: &str) -> Option<String> {
    let pattern = format!("\"{}\"", key);
    let pos = json.find(&pattern)?;
    let rest = &json[pos + pattern.len()..];
    let rest = rest.trim_start().trim_start_matches(':').trim_start();
    if rest.starts_with('"') {
        let rest = &rest[1..];
        let end = rest.find('"')?;
        Some(rest[..end].to_string())
    } else {
        None
    }
}

// 提取数字字段值（支持负数、小数），直到遇到 , } 或空白
fn json_get_raw_value(json: &str, key: &str) -> Option<String> {
    let pattern = format!("\"{}\"", key);
    let pos = json.find(&pattern)?;
    let rest = &json[pos + pattern.len()..];
    let rest = rest.trim_start().trim_start_matches(':').trim_start();
    let end = rest.find(|c: char| c == ',' || c == '}' || c.is_whitespace())?;
    Some(rest[..end].to_string())
}

fn json_get_float(json: &str, key: &str, default: f64) -> f64 {
    json_get_raw_value(json, key)
        .and_then(|s| s.parse::<f64>().ok())
        .unwrap_or(default)
}

fn main() {
    // 关闭缓冲
    let stdout = io::stdout();
    let mut handle = stdout.lock();
    
    // 启动消息
    writeln!(handle, "{{\"status\":\"ready\",\"msg\":\"Rust Engine pipe mode\"}}").unwrap();
    handle.flush().unwrap();

    let mut engine = EvolutionEngine::new();
    let stdin = io::stdin();

    for line in stdin.lock().lines() {
        let line = match line {
            Ok(l) => l,
            Err(_) => break,
        };
        let line = line.trim().to_string();
        if line.is_empty() {
            continue;
        }

        let cmd = json_get_string(&line, "cmd").unwrap_or_default();
        let response = match cmd.as_str() {
            "mutate" => {
                let domain = json_get_string(&line, "domain").unwrap_or("unknown".into());
                let change = json_get_float(&line, "change", 0.1);
                engine.mutate(&domain, change)
            }
            "evaluate" => {
                let gene_id = json_get_string(&line, "gene_id").unwrap_or_default();
                engine.evaluate(&gene_id)
            }
            "retain" => {
                let gene_id = json_get_string(&line, "gene_id").unwrap_or_default();
                engine.retain(&gene_id)
            }
            "forget" => {
                let threshold = json_get_float(&line, "threshold", 0.1);
                engine.forget_weak(threshold)
            }
            "balance" => engine.balance_status(),
            "status" => engine.status(),
            "retention_check" => engine.retention_check(),
            _ => format!("{{\"status\":\"error\",\"msg\":\"unknown cmd: {}\"}}", cmd),
        };

        writeln!(handle, "{}", response).unwrap();
        handle.flush().unwrap();
    }

    writeln!(handle, "{{\"status\":\"exited\"}}").unwrap();
    handle.flush().unwrap();
}
