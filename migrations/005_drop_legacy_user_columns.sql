-- Migration 005: Drop legacy username, password_hash, status columns from prototype
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='users' AND column_name='username') THEN
        ALTER TABLE users ALTER COLUMN username DROP NOT NULL;
        ALTER TABLE users DROP COLUMN IF EXISTS username CASCADE;
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='users' AND column_name='password_hash') THEN
        ALTER TABLE users ALTER COLUMN password_hash DROP NOT NULL;
        ALTER TABLE users DROP COLUMN IF EXISTS password_hash CASCADE;
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='users' AND column_name='status') THEN
        ALTER TABLE users DROP COLUMN IF EXISTS status CASCADE;
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='users' AND column_name='last_login') THEN
        ALTER TABLE users DROP COLUMN IF EXISTS last_login CASCADE;
    END IF;
END $$;
