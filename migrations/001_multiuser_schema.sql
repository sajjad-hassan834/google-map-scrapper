-- Migration 001: Initial Multi-User Schema
-- Maps leads, searches, usage, keys, plans, and audit logs to user_id

CREATE TABLE IF NOT EXISTS schema_migrations (
    version VARCHAR(64) PRIMARY KEY,
    applied_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS plans (
    id VARCHAR(32) PRIMARY KEY,
    name VARCHAR(64) NOT NULL,
    monthly_searches INTEGER NOT NULL,
    can_use_deep_search BOOLEAN DEFAULT FALSE,
    can_use_own_key BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Seed default plans
INSERT INTO plans (id, name, monthly_searches, can_use_deep_search, can_use_own_key)
VALUES 
    ('free', 'Free', 10, FALSE, TRUE),
    ('pro', 'Pro', 100, FALSE, TRUE),
    ('owner', 'Owner', 999999, TRUE, TRUE)
ON CONFLICT (id) DO NOTHING;

CREATE TABLE IF NOT EXISTS users (
    id VARCHAR(64) PRIMARY KEY,
    email VARCHAR(255) UNIQUE NOT NULL,
    display_name VARCHAR(255),
    role VARCHAR(32) NOT NULL DEFAULT 'member',
    plan_id VARCHAR(32) NOT NULL DEFAULT 'free' REFERENCES plans(id),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    custom_monthly_limit INTEGER,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    last_active_at TIMESTAMP WITH TIME ZONE
);

CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
CREATE INDEX IF NOT EXISTS idx_users_created_at ON users(created_at);

CREATE TABLE IF NOT EXISTS platform_settings (
    key VARCHAR(64) PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Default safety limits for shared Google Places API Key
INSERT INTO platform_settings (key, value)
VALUES 
    ('global_monthly_limit', '1000'),
    ('global_safety_buffer', '50'),
    ('cache_retention_days', '30')
ON CONFLICT (key) DO NOTHING;

CREATE TABLE IF NOT EXISTS leads (
    id VARCHAR(128) NOT NULL,
    user_id VARCHAR(64) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    phone TEXT,
    website TEXT,
    has_website BOOLEAN DEFAULT FALSE,
    rating REAL DEFAULT 0,
    reviews INTEGER DEFAULT 0,
    address TEXT,
    category TEXT,
    google_maps_url TEXT,
    opportunity_tier VARCHAR(64) DEFAULT 'STANDARD',
    stage VARCHAR(64) DEFAULT 'New Lead',
    notes TEXT DEFAULT '',
    deal_value REAL DEFAULT 1500.0,
    follow_up_date TEXT DEFAULT '',
    social_links TEXT DEFAULT '{}',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_leads_user_id ON leads(user_id);
CREATE INDEX IF NOT EXISTS idx_leads_created_at ON leads(created_at);
CREATE INDEX IF NOT EXISTS idx_leads_stage ON leads(stage);

CREATE TABLE IF NOT EXISTS searches (
    id SERIAL PRIMARY KEY,
    user_id VARCHAR(64) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    query TEXT NOT NULL,
    location_asked TEXT,
    search_engine VARCHAR(32) NOT NULL DEFAULT 'places',
    pages_requested INTEGER DEFAULT 1,
    credits_used INTEGER DEFAULT 1,
    key_type VARCHAR(16) DEFAULT 'shared',
    cached BOOLEAN DEFAULT FALSE,
    total_results INTEGER DEFAULT 0,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_searches_user_id ON searches(user_id);
CREATE INDEX IF NOT EXISTS idx_searches_created_at ON searches(created_at);

CREATE TABLE IF NOT EXISTS usage_events (
    id SERIAL PRIMARY KEY,
    user_id VARCHAR(64) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    month VARCHAR(7) NOT NULL,
    event_type VARCHAR(32) NOT NULL,
    credits_spent INTEGER NOT NULL DEFAULT 1,
    key_type VARCHAR(16) NOT NULL DEFAULT 'shared',
    description TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_usage_user_id ON usage_events(user_id);
CREATE INDEX IF NOT EXISTS idx_usage_month ON usage_events(month);
CREATE INDEX IF NOT EXISTS idx_usage_created_at ON usage_events(created_at);

CREATE TABLE IF NOT EXISTS api_keys (
    id SERIAL PRIMARY KEY,
    user_id VARCHAR(64) UNIQUE NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    encrypted_key TEXT NOT NULL,
    key_last_four VARCHAR(4) NOT NULL,
    is_valid BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_api_keys_user_id ON api_keys(user_id);

CREATE TABLE IF NOT EXISTS audit_log (
    id SERIAL PRIMARY KEY,
    user_id VARCHAR(64),
    action VARCHAR(64) NOT NULL,
    ip_address VARCHAR(45),
    details TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_audit_user_id ON audit_log(user_id);
CREATE INDEX IF NOT EXISTS idx_audit_created_at ON audit_log(created_at);
