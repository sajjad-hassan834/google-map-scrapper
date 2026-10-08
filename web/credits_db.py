import os
import json
import hashlib
import calendar
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional

try:
    from zoneinfo import ZoneInfo
    PACIFIC_TZ = ZoneInfo("America/Los_Angeles")
except Exception:
    from datetime import timezone
    PACIFIC_TZ = timezone(timedelta(hours=-7))

from web.crm_db import get_db, _execute, IS_POSTGRES

def get_pacific_now() -> datetime:
    return datetime.now(PACIFIC_TZ)

def get_current_billing_month() -> str:
    return get_pacific_now().strftime("%Y-%m")

def get_days_remaining_in_month() -> int:
    now = get_pacific_now()
    _, last_day = calendar.monthrange(now.year, now.month)
    # Days left in this billing month including today
    return max(1, last_day - now.day + 1)

def init_credits_db():
    conn = get_db()
    if IS_POSTGRES:
        _execute(conn, """
        CREATE TABLE IF NOT EXISTS places_credits (
            month TEXT PRIMARY KEY,
            used INTEGER DEFAULT 0,
            monthly_limit INTEGER DEFAULT 1000,
            safety_buffer INTEGER DEFAULT 50,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)
        _execute(conn, """
        CREATE TABLE IF NOT EXISTS places_search_logs (
            id SERIAL PRIMARY KEY,
            search_date TEXT NOT NULL,
            query TEXT NOT NULL,
            pages_requested INTEGER DEFAULT 1,
            credits_used INTEGER DEFAULT 1,
            cached INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)
        _execute(conn, """
        CREATE TABLE IF NOT EXISTS places_search_cache (
            cache_key TEXT PRIMARY KEY,
            query TEXT NOT NULL,
            response_json TEXT NOT NULL,
            cached_at TEXT NOT NULL,
            expires_at TEXT NOT NULL
        );
        """)
    else:
        _execute(conn, """
        CREATE TABLE IF NOT EXISTS places_credits (
            month TEXT PRIMARY KEY,
            used INTEGER DEFAULT 0,
            monthly_limit INTEGER DEFAULT 1000,
            safety_buffer INTEGER DEFAULT 50,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        """)
        _execute(conn, """
        CREATE TABLE IF NOT EXISTS places_search_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            search_date TEXT NOT NULL,
            query TEXT NOT NULL,
            pages_requested INTEGER DEFAULT 1,
            credits_used INTEGER DEFAULT 1,
            cached INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        """)
        _execute(conn, """
        CREATE TABLE IF NOT EXISTS places_search_cache (
            cache_key TEXT PRIMARY KEY,
            query TEXT NOT NULL,
            response_json TEXT NOT NULL,
            cached_at TEXT NOT NULL,
            expires_at TEXT NOT NULL
        );
        """)
    conn.commit()
    conn.close()

def get_or_create_monthly_record(month: Optional[str] = None) -> Dict[str, Any]:
    if not month:
        month = get_current_billing_month()
    
    conn = get_db()
    cursor = _execute(conn, "SELECT * FROM places_credits WHERE month = ?", (month,))
    row = cursor.fetchone()

    if row:
        record = dict(row)
        conn.close()
        return record

    # New month: inherit previous limits if available
    cursor_prev = _execute(conn, "SELECT monthly_limit, safety_buffer FROM places_credits ORDER BY month DESC LIMIT 1")
    prev_row = cursor_prev.fetchone()
    monthly_limit = prev_row["monthly_limit"] if prev_row else 1000
    safety_buffer = prev_row["safety_buffer"] if prev_row else 50

    now_iso = get_pacific_now().isoformat()
    _execute(conn, """
    INSERT INTO places_credits (month, used, monthly_limit, safety_buffer, updated_at)
    VALUES (?, 0, ?, ?, ?)
    """, (month, monthly_limit, safety_buffer, now_iso))
    conn.commit()

    cursor = _execute(conn, "SELECT * FROM places_credits WHERE month = ?", (month,))
    record = dict(cursor.fetchone())
    conn.close()
    return record

def get_credit_status() -> Dict[str, Any]:
    month = get_current_billing_month()
    record = get_or_create_monthly_record(month)

    used = int(record.get("used", 0))
    limit = int(record.get("monthly_limit", 1000))
    buffer = int(record.get("safety_buffer", 50))

    credits_left = max(0, limit - used)
    safe_credits_left = max(0, credits_left - buffer)
    days_left = get_days_remaining_in_month()
    daily_rate = int(safe_credits_left / days_left) if days_left > 0 else 0

    pct_left = (credits_left / limit * 100) if limit > 0 else 0

    # Fetch last 30 search logs
    conn = get_db()
    cursor = _execute(conn, """
    SELECT id, search_date, query, pages_requested, credits_used, cached
    FROM places_search_logs
    ORDER BY id DESC
    LIMIT 30
    """)
    recent = [dict(r) for r in cursor.fetchall()]
    conn.close()

    return {
        "month": month,
        "used": used,
        "monthly_limit": limit,
        "safety_buffer": buffer,
        "credits_left": credits_left,
        "safe_credits_left": safe_credits_left,
        "days_remaining": days_left,
        "daily_rate": daily_rate,
        "percentage_left": round(pct_left, 1),
        "recent_searches": recent
    }

def update_credit_settings(monthly_limit: int, safety_buffer: int) -> Dict[str, Any]:
    month = get_current_billing_month()
    get_or_create_monthly_record(month)

    conn = get_db()
    now_iso = get_pacific_now().isoformat()
    _execute(conn, """
    UPDATE places_credits
    SET monthly_limit = ?, safety_buffer = ?, updated_at = ?
    WHERE month = ?
    """, (max(1, monthly_limit), max(0, safety_buffer), now_iso, month))
    conn.commit()
    conn.close()
    return get_credit_status()

def increment_credit_usage(count: int = 1) -> int:
    month = get_current_billing_month()
    get_or_create_monthly_record(month)

    conn = get_db()
    now_iso = get_pacific_now().isoformat()
    _execute(conn, """
    UPDATE places_credits
    SET used = used + ?, updated_at = ?
    WHERE month = ?
    """, (count, now_iso, month))
    conn.commit()

    cursor = _execute(conn, "SELECT used FROM places_credits WHERE month = ?", (month,))
    new_used = int(cursor.fetchone()["used"])
    conn.close()
    return new_used

def log_places_search(query: str, pages_requested: int, credits_used: int, cached: bool = False):
    conn = get_db()
    now_str = get_pacific_now().strftime("%Y-%m-%d %H:%M:%S PT")
    _execute(conn, """
    INSERT INTO places_search_logs (search_date, query, pages_requested, credits_used, cached)
    VALUES (?, ?, ?, ?, ?)
    """, (now_str, query, pages_requested, credits_used, 1 if cached else 0))
    conn.commit()
    conn.close()

def generate_cache_key(query: str, filters: Dict[str, Any]) -> str:
    norm_query = query.strip().lower()
    norm_data = {
        "q": norm_query,
        "nw": bool(filters.get("no_website_only", True)),
        "mr": int(filters.get("min_reviews", 1)),
        "mrt": float(filters.get("min_rating", 0.0)),
        "mp": bool(filters.get("must_have_phone", True)),
        "pg": int(filters.get("max_pages", 3))
    }
    dumped = json.dumps(norm_data, sort_keys=True)
    return hashlib.sha256(dumped.encode("utf-8")).hexdigest()

def get_cached_search_results(cache_key: str) -> Optional[Dict[str, Any]]:
    conn = get_db()
    now_iso = get_pacific_now().isoformat()
    cursor = _execute(conn, """
    SELECT response_json FROM places_search_cache
    WHERE cache_key = ? AND expires_at > ?
    """, (cache_key, now_iso))
    row = cursor.fetchone()
    conn.close()

    if row:
        try:
            return json.loads(row["response_json"])
        except Exception:
            return None
    return None

def store_cached_search_results(cache_key: str, query: str, data: Dict[str, Any]):
    conn = get_db()
    now = get_pacific_now()
    now_iso = now.isoformat()
    expires_iso = (now + timedelta(days=30)).isoformat()
    dumped = json.dumps(data)

    if IS_POSTGRES:
        _execute(conn, """
        INSERT INTO places_search_cache (cache_key, query, response_json, cached_at, expires_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT (cache_key) DO UPDATE SET
            response_json = EXCLUDED.response_json,
            cached_at = EXCLUDED.cached_at,
            expires_at = EXCLUDED.expires_at
        """, (cache_key, query, dumped, now_iso, expires_iso))
    else:
        _execute(conn, """
        INSERT OR REPLACE INTO places_search_cache (cache_key, query, response_json, cached_at, expires_at)
        VALUES (?, ?, ?, ?, ?)
        """, (cache_key, query, dumped, now_iso, expires_iso))

    conn.commit()
    conn.close()

# Initialize tables on import
init_credits_db()
