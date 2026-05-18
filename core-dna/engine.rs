/**
 * Rust 引擎 — 高性能进化引擎（v2：增强 JSON 支持）
 * 骨骼和肌肉：执行 C core 的决策
 * 
 * 核心职责：
 * 1. 变异执行（高效计算）
 * 2. 选择评估（快速评估）
 * 3. 保留管理（高效存储）
 * 4. 状态监控（实时检测）
 * 
 * JSON 协议：
 * 5. 手写 JSON 解析器（无需 serde/serde_json）
 * 6. 手写 JSON 生成器
 * 7. C core 通信接口（stdin/stdout JSON 协议）
 */

use std::collections::HashMap;
use std::fmt;
use std::fs;
use std::io::{self, BufRead, Write};
use std::time::{SystemTime, UNIX_EPOCH};

// =============================================================================
// 手写 JSON 序列化/反序列化（无需任何外部依赖）
// =============================================================================

/// JSON 值类型枚举 —— 覆盖标准 JSON 所有类型
#[derive(Debug, Clone, PartialEq)]
enum JsonValue {
    Null,
    Bool(bool),
    Number(f64),
    String(String),
    Array(Vec<JsonValue>),
    Object(Vec<(String, JsonValue)>),
}

impl JsonValue {
    /// 便捷构造：对象
    fn object(pairs: Vec<(&str, JsonValue)>) -> Self {
        JsonValue::Object(pairs.into_iter().map(|(k, v)| (k.to_string(), v)).collect())
    }

    /// 便捷构造：字符串
    fn str(s: &str) -> Self {
        JsonValue::String(s.to_string())
    }

    /// 便捷构造：数字
    fn num(n: f64) -> Self {
        JsonValue::Number(n)
    }

    /// 便捷构造：布尔
    fn bool(b: bool) -> Self {
        JsonValue::Bool(b)
    }

    /// 便捷构造：数组
    fn array(items: Vec<JsonValue>) -> Self {
        JsonValue::Array(items)
    }

    // --- 字段提取方法 ---

    fn as_str(&self) -> Option<&str> {
        if let JsonValue::String(s) = self { Some(s) } else { None }
    }

    fn as_f64(&self) -> Option<f64> {
        if let JsonValue::Number(n) = self { Some(*n) } else { None }
    }

    fn as_object(&self) -> Option<&Vec<(String, JsonValue)>> {
        if let JsonValue::Object(pairs) = self { Some(pairs) } else { None }
    }

    fn as_array(&self) -> Option<&Vec<JsonValue>> {
        if let JsonValue::Array(arr) = self { Some(arr) } else { None }
    }

    /// 获取对象中某个 key 的值
    fn get(&self, key: &str) -> Option<&JsonValue> {
        self.as_object()?.iter().find(|(k, _)| k == key).map(|(_, v)| v)
    }

}

impl fmt::Display for JsonValue {
    fn fmt(&self, f: &mut fmt::Formatter) -> fmt::Result {
        match self {
            JsonValue::Null => write!(f, "null"),
            JsonValue::Bool(b) => write!(f, "{}", b),
            JsonValue::Number(n) => {
                if *n == (*n as i64) as f64 && n.abs() < 1e15 {
                    write!(f, "{}", *n as i64)
                } else {
                    write!(f, "{}", n)
                }
            }
            JsonValue::String(s) => write!(f, "\"{}\"", json_escape(s)),
            JsonValue::Array(arr) => {
                write!(f, "[")?;
                for (i, item) in arr.iter().enumerate() {
                    if i > 0 { write!(f, ",")?; }
                    write!(f, "{}", item)?;
                }
                write!(f, "]")
            }
            JsonValue::Object(pairs) => {
                write!(f, "{{")?;
                for (i, (k, v)) in pairs.iter().enumerate() {
                    if i > 0 { write!(f, ",")?; }
                    write!(f, "\"{}\":{}", json_escape(k), v)?;
                }
                write!(f, "}}")
            }
        }
    }
}

/// JSON 字符串转义
fn json_escape(s: &str) -> String {
    let mut out = String::with_capacity(s.len() * 2);
    for c in s.chars() {
        match c {
            '"'  => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            '\u{0008}' => out.push_str("\\b"),
            '\u{000C}' => out.push_str("\\f"),
            c if c < '\u{0020}' => {
                out.push_str(&format!("\\u{:04x}", c as u32));
            }
            c => out.push(c),
        }
    }
    out
}

// =============================================================================
// JSON 解析器（递归下降，支持所有 JSON 类型）
// =============================================================================

struct JsonParser {
    chars: Vec<char>,
    pos: usize,
}

impl JsonParser {
    fn new(input: &str) -> Self {
        JsonParser {
            chars: input.chars().collect(),
            pos: 0,
        }
    }

    fn skip_whitespace(&mut self) {
        while self.pos < self.chars.len() && self.chars[self.pos].is_whitespace() {
            self.pos += 1;
        }
    }

    fn peek(&mut self) -> Option<char> {
        self.skip_whitespace();
        self.chars.get(self.pos).copied()
    }

    fn advance(&mut self) -> Option<char> {
        let c = self.chars.get(self.pos).copied();
        if c.is_some() { self.pos += 1; }
        c
    }

    fn expect(&mut self, expected: char) -> Result<(), String> {
        match self.advance() {
            Some(c) if c == expected => Ok(()),
            Some(c) => Err(format!("Expected '{}', got '{}' at position {}", expected, c, self.pos)),
            None => Err(format!("Expected '{}', got EOF", expected)),
        }
    }

    /// 解析一个完整的 JSON 值
    fn parse_value(&mut self) -> Result<JsonValue, String> {
        self.skip_whitespace();
        match self.peek() {
            Some('{') => self.parse_object(),
            Some('[') => self.parse_array(),
            Some('"') => self.parse_string().map(JsonValue::String),
            Some('t') | Some('f') => self.parse_bool(),
            Some('n') => self.parse_null(),
            Some(c) if c == '-' || c.is_ascii_digit() => self.parse_number(),
            Some(c) => Err(format!("Unexpected character '{}' at position {}", c, self.pos)),
            None => Err("Unexpected end of input".to_string()),
        }
    }

    fn parse_object(&mut self) -> Result<JsonValue, String> {
        self.expect('{')?;
        self.skip_whitespace();

        let mut pairs = Vec::new();

        // 空对象
        if self.peek() == Some('}') {
            self.advance();
            return Ok(JsonValue::Object(pairs));
        }

        loop {
            // key
            let key = self.parse_string()?;
            // colon
            self.expect(':')?;
            self.skip_whitespace();
            // value
            let value = self.parse_value()?;
            pairs.push((key, value));

            match self.peek() {
                Some(',') => { self.advance(); }
                Some('}') => { self.advance(); break; }
                other => return Err(format!("Expected ',' or '}}', got {:?}", other)),
            }
        }

        Ok(JsonValue::Object(pairs))
    }

    fn parse_array(&mut self) -> Result<JsonValue, String> {
        self.expect('[')?;
        self.skip_whitespace();

        let mut items = Vec::new();

        // 空数组
        if self.peek() == Some(']') {
            self.advance();
            return Ok(JsonValue::Array(items));
        }

        loop {
            let value = self.parse_value()?;
            items.push(value);

            match self.peek() {
                Some(',') => { self.advance(); }
                Some(']') => { self.advance(); break; }
                other => return Err(format!("Expected ',' or ']', got {:?}", other)),
            }
        }

        Ok(JsonValue::Array(items))
    }

    fn parse_string(&mut self) -> Result<String, String> {
        self.skip_whitespace();
        self.expect('"')?;
        let mut s = String::new();
        loop {
            match self.advance() {
                Some('"') => return Ok(s),
                Some('\\') => {
                    match self.advance() {
                        Some('"')  => s.push('"'),
                        Some('\\') => s.push('\\'),
                        Some('/')  => s.push('/'),
                        Some('n')  => s.push('\n'),
                        Some('r')  => s.push('\r'),
                        Some('t')  => s.push('\t'),
                        Some('b')  => s.push('\u{0008}'),
                        Some('f')  => s.push('\u{000C}'),
                        Some('u') => {
                            let mut hex = String::new();
                            for _ in 0..4 {
                                match self.advance() {
                                    Some(c) if c.is_ascii_hexdigit() => hex.push(c),
                                    other => return Err(format!("Invalid \\u escape, expected hex digit, got {:?}", other)),
                                }
                            }
                            let code = u32::from_str_radix(&hex, 16)
                                .map_err(|_| format!("Invalid hex in \\u escape: {}", hex))?;
                            let c = char::from_u32(code)
                                .ok_or_else(|| format!("Invalid Unicode code point: {}", hex))?;
                            s.push(c);
                        }
                        Some(c) => s.push(c),
                        None => return Err("Unexpected EOF in string escape".to_string()),
                    }
                }
                Some(c) => s.push(c),
                None => return Err("Unterminated string".to_string()),
            }
        }
    }

    fn parse_number(&mut self) -> Result<JsonValue, String> {
        let start = self.pos;

        // 可选负号
        if self.peek() == Some('-') { self.advance(); }

        // 整数部分
        while self.pos < self.chars.len() && self.chars[self.pos].is_ascii_digit() {
            self.pos += 1;
        }

        // 小数部分
        if self.peek() == Some('.') {
            self.advance();
            while self.pos < self.chars.len() && self.chars[self.pos].is_ascii_digit() {
                self.pos += 1;
            }
        }

        // 指数部分
        if self.peek() == Some('e') || self.peek() == Some('E') {
            self.advance();
            if self.peek() == Some('+') || self.peek() == Some('-') {
                self.advance();
            }
            while self.pos < self.chars.len() && self.chars[self.pos].is_ascii_digit() {
                self.pos += 1;
            }
        }

        let num_str: String = self.chars[start..self.pos].iter().collect();
        let n: f64 = num_str.parse().map_err(|_e| {
            format!("Invalid number: {}", num_str)
        })?;
        Ok(JsonValue::Number(n))
    }

    fn parse_bool(&mut self) -> Result<JsonValue, String> {
        let start = self.pos;
        while self.pos < self.chars.len() && self.chars[self.pos].is_alphabetic() {
            self.pos += 1;
        }
        let word: String = self.chars[start..self.pos].iter().collect();
        match word.as_str() {
            "true" => Ok(JsonValue::Bool(true)),
            "false" => Ok(JsonValue::Bool(false)),
            other => Err(format!("Expected 'true' or 'false', got '{}'", other)),
        }
    }

    fn parse_null(&mut self) -> Result<JsonValue, String> {
        let start = self.pos;
        while self.pos < self.chars.len() && self.chars[self.pos].is_alphabetic() {
            self.pos += 1;
        }
        let word: String = self.chars[start..self.pos].iter().collect();
        if word == "null" {
            Ok(JsonValue::Null)
        } else {
            Err(format!("Expected 'null', got '{}'", word))
        }
    }
}

/// 解析 JSON 字符串为 JsonValue
fn json_parse(input: &str) -> Result<JsonValue, String> {
    let mut parser = JsonParser::new(input);
    let value = parser.parse_value()?;
    parser.skip_whitespace();
    if parser.pos < parser.chars.len() {
        return Err(format!("Unexpected trailing content at position {}", parser.pos));
    }
    Ok(value)
}

// =============================================================================
// 便捷解析函数（兼容旧接口，从 JsonValue 提取字段）
// =============================================================================

// =============================================================================
// 基因定义
// =============================================================================

#[derive(Debug, Clone)]
struct Gene {
    id: String,
    domain: String,
    strength: f64,
    generation: u32,
    created_at: u64,
    last_used: u64,
    use_count: u32,
}

impl Gene {
    /// 基因 → JsonValue
    fn to_json(&self) -> JsonValue {
        JsonValue::object(vec![
            ("id", JsonValue::str(&self.id)),
            ("domain", JsonValue::str(&self.domain)),
            ("strength", JsonValue::num(self.strength)),
            ("generation", JsonValue::num(self.generation as f64)),
            ("created_at", JsonValue::num(self.created_at as f64)),
            ("last_used", JsonValue::num(self.last_used as f64)),
            ("use_count", JsonValue::num(self.use_count as f64)),
        ])
    }

    /// JsonValue → Gene
    fn from_json(val: &JsonValue) -> Option<Self> {
        Some(Gene {
            id: val.get("id")?.as_str()?.to_string(),
            domain: val.get("domain")?.as_str()?.to_string(),
            strength: val.get("strength")?.as_f64()?,
            generation: val.get("generation")?.as_f64()? as u32,
            created_at: val.get("created_at")?.as_f64()? as u64,
            last_used: val.get("last_used")?.as_f64()? as u64,
            use_count: val.get("use_count")?.as_f64()? as u32,
        })
    }
}

fn genes_to_json(genes: &[Gene]) -> JsonValue {
    JsonValue::array(genes.iter().map(|g| g.to_json()).collect())
}

fn json_to_genes(val: &JsonValue) -> Vec<Gene> {
    let arr = match val.as_array() {
        Some(a) => a,
        None => return Vec::new(),
    };
    arr.iter().filter_map(|v| Gene::from_json(v)).collect()
}

// =============================================================================
// Rust 自愈模块
// =============================================================================

struct SelfHealer {
    consecutive_failures: u32,
    max_consecutive: u32,
    circuit_open_until: u64,
    cooldown_seconds: u64,
}

impl SelfHealer {
    fn new() -> Self {
        SelfHealer {
            consecutive_failures: 0,
            max_consecutive: 3,
            circuit_open_until: 0,
            cooldown_seconds: 60,
        }
    }

    fn now_epoch() -> u64 {
        SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_secs()
    }

    /// 熔断器检查
    fn circuit_check(&mut self) -> bool {
        if self.consecutive_failures >= self.max_consecutive {
            let now = Self::now_epoch();
            if now < self.circuit_open_until {
                eprintln!("[Rust] 🔴 熔断中 (剩余 {}s)", self.circuit_open_until - now);
                return true;
            } else {
                eprintln!("[Rust] 🟢 熔断冷却结束");
                self.consecutive_failures = 0;
            }
        }
        false
    }

    /// 记录修复结果
    fn record_result(&mut self, success: bool) {
        if success {
            self.consecutive_failures = 0;
        } else {
            self.consecutive_failures += 1;
            if self.consecutive_failures >= self.max_consecutive {
                self.circuit_open_until = Self::now_epoch() + self.cooldown_seconds;
                eprintln!("[Rust] 🔴 连续失败 {} 次 → 熔断 {}s",
                    self.consecutive_failures, self.cooldown_seconds);
            }
        }
    }

    /// 计算基因库哈希（检测变化）
    fn snapshot_hash(genes: &[Gene]) -> u64 {
        let mut h: u64 = 14695981039346656037; // FNV-1a offset
        for g in genes {
            for b in g.domain.as_bytes() {
                h ^= *b as u64;
                h = h.wrapping_mul(1099511628211);
            }
            let s_bits = (g.strength * 1000.0) as u64;
            h ^= s_bits;
            h = h.wrapping_mul(1099511628211);
        }
        h
    }

}

// =============================================================================
// 进化引擎
// =============================================================================

struct EvolutionEngine {
    genes: Vec<Gene>,
    balance: f64,
    cycle_count: u32,
    mutation_rate: f64,
    healer: SelfHealer,
}

impl EvolutionEngine {
    fn new() -> Self {
        EvolutionEngine {
            genes: Vec::new(),
            balance: 0.0,
            cycle_count: 0,
            mutation_rate: 0.1,
            healer: SelfHealer::new(),
        }
    }

    /// 洛书平衡计算
    fn calculate_balance(&self) -> f64 {
        let mut domain_counts: HashMap<String, f64> = HashMap::new();
        for gene in &self.genes {
            *domain_counts.entry(gene.domain.clone()).or_insert(0.0) += gene.strength;
        }

        if domain_counts.is_empty() { return 0.0; }

        let values: Vec<f64> = domain_counts.values().cloned().collect();
        let target = 2.5_f64;
        let deviation: f64 = values.iter().map(|v| (v - target).abs()).sum::<f64>() / values.len() as f64;
        (1.0 - deviation / target).max(0.0)
    }

    /// 变异执行
    fn mutate(&mut self, domain: &str, change: f64) -> bool {
        if change.abs() > self.mutation_rate * 10.0 {
            eprintln!("[Rust] ⚠️ 变异幅度过大: {} (限制 {})", change, self.mutation_rate * 10.0);
            return false;
        }

        let now = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_secs();

        let gene = Gene {
            id: format!("gene-{}-{}", self.cycle_count, self.genes.len()),
            domain: domain.to_string(),
            strength: 1.0 + change,
            generation: self.cycle_count,
            created_at: now,
            last_used: now,
            use_count: 0,
        };

        eprintln!("[Rust] ✅ 变异执行: {} (强度 {:.3})", domain, gene.strength);
        self.genes.push(gene);
        self.balance = self.calculate_balance();
        true
    }

    /// 选择评估
    fn evaluate(&self, gene_id: &str) -> Option<f64> {
        self.genes.iter().find(|g| g.id == gene_id).map(|gene| {
            let use_factor = (gene.use_count as f64).sqrt() * 0.1;
            gene.strength * self.balance * (1.0 + use_factor)
        })
    }

    /// 保留管理
    fn retain(&mut self, gene_id: &str) -> bool {
        if let Some(pos) = self.genes.iter().position(|g| g.id == gene_id) {
            let gene = &mut self.genes[pos];
            gene.use_count += 1;
            gene.last_used = SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_secs();
            eprintln!("[Rust] ✅ 保留基因: {} (使用 {} 次)", gene_id, gene.use_count);
            true
        } else {
            false
        }
    }

    /// 自然遗忘
    fn forget_weak(&mut self, threshold: f64) -> usize {
        let before = self.genes.len();
        let now = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_secs();
        self.genes.retain(|gene| {
            let age = now.saturating_sub(gene.created_at);
            let decay = (-(age as f64) / 86400.0).exp();
            gene.strength * decay > threshold
        });
        let forgotten = before - self.genes.len();
        if forgotten > 0 {
            eprintln!("[Rust] 🗑️ 遗忘 {} 个弱基因", forgotten);
        }
        forgotten
    }

    /// 心跳
    fn heartbeat(&mut self) {
        self.cycle_count += 1;
        self.balance = self.calculate_balance();
        if self.cycle_count % 10 == 0 {
            self.forget_weak(0.1);
        }
        if self.cycle_count % 100 == 0 {
            eprintln!("[Rust] 💓 心跳 #{} | 基因 {} | 平衡度 {:.3} | 代数 {}",
                      self.cycle_count, self.genes.len(), self.balance, self.cycle_count);
        }
    }

    /// === 记忆巩固：基因计算 → 长期记忆转化 ===
    /// 类比海马体的记忆巩固过程：
    /// 1. 短期记忆（活跃基因）→ 2. 编码（强化）→ 3. 长期存储（持久化）
    fn consolidate_memory(&mut self) -> String {
        let mut consolidated = 0;
        let mut promoted = 0;
        let now = SelfHealer::now_epoch();

        // 遍历基因，执行巩固
        for i in 0..self.genes.len() {
            let gene = &mut self.genes[i];

            // 巩固条件：使用次数 >= 3 且强度 > 0.5
            if gene.use_count >= 3 && gene.strength > 0.5 {
                // 强化：强度 + 10%
                gene.strength = (gene.strength * 1.1).min(2.0);
                gene.use_count += 1;  // 巩固也算一次使用
                promoted += 1;
            }

            // 降级条件：超过 7 天未使用且强度 < 0.3
            let age_days = (now.saturating_sub(gene.last_used)) as f64 / 86400.0;
            if age_days > 7.0 && gene.strength < 0.3 {
                gene.strength *= 0.8;  // 衰减
                consolidated += 1;
            }
        }

        self.balance = self.calculate_balance();
        format!("记忆巩固完成: 强化 {} 个, 衰减 {} 个, 总计 {} 个基因",
                promoted, consolidated, self.genes.len())
    }

    /// === 快速回溯与复用 ===
    /// 类比海马体的情景记忆回溯：
    /// 根据关键词/领域快速检索相关基因
    fn recall_memory(&self, query: &str, top_k: usize) -> Vec<(String, f64, String)> {
        // 返回 (基因ID, 评分, 域名) 列表
        let mut candidates: Vec<(String, f64, String)> = self.genes.iter()
            .filter(|g| {
                // 关键词匹配：域名或基因ID包含查询词
                g.domain.to_lowercase().contains(&query.to_lowercase()) ||
                g.id.to_lowercase().contains(&query.to_lowercase())
            })
            .map(|g| {
                // 综合评分：强度 * 使用频率 * 时效性
                let recency = if g.last_used > 0 {
                    let age = SelfHealer::now_epoch().saturating_sub(g.last_used) as f64;
                    (-age / 86400.0).exp()  // 24h 衰减
                } else {
                    0.5
                };
                let score = g.strength * (1.0 + (g.use_count as f64).sqrt() * 0.1) * recency;
                (g.id.clone(), score, g.domain.clone())
            })
            .collect();

        // 按评分降序排列
        candidates.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));
        candidates.truncate(top_k);
        candidates
    }

    /// === 长期记忆持久化（带元数据）===
    fn save_long_term_memory(&self, path: &str) -> io::Result<()> {
        // 创建带元数据的 JSON 结构
        let meta = JsonValue::object(vec![
            ("version", JsonValue::num(2.0)),
            ("gene_count", JsonValue::num(self.genes.len() as f64)),
            ("balance", JsonValue::num(self.balance)),
            ("cycle", JsonValue::num(self.cycle_count as f64)),
            ("consolidated_at", JsonValue::num(SelfHealer::now_epoch() as f64)),
        ]);
        
        let genes_json = genes_to_json(&self.genes);
        let root = JsonValue::object(vec![
            ("meta", meta),
            ("genes", genes_json),
        ]);
        
        fs::write(path, root.to_string())?;
        eprintln!("[Rust] 💾 长期记忆已保存: {} ({} 个基因)", path, self.genes.len());
        Ok(())
    }

    /// 从长期记忆加载（带元数据验证）
    fn load_long_term_memory(&mut self, path: &str) -> io::Result<usize> {
        let content = fs::read_to_string(path)?;
        let val = json_parse(&content).map_err(|e| {
            io::Error::new(io::ErrorKind::InvalidData, e)
        })?;
        
        // 读取元数据
        if let Some(meta) = val.get("meta") {
            if let Some(v) = meta.get("version").and_then(|v| v.as_f64()) {
                eprintln!("[Rust] 📂 加载长期记忆 (版本 {})", v);
            }
        }
        
        // 读取基因
        let default_val = JsonValue::Array(vec![]); let genes_val = val.get("genes").unwrap_or(&default_val);
        let loaded = json_to_genes(genes_val);
        let count = loaded.len();
        self.genes.extend(loaded);
        self.balance = self.calculate_balance();
        eprintln!("[Rust] 📂 从长期记忆加载 {} 个基因 (总计 {})", count, self.genes.len());
        Ok(count)
    }

    /// 基因持久化：写入 JSON 文件
    fn save_to_file(&self, path: &str) -> io::Result<()> {
        let json = genes_to_json(&self.genes);
        fs::write(path, json.to_string())?;
        eprintln!("[Rust] 💾 基因库已保存: {} ({} 个基因)", path, self.genes.len());
        Ok(())
    }

    /// 基因持久化：从 JSON 文件加载
    fn load_from_file(&mut self, path: &str) -> io::Result<usize> {
        let content = fs::read_to_string(path)?;
        let val = json_parse(&content).map_err(|e| {
            io::Error::new(io::ErrorKind::InvalidData, e)
        })?;
        let loaded = json_to_genes(&val);
        let count = loaded.len();
        self.genes.extend(loaded);
        self.balance = self.calculate_balance();
        eprintln!("[Rust] 📂 从文件加载 {} 个基因 (总计 {})", count, self.genes.len());
        Ok(count)
    }

    /// 自愈：检测 + 修复基因库（带熔断器）
    /// 从论文学习，优化基因计算
    fn learn_from_papers(&mut self) -> String {
        let content = match fs::read_to_string("paper-genes.txt") {
            Ok(c) => c,
            Err(_) => return "论文文件不存在".to_string(),
        };

        let mut new_genes = 0;
        let lines: Vec<&str> = content.lines().collect();
        for line in lines {
            if line.starts_with("## ") {
                let paper_name = line.trim_start_matches("## ").trim();
                // 检查是否已有相关基因
                let has_gene = self.genes.iter().any(|g| g.domain.contains(paper_name));
                if !has_gene && !paper_name.is_empty() {
                    // 为每篇论文创建一个学习基因
                    let gene = Gene {
                        id: format!("paper-{}", self.genes.len()),
                        domain: paper_name.to_string(),
                        strength: 0.8,
                        generation: self.cycle_count,
                        created_at: SelfHealer::now_epoch(),
                        last_used: SelfHealer::now_epoch(),
                        use_count: 0,
                    };
                    self.genes.push(gene);
                    new_genes += 1;
                }
            }
        }

        self.balance = self.calculate_balance();
        format!("从论文学习: 新增 {} 个基因, 总计 {}", new_genes, self.genes.len())
    }

    fn self_heal(&mut self) -> String {
        if self.healer.circuit_check() {
            return "熔断中，跳过修复".to_string();
        }

        let before_hash = SelfHealer::snapshot_hash(&self.genes);
        let mut fixes: Vec<String> = Vec::new();

        // 检测 1：基因库为空
        if self.genes.is_empty() {
            eprintln!("[Rust] ⚠️ 基因库为空，尝试从文件恢复");
            if self.load_from_file("../memory/evolution-genes.json").is_ok() {
                fixes.push("从文件恢复基因库".to_string());
            } else {
                fixes.push("恢复失败（文件也为空）".to_string());
            }
        }

        // 检测 2：基因强度异常（NaN 或负数）
        let bad_count = self.genes.iter()
            .filter(|g| g.strength.is_nan() || g.strength < 0.0)
            .count();
        if bad_count > 0 {
            self.genes.retain(|g| !g.strength.is_nan() && g.strength >= 0.0);
            let msg = format!("清除 {} 个异常基因", bad_count);
            fixes.push(msg);
        }

        // 检测 3：平衡度为 0（可能有腐坏基因）
        self.balance = self.calculate_balance();
        if self.balance == 0.0 && !self.genes.is_empty() {
            if self.load_from_file("../memory/evolution-genes.json").is_ok() {
                self.balance = self.calculate_balance();
                fixes.push("平衡度异常，重新加载基因库".to_string());
            }
        }

        // 检测 4：基因文件与内存不一致
        if let Ok(content) = fs::read_to_string("../memory/evolution-genes.json") {
            if let Ok(disk_val) = json_parse(&content) {
                let disk_count = json_to_genes(&disk_val).len();
                let mem_count = self.genes.len();
                if (disk_count as i64 - mem_count as i64).abs() > 10 {
                    let msg = format!("内存({})与磁盘({})不一致，已同步", mem_count, disk_count);
                    fixes.push(msg);
                    self.genes = json_to_genes(&disk_val);
                    self.balance = self.calculate_balance();
                }
            }
        }

        let after_hash = SelfHealer::snapshot_hash(&self.genes);
        let changed = before_hash != after_hash;
        let success = !fixes.is_empty() && changed;

        self.healer.record_result(success || fixes.is_empty());

        if fixes.is_empty() {
            "✅ 无异常".to_string()
        } else {
            let report = fixes.join("; ");
            eprintln!("[Rust] 🔧 自愈完成: {}", report);
            report
        }
    }

    // === C core 通信接口：处理一条 JSON 命令 ===
    fn handle_command(&mut self, input: &str) -> String {
        // 解析输入 JSON
        let parsed = match json_parse(input) {
            Ok(v) => v,
            Err(e) => {
                return JsonValue::object(vec![
                    ("ok", JsonValue::bool(false)),
                    ("error", JsonValue::str(&format!("JSON parse error: {}", e))),
                ]).to_string();
            }
        };

        let cmd = parsed.get("cmd").and_then(|v| v.as_str()).unwrap_or("");

        match cmd {
            "mutate" => {
                let domain = parsed.get("domain").and_then(|v| v.as_str()).unwrap_or("unknown");
                let change = parsed.get("change").and_then(|v| v.as_f64()).unwrap_or(0.1);
                let ok = self.mutate(domain, change);
                JsonValue::object(vec![
                    ("ok", JsonValue::bool(ok)),
                    ("genes", JsonValue::num(self.genes.len() as f64)),
                    ("balance", JsonValue::num(self.balance)),
                ]).to_string()
            }
            "evaluate" => {
                let gene_id = parsed.get("gene_id").and_then(|v| v.as_str()).unwrap_or("");
                match self.evaluate(gene_id) {
                    Some(score) => JsonValue::object(vec![
                        ("ok", JsonValue::bool(true)),
                        ("gene_id", JsonValue::str(gene_id)),
                        ("score", JsonValue::num(score)),
                    ]).to_string(),
                    None => JsonValue::object(vec![
                        ("ok", JsonValue::bool(false)),
                        ("error", JsonValue::str("gene not found")),
                    ]).to_string(),
                }
            }
            "retain" => {
                let gene_id = parsed.get("gene_id").and_then(|v| v.as_str()).unwrap_or("");
                let ok = self.retain(gene_id);
                JsonValue::object(vec![
                    ("ok", JsonValue::bool(ok)),
                ]).to_string()
            }
            "heartbeat" => {
                self.heartbeat();
                JsonValue::object(vec![
                    ("ok", JsonValue::bool(true)),
                    ("cycle", JsonValue::num(self.cycle_count as f64)),
                    ("genes", JsonValue::num(self.genes.len() as f64)),
                    ("balance", JsonValue::num(self.balance)),
                ]).to_string()
            }
            "save" => {
                let path = parsed.get("path").and_then(|v| v.as_str()).unwrap_or("../memory/evolution-genes.json");
                match self.save_to_file(path) {
                    Ok(()) => JsonValue::object(vec![
                        ("ok", JsonValue::bool(true)),
                        ("saved", JsonValue::num(self.genes.len() as f64)),
                    ]).to_string(),
                    Err(e) => JsonValue::object(vec![
                        ("ok", JsonValue::bool(false)),
                        ("error", JsonValue::str(&e.to_string())),
                    ]).to_string(),
                }
            }
            "load" => {
                let path = parsed.get("path").and_then(|v| v.as_str()).unwrap_or("../memory/evolution-genes.json");
                match self.load_from_file(path) {
                    Ok(n) => JsonValue::object(vec![
                        ("ok", JsonValue::bool(true)),
                        ("loaded", JsonValue::num(n as f64)),
                        ("total", JsonValue::num(self.genes.len() as f64)),
                    ]).to_string(),
                    Err(e) => JsonValue::object(vec![
                        ("ok", JsonValue::bool(false)),
                        ("error", JsonValue::str(&e.to_string())),
                    ]).to_string(),
                }
            }
            "self_heal" => {
                let report = self.self_heal();
                JsonValue::object(vec![
                    ("ok", JsonValue::bool(true)),
                    ("report", JsonValue::str(&report)),
                    ("failures", JsonValue::num(self.healer.consecutive_failures as f64)),
                    ("circuit_open", JsonValue::bool(
                        SelfHealer::now_epoch() < self.healer.circuit_open_until
                    )),
                ]).to_string()
            }
            "status" => {
                JsonValue::object(vec![
                    ("ok", JsonValue::bool(true)),
                    ("cycle", JsonValue::num(self.cycle_count as f64)),
                    ("genes", genes_to_json(&self.genes)),
                    ("gene_count", JsonValue::num(self.genes.len() as f64)),
                    ("balance", JsonValue::num(self.balance)),
                    ("mutation_rate", JsonValue::num(self.mutation_rate)),
                ]).to_string()
            }
            "learn" => {
                // 从 paper-genes.txt 学习，优化基因计算
                let learnings = self.learn_from_papers();
                JsonValue::object(vec![
                    ("ok", JsonValue::bool(true)),
                    ("learnings", JsonValue::str(&learnings)),
                    ("gene_count", JsonValue::num(self.genes.len() as f64)),
                ]).to_string()
            }
            "consolidate" => {
                // 记忆巩固
                let report = self.consolidate_memory();
                JsonValue::object(vec![
                    ("ok", JsonValue::bool(true)),
                    ("report", JsonValue::str(&report)),
                    ("gene_count", JsonValue::num(self.genes.len() as f64)),
                ]).to_string()
            }
            "recall" => {
                // 快速回溯
                let query = parsed.get("query").and_then(|v| v.as_str()).unwrap_or("");
                let top_k = parsed.get("top_k").and_then(|v| v.as_f64()).unwrap_or(5.0) as usize;
                let results = self.recall_memory(query, top_k);
                let results_json: Vec<JsonValue> = results.iter().map(|(id, score, domain)| {
                    JsonValue::object(vec![
                        ("id", JsonValue::str(id)),
                        ("score", JsonValue::num(*score)),
                        ("domain", JsonValue::str(domain)),
                    ])
                }).collect();
                JsonValue::object(vec![
                    ("ok", JsonValue::bool(true)),
                    ("query", JsonValue::str(query)),
                    ("results", JsonValue::array(results_json)),
                    ("total", JsonValue::num(results.len() as f64)),
                ]).to_string()
            }
            "save_lt" => {
                // 保存长期记忆
                let path = parsed.get("path").and_then(|v| v.as_str()).unwrap_or("../memory/long-term-memory.json");
                match self.save_long_term_memory(path) {
                    Ok(()) => JsonValue::object(vec![
                        ("ok", JsonValue::bool(true)),
                        ("saved", JsonValue::num(self.genes.len() as f64)),
                    ]).to_string(),
                    Err(e) => JsonValue::object(vec![
                        ("ok", JsonValue::bool(false)),
                        ("error", JsonValue::str(&e.to_string())),
                    ]).to_string(),
                }
            }
            "load_lt" => {
                // 加载长期记忆
                let path = parsed.get("path").and_then(|v| v.as_str()).unwrap_or("../memory/long-term-memory.json");
                match self.load_long_term_memory(path) {
                    Ok(n) => JsonValue::object(vec![
                        ("ok", JsonValue::bool(true)),
                        ("loaded", JsonValue::num(n as f64)),
                        ("total", JsonValue::num(self.genes.len() as f64)),
                    ]).to_string(),
                    Err(e) => JsonValue::object(vec![
                        ("ok", JsonValue::bool(false)),
                        ("error", JsonValue::str(&e.to_string())),
                    ]).to_string(),
                }
            }
            "forget" => {
                let threshold = parsed.get("threshold").and_then(|v| v.as_f64()).unwrap_or(0.1);
                let forgotten = self.forget_weak(threshold);
                JsonValue::object(vec![
                    ("ok", JsonValue::bool(true)),
                    ("forgotten", JsonValue::num(forgotten as f64)),
                    ("remaining", JsonValue::num(self.genes.len() as f64)),
                ]).to_string()
            }
            "quit" => {
                eprintln!("[Rust] 收到退出指令，保存基因库...");
                let _ = self.save_to_file("../memory/evolution-genes.json");
                std::process::exit(0);
            }
            _ => {
                JsonValue::object(vec![
                    ("ok", JsonValue::bool(false)),
                    ("error", JsonValue::str(&format!("unknown command: {}", cmd))),
                ]).to_string()
            }
        }
    }
}

// =============================================================================
// 主函数
// =============================================================================

fn main() {
    eprintln!("=== Rust 引擎启动 ===");
    eprintln!("[Rust] 📡 等待 C core 指令 (stdin JSON 协议)");
    eprintln!("[Rust] 支持命令: mutate, evaluate, retain, heartbeat, save, load, status, forget, quit");
    eprintln!("[Rust] 示例: {{\"cmd\":\"mutate\",\"domain\":\"安全\",\"change\":0.5}}");

    let mut engine = EvolutionEngine::new();

    // 尝试加载已有基因库
    let _ = engine.load_from_file("../memory/evolution-genes.json");

    // C core 通信循环：从 stdin 读取 JSON 命令，通过 stdout 返回结果
    let stdin = io::stdin();
    let mut stdout = io::stdout();

    for line in stdin.lock().lines() {
        match line {
            Ok(input) => {
                let trimmed = input.trim();
                if trimmed.is_empty() { continue; }
                let response = engine.handle_command(trimmed);
                println!("{}", response);
                let _ = stdout.flush();
            }
            Err(e) => {
                eprintln!("[Rust] ❌ 读取错误: {}", e);
                break;
            }
        }
    }

    // stdin 关闭，保存基因库并退出
    let _ = engine.save_to_file("../memory/evolution-genes.json");
    eprintln!("[Rust] 💾 基因库已保存 ({} 个基因)", engine.genes.len());
}
