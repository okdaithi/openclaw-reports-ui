CREATE TABLE reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_date TEXT NOT NULL,
    report_type TEXT NOT NULL CHECK(report_type IN ('daily_brief', 'country', 'executive_summary', 'twitter_digest')),
    country TEXT,
    generation_timestamp TEXT NOT NULL,
    model_used TEXT NOT NULL,
    token_estimate INTEGER,
    confidence_overall REAL CHECK(confidence_overall >= 0.0 AND confidence_overall <= 1.0),
    risk_score INTEGER CHECK(risk_score >= 0 AND risk_score <= 100),
    risk_delta_7d REAL,
    volatility_score INTEGER CHECK(volatility_score >= 0 AND volatility_score <= 100),
    horizon TEXT CHECK(horizon IN ('48h', '7d', '30d')),
    systemic_risk_flag INTEGER CHECK(systemic_risk_flag IN (0, 1)),
    anomaly_flag INTEGER CHECK(anomaly_flag IN (0, 1)),
    cross_domain_alignment_score INTEGER CHECK(cross_domain_alignment_score >= 0 AND cross_domain_alignment_score <= 100),
    signal_density REAL,
    entity_diversity REAL,
    json_path TEXT NOT NULL,
    markdown_path TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE(report_date, report_type, country)
);
CREATE INDEX idx_reports_date ON reports(report_date);
CREATE INDEX idx_reports_type ON reports(report_type);
CREATE INDEX idx_reports_country ON reports(country);
CREATE INDEX idx_reports_risk ON reports(risk_score);
CREATE TABLE entities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    type TEXT NOT NULL CHECK(type IN ('country', 'organization', 'individual', 'platform', 'policy', 'infrastructure', 'event')),
    first_seen_date TEXT NOT NULL,
    total_mentions INTEGER DEFAULT 0,
    last_seen_date TEXT,
    avg_importance_score REAL,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX idx_entities_name ON entities(name);
CREATE INDEX idx_entities_type ON entities(type);
CREATE TABLE entity_mentions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id INTEGER NOT NULL,
    entity_id INTEGER NOT NULL,
    mention_count INTEGER NOT NULL DEFAULT 1,
    importance_score REAL,
    context_tags TEXT,
    FOREIGN KEY(report_id) REFERENCES reports(id) ON DELETE CASCADE,
    FOREIGN KEY(entity_id) REFERENCES entities(id) ON DELETE CASCADE,
    UNIQUE(report_id, entity_id)
);
CREATE INDEX idx_mentions_report ON entity_mentions(report_id);
CREATE INDEX idx_mentions_entity ON entity_mentions(entity_id);
CREATE TABLE signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id INTEGER NOT NULL,
    category TEXT NOT NULL CHECK(category IN ('geopolitics', 'macro', 'technology', 'supply_chain', 'security', 'governance')),
    title TEXT NOT NULL,
    summary TEXT,
    directional_bias TEXT CHECK(directional_bias IN ('positive', 'negative', 'neutral')),
    impact_score INTEGER CHECK(impact_score >= 0 AND impact_score <= 100),
    probability_estimate REAL CHECK(probability_estimate >= 0.0 AND probability_estimate <= 1.0),
    time_horizon TEXT CHECK(time_horizon IN ('immediate', '48h', '7d', '30d', '90d')),
    source_count INTEGER,
    primary_entities TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY(report_id) REFERENCES reports(id) ON DELETE CASCADE
);
CREATE INDEX idx_signals_report ON signals(report_id);
CREATE INDEX idx_signals_category ON signals(category);
CREATE INDEX idx_signals_impact ON signals(impact_score);
CREATE TABLE sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id INTEGER NOT NULL,
    url TEXT,
    domain TEXT NOT NULL,
    timestamp TEXT,
    source_type TEXT CHECK(source_type IN ('primary', 'secondary', 'social', 'aggregator')),
    reliability_estimate REAL CHECK(reliability_estimate >= 0.0 AND reliability_estimate <= 1.0),
    FOREIGN KEY(report_id) REFERENCES reports(id) ON DELETE CASCADE
);
CREATE INDEX idx_sources_report ON sources(report_id);
CREATE INDEX idx_sources_domain ON sources(domain);
CREATE TABLE system_health (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    metric_date TEXT NOT NULL UNIQUE,
    json_success_count INTEGER DEFAULT 0,
    json_failure_count INTEGER DEFAULT 0,
    db_write_avg_ms REAL,
    db_write_max_ms REAL,
    total_reports_generated INTEGER DEFAULT 0,
    total_entities_tracked INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX idx_health_date ON system_health(metric_date);
CREATE VIEW v_country_risk_trend AS
SELECT 
    country,
    report_date,
    risk_score,
    risk_delta_7d,
    volatility_score,
    systemic_risk_flag,
    anomaly_flag
FROM reports
WHERE report_type = 'country'
ORDER BY country, report_date DESC
/* v_country_risk_trend(country,report_date,risk_score,risk_delta_7d,volatility_score,systemic_risk_flag,anomaly_flag) */;
CREATE VIEW v_entity_cooccurrence AS
SELECT 
    e1.name AS entity_1,
    e2.name AS entity_2,
    COUNT(DISTINCT em1.report_id) AS cooccurrence_count,
    MAX(r.report_date) AS last_cooccurrence
FROM entity_mentions em1
JOIN entity_mentions em2 ON em1.report_id = em2.report_id AND em1.entity_id < em2.entity_id
JOIN entities e1 ON em1.entity_id = e1.id
JOIN entities e2 ON em2.entity_id = e2.id
JOIN reports r ON em1.report_id = r.id
GROUP BY e1.name, e2.name
HAVING cooccurrence_count > 1
ORDER BY cooccurrence_count DESC
/* v_entity_cooccurrence(entity_1,entity_2,cooccurrence_count,last_cooccurrence) */;
CREATE VIEW v_signal_heatmap_7d AS
SELECT 
    category,
    COUNT(*) AS signal_count,
    AVG(impact_score) AS avg_impact,
    AVG(probability_estimate) AS avg_probability,
    SUM(CASE WHEN directional_bias = 'negative' THEN 1 ELSE 0 END) AS negative_count,
    SUM(CASE WHEN directional_bias = 'positive' THEN 1 ELSE 0 END) AS positive_count
FROM signals s
JOIN reports r ON s.report_id = r.id
WHERE r.report_date >= date('now', '-7 days')
GROUP BY category
ORDER BY avg_impact DESC
/* v_signal_heatmap_7d(category,signal_count,avg_impact,avg_probability,negative_count,positive_count) */;
CREATE VIEW v_top_entities_30d AS
SELECT 
    e.name,
    e.type,
    COUNT(DISTINCT em.report_id) AS report_count,
    SUM(em.mention_count) AS total_mentions,
    AVG(em.importance_score) AS avg_importance,
    MAX(r.report_date) AS last_seen
FROM entities e
JOIN entity_mentions em ON e.id = em.entity_id
JOIN reports r ON em.report_id = r.id
WHERE r.report_date >= date('now', '-30 days')
GROUP BY e.name, e.type
ORDER BY avg_importance DESC
LIMIT 50
/* v_top_entities_30d(name,type,report_count,total_mentions,avg_importance,last_seen) */;
