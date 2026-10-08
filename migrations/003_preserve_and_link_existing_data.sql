-- Migration 003: Existing Data Preservation & Compatibility
-- Ensures legacy crm_leads and places_search_logs remain accessible until claimed by owner

-- Create holding table for unassigned legacy leads if needed
CREATE TABLE IF NOT EXISTS legacy_unassigned_data (
    id SERIAL PRIMARY KEY,
    entity_type VARCHAR(32) NOT NULL,
    entity_id TEXT,
    payload TEXT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Copy any existing crm_leads into legacy_unassigned_data if not already present
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'crm_leads') THEN
        INSERT INTO legacy_unassigned_data (entity_type, entity_id, payload)
        SELECT 'crm_lead', id, row_to_json(crm_leads)::text
        FROM crm_leads
        WHERE NOT EXISTS (
            SELECT 1 FROM legacy_unassigned_data WHERE entity_type = 'crm_lead' AND entity_id = crm_leads.id
        );
    END IF;
END $$;
