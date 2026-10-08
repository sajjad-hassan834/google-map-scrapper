-- Migration 002: Enable PostgreSQL Row-Level Security (RLS)
-- Isolates data per-user at the database engine level

-- Leads RLS
ALTER TABLE leads ENABLE ROW LEVEL SECURITY;

DO $$ 
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies WHERE tablename = 'leads' AND policyname = 'leads_user_isolation'
    ) THEN
        CREATE POLICY leads_user_isolation ON leads
            FOR ALL
            USING (user_id = current_setting('app.current_user_id', true));
    END IF;
END $$;

-- Searches RLS
ALTER TABLE searches ENABLE ROW LEVEL SECURITY;

DO $$ 
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies WHERE tablename = 'searches' AND policyname = 'searches_user_isolation'
    ) THEN
        CREATE POLICY searches_user_isolation ON searches
            FOR ALL
            USING (user_id = current_setting('app.current_user_id', true));
    END IF;
END $$;

-- Usage Events RLS
ALTER TABLE usage_events ENABLE ROW LEVEL SECURITY;

DO $$ 
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies WHERE tablename = 'usage_events' AND policyname = 'usage_events_user_isolation'
    ) THEN
        CREATE POLICY usage_events_user_isolation ON usage_events
            FOR ALL
            USING (user_id = current_setting('app.current_user_id', true));
    END IF;
END $$;

-- API Keys RLS
ALTER TABLE api_keys ENABLE ROW LEVEL SECURITY;

DO $$ 
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies WHERE tablename = 'api_keys' AND policyname = 'api_keys_user_isolation'
    ) THEN
        CREATE POLICY api_keys_user_isolation ON api_keys
            FOR ALL
            USING (user_id = current_setting('app.current_user_id', true));
    END IF;
END $$;
