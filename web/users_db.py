"""User & Account Management Persistence Module for MapLead.

Supports multi-user role-based platform (Admin/Manager vs Member).
Uses PBKDF2-HMAC-SHA256 for secure password hashing with random salt.
Full compatibility with SQLite and PostgreSQL.
"""
import os
import time
import hmac
import hashlib
import secrets
from typing import Dict, List, Optional, Tuple
from web.crm_db import get_db, _execute, IS_POSTGRES

# ----------------- Password Hashing -----------------

def hash_password(password: str) -> str:
    """Hash password with PBKDF2-HMAC-SHA256 and a random 16-byte salt."""
    salt = secrets.token_bytes(16)
    key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 100000)
    return f"{salt.hex()}${key.hex()}"


def verify_password_hash(password: str, stored_hash: str) -> bool:
    """Verify password against salt$hash string using constant-time comparison."""
    if not stored_hash or "$" not in stored_hash:
        return False
    try:
        salt_hex, key_hex = stored_hash.split("$", 1)
        salt = bytes.fromhex(salt_hex)
        expected_key = bytes.fromhex(key_hex)
        key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 100000)
        return hmac.compare_digest(key, expected_key)
    except Exception:
        return False


# ----------------- Database Schema -----------------

def init_users_db():
    """Create users table and seed initial Super Admin account."""
    conn = get_db()
    if IS_POSTGRES:
        _execute(conn, """
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            email TEXT UNIQUE,
            display_name TEXT,
            password_hash TEXT NOT NULL,
            role TEXT DEFAULT 'member',
            status TEXT DEFAULT 'active',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_login TIMESTAMP
        );
        """)
    else:
        _execute(conn, """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            email TEXT UNIQUE,
            display_name TEXT,
            password_hash TEXT NOT NULL,
            role TEXT DEFAULT 'member',
            status TEXT DEFAULT 'active',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_login TIMESTAMP
        );
        """)
    conn.commit()

    # Seed default Admin account if table is empty
    cursor = _execute(conn, "SELECT COUNT(*) as count FROM users")
    row = cursor.fetchone()
    count = row["count"] if IS_POSTGRES else row[0]

    if count == 0:
        app_pwd = os.environ.get("APP_PASSWORD", "").strip() or "admin123"
        pwd_hash = hash_password(app_pwd)
        _execute(conn, """
        INSERT INTO users (username, email, display_name, password_hash, role, status)
        VALUES (?, ?, ?, ?, 'admin', 'active')
        """, ("admin", "admin@maplead.local", "Super Admin", pwd_hash))
        conn.commit()

    conn.close()


# Ensure table is initialized on import
try:
    init_users_db()
except Exception:
    pass


# ----------------- User Queries & Operations -----------------

def get_user_by_id(user_id: int) -> Optional[Dict]:
    conn = get_db()
    cursor = _execute(conn, "SELECT * FROM users WHERE id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return None
    d = dict(row)
    d.pop("password_hash", None)
    return d


def get_user_with_hash(username_or_email: str) -> Optional[Dict]:
    conn = get_db()
    identifier = username_or_email.strip().lower()
    cursor = _execute(
        conn,
        "SELECT * FROM users WHERE LOWER(username) = ? OR LOWER(email) = ?",
        (identifier, identifier)
    )
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def list_all_users() -> List[Dict]:
    conn = get_db()
    cursor = _execute(conn, "SELECT id, username, email, display_name, role, status, created_at, last_login FROM users ORDER BY id ASC")
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def create_user(username: str, password: str, email: str = "", display_name: str = "", role: str = "member") -> Dict:
    clean_username = username.strip().lower()
    clean_email = email.strip().lower() or None
    clean_display = display_name.strip() or username.strip()
    role_clean = "admin" if role.lower() in ("admin", "manager") else "member"

    pwd_hash = hash_password(password)

    conn = get_db()
    try:
        _execute(conn, """
        INSERT INTO users (username, email, display_name, password_hash, role, status)
        VALUES (?, ?, ?, ?, ?, 'active')
        """, (clean_username, clean_email, clean_display, pwd_hash, role_clean))
        conn.commit()
    finally:
        conn.close()

    user = get_user_with_hash(clean_username)
    if user:
        user.pop("password_hash", None)
    return user or {}


def update_user_profile(user_id: int, display_name: Optional[str] = None, email: Optional[str] = None, role: Optional[str] = None, status: Optional[str] = None) -> Optional[Dict]:
    conn = get_db()
    updates = []
    params = []

    if display_name is not None:
        updates.append("display_name = ?")
        params.append(display_name.strip())
    if email is not None:
        updates.append("email = ?")
        params.append(email.strip().lower() or None)
    if role is not None:
        role_clean = "admin" if role.lower() in ("admin", "manager") else "member"
        updates.append("role = ?")
        params.append(role_clean)
    if status is not None:
        status_clean = "disabled" if status.lower() == "disabled" else "active"
        updates.append("status = ?")
        params.append(status_clean)

    if not updates:
        conn.close()
        return get_user_by_id(user_id)

    params.append(user_id)
    sql = f"UPDATE users SET {', '.join(updates)} WHERE id = ?"
    _execute(conn, sql, tuple(params))
    conn.commit()
    conn.close()
    return get_user_by_id(user_id)


def update_user_password(user_id: int, new_password: str) -> bool:
    conn = get_db()
    pwd_hash = hash_password(new_password)
    _execute(conn, "UPDATE users SET password_hash = ? WHERE id = ?", (pwd_hash, user_id))
    conn.commit()
    conn.close()
    return True


def delete_user(user_id: int) -> bool:
    conn = get_db()
    _execute(conn, "DELETE FROM users WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()
    return True


def record_user_login(user_id: int):
    conn = get_db()
    now_str = time.strftime("%Y-%m-%d %H:%M:%S")
    _execute(conn, "UPDATE users SET last_login = ? WHERE id = ?", (now_str, user_id))
    conn.commit()
    conn.close()
