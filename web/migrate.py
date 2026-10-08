"""Database Migration Runner for MapLead.

Applies versioned SQL migrations in strict ascending order.
Records applied migrations in the schema_migrations table.
Full compatibility with PostgreSQL (production) and SQLite (local dev).
"""
import os
import glob
import logging
from datetime import datetime
from web.crm_db import get_db, _execute, IS_POSTGRES

logger = logging.getLogger("maplead.migrate")
logging.basicConfig(level=logging.INFO)

MIGRATIONS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "migrations")

def init_migration_table(conn):
    """Ensure the schema_migrations tracking table exists."""
    if IS_POSTGRES:
        _execute(conn, """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version VARCHAR(64) PRIMARY KEY,
            applied_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
        );
        """)
    else:
        _execute(conn, """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version VARCHAR(64) PRIMARY KEY,
            applied_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        """)
    conn.commit()


def get_applied_migrations(conn) -> set:
    """Return set of migration version names already applied."""
    cursor = _execute(conn, "SELECT version FROM schema_migrations")
    rows = cursor.fetchall()
    if IS_POSTGRES:
        return {r["version"] for r in rows}
    else:
        return {r[0] for r in rows}


def apply_migration_file(conn, file_path: str, version: str):
    """Read and apply an individual SQL migration file."""
    with open(file_path, "r", encoding="utf-8") as f:
        sql = f.read()

    logger.info(f"Applying migration: {version}")
    
    if IS_POSTGRES:
        with conn.cursor() as cursor:
            cursor.execute(sql)
            cursor.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (version,))
        conn.commit()
    else:
        # SQLite compatibility: strip PG-specific constructs if running SQLite
        statements = []
        # Remove DO $$ blocks and RLS statements for SQLite
        in_do_block = False
        cleaned_lines = []
        for line in sql.splitlines():
            if "DO $$" in line:
                in_do_block = True
                continue
            if in_do_block:
                if "$$;" in line:
                    in_do_block = False
                continue
            if "ROW LEVEL SECURITY" in line or "CREATE POLICY" in line:
                continue
            cleaned_lines.append(line)
            
        clean_sql = "\n".join(cleaned_lines)
        # Adapt data types for SQLite
        clean_sql = clean_sql.replace("SERIAL PRIMARY KEY", "INTEGER PRIMARY KEY AUTOINCREMENT")
        clean_sql = clean_sql.replace("TIMESTAMP WITH TIME ZONE", "TEXT")
        clean_sql = clean_sql.replace("TIMESTAMP", "TEXT")
        clean_sql = clean_sql.replace("BOOLEAN", "INTEGER")
        clean_sql = clean_sql.replace("TRUE", "1")
        clean_sql = clean_sql.replace("FALSE", "0")
        
        cursor = conn.cursor()
        if clean_sql.strip():
            cursor.executescript(clean_sql)
            
        # SQLite schema verification: ensure users table columns exist
        cursor.execute("PRAGMA table_info(users)")
        existing_cols = {row[1] for row in cursor.fetchall()}
        if "plan_id" not in existing_cols:
            try: cursor.execute("ALTER TABLE users ADD COLUMN plan_id TEXT DEFAULT 'free'")
            except Exception: pass
        if "is_active" not in existing_cols:
            try: cursor.execute("ALTER TABLE users ADD COLUMN is_active INTEGER DEFAULT 1")
            except Exception: pass
        if "last_active_at" not in existing_cols:
            try: cursor.execute("ALTER TABLE users ADD COLUMN last_active_at TEXT DEFAULT CURRENT_TIMESTAMP")
            except Exception: pass

        cursor.execute("INSERT INTO schema_migrations (version) VALUES (?)", (version,))
        conn.commit()

    logger.info(f"Successfully applied migration: {version}")


def preflight_align_schema(conn):
    """Ensure existing tables are aligned for migrations in PostgreSQL & SQLite."""
    if IS_POSTGRES:
        with conn.cursor() as cursor:
            # 1. Align legacy users table if it exists with integer id
            cursor.execute("""
                SELECT data_type 
                FROM information_schema.columns 
                WHERE table_name = 'users' AND column_name = 'id'
            """)
            row = cursor.fetchone()
            if row:
                dt = row["data_type"] if isinstance(row, dict) else row[0]
                if dt.lower() in ("integer", "smallint", "bigint", "serial"):
                    logger.info(f"Preflight: Migrating users.id from {dt} to VARCHAR(64)...")
                    cursor.execute("""
                        ALTER TABLE users ALTER COLUMN id DROP DEFAULT;
                        ALTER TABLE users ALTER COLUMN id TYPE VARCHAR(64) USING id::VARCHAR(64);
                    """)

            # 2. Ensure plans table exists with seeds before users references it
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS plans (
                    id VARCHAR(32) PRIMARY KEY,
                    name VARCHAR(64) NOT NULL,
                    monthly_searches INTEGER NOT NULL,
                    can_use_deep_search BOOLEAN DEFAULT FALSE,
                    can_use_own_key BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                );
                INSERT INTO plans (id, name, monthly_searches, can_use_deep_search, can_use_own_key)
                VALUES 
                    ('free', 'Free', 10, FALSE, TRUE),
                    ('pro', 'Pro', 100, FALSE, TRUE),
                    ('owner', 'Owner', 999999, TRUE, TRUE)
                ON CONFLICT (id) DO NOTHING;
            """)

            # 3. If users table already exists, ensure all required columns exist
            cursor.execute("""
                DO $$
                BEGIN
                    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'users') THEN
                        IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='users' AND column_name='plan_id') THEN
                            ALTER TABLE users ADD COLUMN plan_id VARCHAR(32) DEFAULT 'free' REFERENCES plans(id);
                        END IF;
                        IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='users' AND column_name='role') THEN
                            ALTER TABLE users ADD COLUMN role VARCHAR(32) DEFAULT 'member';
                        END IF;
                        IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='users' AND column_name='is_active') THEN
                            ALTER TABLE users ADD COLUMN is_active BOOLEAN DEFAULT TRUE;
                        END IF;
                        IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='users' AND column_name='custom_monthly_limit') THEN
                            ALTER TABLE users ADD COLUMN custom_monthly_limit INTEGER;
                        END IF;
                        IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='users' AND column_name='last_active_at') THEN
                            ALTER TABLE users ADD COLUMN last_active_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP;
                        END IF;
                        IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='users' AND column_name='display_name') THEN
                            ALTER TABLE users ADD COLUMN display_name VARCHAR(255);
                        END IF;
                    END IF;
                END $$;
            """)
        conn.commit()
    else:
        # SQLite preflight
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='users'")
        if cursor.fetchone():
            cursor.execute("PRAGMA table_info(users)")
            existing_cols = {r[1] for r in cursor.fetchall()}
            if "plan_id" not in existing_cols:
                try: cursor.execute("ALTER TABLE users ADD COLUMN plan_id TEXT DEFAULT 'free'")
                except Exception: pass
            if "is_active" not in existing_cols:
                try: cursor.execute("ALTER TABLE users ADD COLUMN is_active INTEGER DEFAULT 1")
                except Exception: pass
            if "last_active_at" not in existing_cols:
                try: cursor.execute("ALTER TABLE users ADD COLUMN last_active_at TEXT DEFAULT CURRENT_TIMESTAMP")
                except Exception: pass
            if "custom_monthly_limit" not in existing_cols:
                try: cursor.execute("ALTER TABLE users ADD COLUMN custom_monthly_limit INTEGER")
                except Exception: pass
            conn.commit()


def run_migrations():
    """Apply all pending migrations in sorted order."""
    conn = get_db()
    try:
        init_migration_table(conn)
        preflight_align_schema(conn)
        applied = get_applied_migrations(conn)
        
        migration_files = sorted(glob.glob(os.path.join(MIGRATIONS_DIR, "*.sql")))
        if not migration_files:
            logger.warning(f"No migration files found in {MIGRATIONS_DIR}")
            return []

        applied_now = []
        for file_path in migration_files:
            version = os.path.basename(file_path)
            if version not in applied:
                apply_migration_file(conn, file_path, version)
                applied_now.append(version)
            else:
                logger.debug(f"Skipping already applied migration: {version}")
                
        return applied_now
    finally:
        conn.close()


if __name__ == "__main__":
    print("Running MapLead database migrations...")
    applied = run_migrations()
    print(f"Migration completed. Applied {len(applied)} new migration(s): {applied}")
