"""Centralized Data Access Layer (DAL) for MapLead Multi-User Architecture.

Enforces two-layer data isolation:
  Layer 1: Every query is scoped by `user_id` at the application query level.
  Layer 2: PostgreSQL session variable `SET LOCAL app.current_user_id = %s` sets the active tenant for RLS.
No raw SQL queries exist outside this module.
"""
import os
import json
import logging
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime
from web.crm_db import get_db, _execute, IS_POSTGRES
from web.credits_db import get_current_billing_month, get_pacific_now

logger = logging.getLogger("maplead.dal")

def get_tenant_connection(user_id: Optional[str] = None):
    """
    Get database connection and configure Layer 2 isolation for PostgreSQL.
    """
    conn = get_db()
    if IS_POSTGRES and user_id:
        with conn.cursor() as cur:
            cur.execute("SET LOCAL app.current_user_id = %s", (user_id,))
    return conn


# ==============================================================================
# Users & Roles Management
# ==============================================================================

def get_user_by_id(user_id: str) -> Optional[Dict[str, Any]]:
    """Retrieve user profile with joined plan specifications."""
    conn = get_tenant_connection(user_id)
    try:
        sql = """
        SELECT u.id, u.email, u.display_name, u.role, u.plan_id, u.is_active, 
               u.custom_monthly_limit, u.created_at, u.last_active_at,
               p.name as plan_name, p.monthly_searches, p.can_use_deep_search, p.can_use_own_key
        FROM users u
        LEFT JOIN plans p ON u.plan_id = p.id
        WHERE u.id = ?
        """
        cursor = _execute(conn, sql, (user_id,))
        row = cursor.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_user_by_email(email: str) -> Optional[Dict[str, Any]]:
    """Retrieve user by normalized email address."""
    clean_email = email.strip().lower()
    conn = get_db()
    try:
        sql = """
        SELECT u.id, u.email, u.display_name, u.role, u.plan_id, u.is_active,
               u.custom_monthly_limit, u.created_at, u.last_active_at,
               p.name as plan_name, p.monthly_searches, p.can_use_deep_search, p.can_use_own_key
        FROM users u
        LEFT JOIN plans p ON u.plan_id = p.id
        WHERE LOWER(u.email) = ?
        """
        cursor = _execute(conn, sql, (clean_email,))
        row = cursor.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def sync_authenticated_user(provider_user_id: str, email: str, display_name: Optional[str] = None) -> Dict[str, Any]:
    """
    Called upon successful token validation.
    Assigns 'owner' role if email matches OWNER_EMAIL, otherwise 'member'.
    Claims existing single-user data for the owner account upon first login.
    """
    owner_email = os.environ.get("OWNER_EMAIL", "").strip().lower()
    clean_email = email.strip().lower()
    is_owner = bool(owner_email and clean_email == owner_email)
    
    assigned_role = "owner" if is_owner else "member"
    assigned_plan = "owner" if is_owner else "free"
    
    conn = get_db()
    try:
        cursor = _execute(conn, "SELECT id, role, plan_id, is_active FROM users WHERE id = ?", (provider_user_id,))
        existing = cursor.fetchone()
        now_iso = get_pacific_now().isoformat()
        
        if existing:
            # Update last active timestamp
            _execute(conn, "UPDATE users SET last_active_at = ?, display_name = COALESCE(?, display_name) WHERE id = ?", 
                     (now_iso, display_name or "", provider_user_id))
            # Ensure owner role is current if email matches OWNER_EMAIL
            if is_owner and existing["role" if IS_POSTGRES else 1] != "owner":
                _execute(conn, "UPDATE users SET role = 'owner', plan_id = 'owner' WHERE id = ?", (provider_user_id,))
            conn.commit()
        else:
            # First sign-in for this user
            name = display_name or clean_email.split("@")[0].capitalize()
            _execute(conn, """
            INSERT INTO users (id, email, display_name, role, plan_id, is_active, created_at, last_active_at)
            VALUES (?, ?, ?, ?, ?, 1, ?, ?)
            """, (provider_user_id, clean_email, name, assigned_role, assigned_plan, now_iso, now_iso))
            conn.commit()
            
            # If this is the owner, claim legacy data
            if is_owner:
                claim_legacy_data_for_owner(provider_user_id)
                
        user = get_user_by_id(provider_user_id)
        return user or {}
    finally:
        conn.close()


def claim_legacy_data_for_owner(owner_user_id: str):
    """Assigns pre-existing un-scoped crm_leads and search logs to the owner account."""
    conn = get_db()
    try:
        # Check if legacy crm_leads table exists and has rows
        try:
            cur = _execute(conn, "SELECT * FROM crm_leads")
            legacy_leads = cur.fetchall()
            for r in legacy_leads:
                d = dict(r)
                lead_id = d.get("id")
                # Insert into new multi-user leads table for the owner
                _execute(conn, """
                INSERT INTO leads (id, user_id, name, phone, website, has_website, rating, reviews,
                                  address, category, google_maps_url, opportunity_tier, stage, notes,
                                  deal_value, follow_up_date, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (id, user_id) DO NOTHING
                """, (
                    lead_id, owner_user_id, d.get("name", ""), d.get("phone", ""), d.get("website", ""),
                    1 if d.get("has_website") else 0, float(d.get("rating") or 0.0), int(d.get("reviews") or 0),
                    d.get("address", ""), d.get("category", ""), d.get("google_maps_url", ""),
                    d.get("opportunity_tier", "STANDARD"), d.get("stage", "New Lead"), d.get("notes", ""),
                    float(d.get("deal_value") or 1500.0), d.get("follow_up_date", ""),
                    str(d.get("created_at") or get_pacific_now().isoformat()),
                    str(d.get("updated_at") or get_pacific_now().isoformat())
                ))
            conn.commit()
            logger.info(f"Successfully claimed {len(legacy_leads)} legacy leads for owner {owner_user_id}")
        except Exception as e:
            logger.debug(f"Legacy crm_leads migration note: {e}")
    finally:
        conn.close()


def list_users_for_admin(requesting_user_id: str) -> List[Dict[str, Any]]:
    """Owner-only: list all platform users with plan, credits used this month, and status."""
    requester = get_user_by_id(requesting_user_id)
    if not requester or requester["role"] != "owner":
        raise PermissionError("Admin operation requires Owner role")
        
    conn = get_db()
    try:
        current_month = get_current_billing_month()
        sql = """
        SELECT u.id, u.email, u.display_name, u.role, u.plan_id, u.is_active,
               u.created_at, u.last_active_at, p.name as plan_name, p.monthly_searches,
               COALESCE((
                   SELECT SUM(credits_spent) FROM usage_events 
                   WHERE user_id = u.id AND month = ?
               ), 0) as credits_used_this_month
        FROM users u
        LEFT JOIN plans p ON u.plan_id = p.id
        ORDER BY u.created_at DESC
        """
        cursor = _execute(conn, sql, (current_month,))
        rows = cursor.fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def set_user_active_status(requesting_user_id: str, target_user_id: str, is_active: bool) -> bool:
    """Owner-only: activate or deactivate a user."""
    requester = get_user_by_id(requesting_user_id)
    if not requester or requester["role"] != "owner":
        raise PermissionError("Admin operation requires Owner role")
    if requesting_user_id == target_user_id and not is_active:
        raise ValueError("Owner cannot deactivate their own account")
        
    conn = get_db()
    try:
        _execute(conn, "UPDATE users SET is_active = ? WHERE id = ?", (1 if is_active else 0, target_user_id))
        conn.commit()
        return True
    finally:
        conn.close()


def update_user_plan(requesting_user_id: str, target_user_id: str, plan_id: str) -> bool:
    """Owner-only: change a user's subscription plan."""
    requester = get_user_by_id(requesting_user_id)
    if not requester or requester["role"] != "owner":
        raise PermissionError("Admin operation requires Owner role")
        
    conn = get_db()
    try:
        _execute(conn, "UPDATE users SET plan_id = ? WHERE id = ?", (plan_id, target_user_id))
        conn.commit()
        return True
    finally:
        conn.close()


# ==============================================================================
# Leads & CRM Layer (Strictly User Scoped)
# ==============================================================================

def get_user_leads(user_id: str, stage: Optional[str] = None) -> List[Dict[str, Any]]:
    """Get CRM leads strictly scoped to user_id."""
    conn = get_tenant_connection(user_id)
    try:
        if stage and stage.upper() != "ALL":
            sql = "SELECT * FROM leads WHERE user_id = ? AND stage = ? ORDER BY updated_at DESC"
            cursor = _execute(conn, sql, (user_id, stage))
        else:
            sql = "SELECT * FROM leads WHERE user_id = ? ORDER BY updated_at DESC"
            cursor = _execute(conn, sql, (user_id,))
        rows = cursor.fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def save_user_lead(user_id: str, lead_data: Dict[str, Any]) -> Dict[str, Any]:
    """Insert or update a lead scoped to user_id."""
    conn = get_tenant_connection(user_id)
    try:
        lead_id = lead_data.get("id") or f"{lead_data.get('name', 'lead')}_{lead_data.get('phone', '')}"
        now_iso = get_pacific_now().isoformat()
        socials_str = json.dumps(lead_data.get("social_links") or {})
        
        sql = """
        INSERT INTO leads (id, user_id, name, phone, website, has_website, rating, reviews,
                          address, category, google_maps_url, opportunity_tier, stage, notes,
                          deal_value, follow_up_date, social_links, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (id, user_id) DO UPDATE SET
            name = EXCLUDED.name,
            phone = EXCLUDED.phone,
            website = EXCLUDED.website,
            has_website = EXCLUDED.has_website,
            rating = EXCLUDED.rating,
            reviews = EXCLUDED.reviews,
            address = EXCLUDED.address,
            category = EXCLUDED.category,
            opportunity_tier = EXCLUDED.opportunity_tier,
            stage = COALESCE(leads.stage, EXCLUDED.stage),
            notes = CASE WHEN EXCLUDED.notes != '' THEN EXCLUDED.notes ELSE leads.notes END,
            social_links = EXCLUDED.social_links,
            updated_at = EXCLUDED.updated_at
        """
        _execute(conn, sql, (
            lead_id, user_id, lead_data.get("name", ""), lead_data.get("phone", ""),
            lead_data.get("website", ""), 1 if lead_data.get("has_website") else 0,
            float(lead_data.get("rating") or 0.0), int(lead_data.get("reviews") or 0),
            lead_data.get("address", ""), lead_data.get("category", ""),
            lead_data.get("google_maps_url", ""), lead_data.get("opportunity_tier", "STANDARD"),
            lead_data.get("stage", "New Lead"), lead_data.get("notes", ""),
            float(lead_data.get("deal_value") or 1500.0), lead_data.get("follow_up_date", ""),
            socials_str, now_iso, now_iso
        ))
        conn.commit()
        return lead_data
    finally:
        conn.close()


def update_lead_stage(user_id: str, lead_id: str, new_stage: str) -> bool:
    """Update lead stage for the specific user."""
    conn = get_tenant_connection(user_id)
    try:
        now_iso = get_pacific_now().isoformat()
        cursor = _execute(conn, "UPDATE leads SET stage = ?, updated_at = ? WHERE id = ? AND user_id = ?",
                         (new_stage, now_iso, lead_id, user_id))
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def update_lead_notes(user_id: str, lead_id: str, notes: str, follow_up: str = "") -> bool:
    """Update lead notes and follow up date for specific user."""
    conn = get_tenant_connection(user_id)
    try:
        now_iso = get_pacific_now().isoformat()
        cursor = _execute(conn, "UPDATE leads SET notes = ?, follow_up_date = ?, updated_at = ? WHERE id = ? AND user_id = ?",
                         (notes, follow_up, now_iso, lead_id, user_id))
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def delete_user_lead(user_id: str, lead_id: str) -> bool:
    """Delete a lead belonging strictly to user_id."""
    conn = get_tenant_connection(user_id)
    try:
        cursor = _execute(conn, "DELETE FROM leads WHERE id = ? AND user_id = ?", (lead_id, user_id))
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


# ==============================================================================
# Usage, Credits & Safety Buffers
# ==============================================================================

def get_platform_setting(key: str, default_val: str = "") -> str:
    """Retrieve platform configuration setting."""
    conn = get_db()
    try:
        cursor = _execute(conn, "SELECT value FROM platform_settings WHERE key = ?", (key,))
        row = cursor.fetchone()
        return row["value" if IS_POSTGRES else 0] if row else default_val
    finally:
        conn.close()


def set_platform_setting(key: str, val: str):
    """Set platform configuration setting."""
    conn = get_db()
    try:
        _execute(conn, """
        INSERT INTO platform_settings (key, value, updated_at)
        VALUES (?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = CURRENT_TIMESTAMP
        """, (key, str(val)))
        conn.commit()
    finally:
        conn.close()


def get_global_shared_key_usage(month: Optional[str] = None) -> Tuple[int, int, int]:
    """
    Returns (shared_used, monthly_limit, safety_buffer) for the platform's shared Google key.
    """
    cur_month = month or get_current_billing_month()
    conn = get_db()
    try:
        cursor = _execute(conn, """
        SELECT COALESCE(SUM(credits_spent), 0) as total_used 
        FROM usage_events 
        WHERE month = ? AND key_type = 'shared'
        """, (cur_month,))
        row = cursor.fetchone()
        used = int(row["total_used" if IS_POSTGRES else 0] or 0)
        
        limit = int(get_platform_setting("global_monthly_limit", "1000"))
        buffer = int(get_platform_setting("global_safety_buffer", "50"))
        return used, limit, buffer
    finally:
        conn.close()


def get_user_credits_status(user_id: str) -> Dict[str, Any]:
    """
    Computes user's monthly credits, limit, used count, and global platform status.
    """
    user = get_user_by_id(user_id)
    if not user:
        return {"error": "User not found"}
        
    cur_month = get_current_billing_month()
    conn = get_tenant_connection(user_id)
    try:
        # User's credit usage this month
        cursor = _execute(conn, """
        SELECT COALESCE(SUM(credits_spent), 0) as user_used
        FROM usage_events
        WHERE user_id = ? AND month = ?
        """, (user_id, cur_month))
        row = cursor.fetchone()
        user_used = int(row["user_used" if IS_POSTGRES else 0] or 0)
    finally:
        conn.close()
        
    # Check if user has an active BYOK key
    has_own_key, last_four = get_user_api_key_status(user_id)
    
    # Determine user limit based on plan or custom override
    is_owner = user["role"] == "owner"
    if is_owner:
        plan_limit = 999999
    elif user.get("custom_monthly_limit") is not None:
        plan_limit = user["custom_monthly_limit"]
    else:
        plan_limit = user.get("monthly_searches") or 10
        
    user_remaining = max(0, plan_limit - user_used)
    
    # Check global shared key status
    shared_used, global_limit, global_buffer = get_global_shared_key_usage(cur_month)
    global_safe_remaining = max(0, global_limit - global_buffer - shared_used)
    global_cap_reached = (shared_used >= (global_limit - global_buffer))
    
    from web.credits_db import get_days_remaining_in_month
    days_left = get_days_remaining_in_month()
    daily_pace = round(user_remaining / days_left, 1) if days_left > 0 else 0
    pct = round((user_remaining / plan_limit * 100), 1) if plan_limit > 0 and plan_limit < 999999 else 100.0
    
    return {
        "user_id": user_id,
        "role": user["role"],
        "plan_name": user.get("plan_name", "Free"),
        "user_used": user_used,
        "user_limit": plan_limit,
        "user_remaining": user_remaining,
        "used": user_used,
        "monthly_limit": plan_limit if plan_limit < 999999 else 1000,
        "credits_left": user_remaining if plan_limit < 999999 else 1000,
        "percentage_left": pct,
        "days_remaining": days_left,
        "daily_pace": daily_pace,
        "daily_rate": daily_pace,
        "has_own_key": has_own_key,
        "own_key_suffix": last_four,
        "global_shared_used": shared_used,
        "global_limit": global_limit,
        "global_buffer": global_buffer,
        "global_safe_remaining": global_safe_remaining,
        "global_cap_reached": global_cap_reached,
    }


def get_monthly_distribution(requesting_user_id: str) -> Dict[str, Any]:
    """Owner-only: provides total monthly distribution of credits, users, and usage."""
    users = list_users_for_admin(requesting_user_id)
    cur_month = get_current_billing_month()
    shared_used, global_limit, global_buffer = get_global_shared_key_usage(cur_month)
    
    from web.credits_db import get_days_remaining_in_month
    days_left = get_days_remaining_in_month()
    
    total_allocated = sum(u.get("monthly_searches") or 10 for u in users if u.get("role") != "owner")
    total_used = sum(u.get("credits_used_this_month") or 0 for u in users)
    
    return {
        "billing_month": cur_month,
        "days_remaining": days_left,
        "total_users": len(users),
        "total_credits_allocated": total_allocated,
        "total_credits_used": total_used,
        "global_shared_limit": global_limit,
        "global_shared_used": shared_used,
        "users": users
    }


def record_usage_event(user_id: str, credits: int, key_type: str = "shared", desc: str = "Places Search"):
    """Log credit usage event for user."""
    conn = get_tenant_connection(user_id)
    try:
        cur_month = get_current_billing_month()
        now_iso = get_pacific_now().isoformat()
        _execute(conn, """
        INSERT INTO usage_events (user_id, month, event_type, credits_spent, key_type, description, created_at)
        VALUES (?, ?, 'search', ?, ?, ?, ?)
        """, (user_id, cur_month, credits, key_type, desc, now_iso))
        conn.commit()
    finally:
        conn.close()


# ==============================================================================
# Search Logs (Scoped to User)
# ==============================================================================

def log_user_search(user_id: str, query: str, pages: int, credits: int, 
                    key_type: str = "shared", cached: bool = False, total_results: int = 0):
    """Save record in searches table."""
    conn = get_tenant_connection(user_id)
    try:
        now_iso = get_pacific_now().isoformat()
        _execute(conn, """
        INSERT INTO searches (user_id, query, pages_requested, credits_used, key_type, cached, total_results, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (user_id, query, pages, credits, key_type, 1 if cached else 0, total_results, now_iso))
        conn.commit()
    finally:
        conn.close()


def get_user_searches(user_id: str, limit: int = 30) -> List[Dict[str, Any]]:
    """Retrieve last searches for current user."""
    conn = get_tenant_connection(user_id)
    try:
        cursor = _execute(conn, """
        SELECT id, query, pages_requested, credits_used, key_type, cached, total_results, created_at
        FROM searches
        WHERE user_id = ?
        ORDER BY created_at DESC
        LIMIT ?
        """, (user_id, limit))
        rows = cursor.fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ==============================================================================
# BYOK User API Keys (AES-256-GCM Encrypted)
# ==============================================================================

def save_user_api_key(user_id: str, plain_key: str):
    """Encrypt and store user's Google Places API Key."""
    from web.crypto import encrypt_api_key, get_masked_key_suffix
    clean = plain_key.strip()
    encrypted = encrypt_api_key(clean)
    last_four = get_masked_key_suffix(clean)
    now_iso = get_pacific_now().isoformat()
    
    conn = get_tenant_connection(user_id)
    try:
        _execute(conn, """
        INSERT INTO api_keys (user_id, encrypted_key, key_last_four, is_valid, created_at, updated_at)
        VALUES (?, ?, ?, 1, ?, ?)
        ON CONFLICT (user_id) DO UPDATE SET
            encrypted_key = EXCLUDED.encrypted_key,
            key_last_four = EXCLUDED.key_last_four,
            is_valid = 1,
            updated_at = EXCLUDED.updated_at
        """, (user_id, encrypted, last_four, now_iso, now_iso))
        conn.commit()
    finally:
        conn.close()


def get_user_decrypted_api_key(user_id: str) -> Optional[str]:
    """Retrieve and decrypt the user's private Google Places API Key."""
    from web.crypto import decrypt_api_key
    conn = get_tenant_connection(user_id)
    try:
        cursor = _execute(conn, "SELECT encrypted_key, is_valid FROM api_keys WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        if not row:
            return None
        is_valid = bool(row["is_valid" if IS_POSTGRES else 1])
        if not is_valid:
            return None
        enc = row["encrypted_key" if IS_POSTGRES else 0]
        return decrypt_api_key(enc)
    finally:
        conn.close()


def get_user_api_key_status(user_id: str) -> Tuple[bool, Optional[str]]:
    """Return (has_key, last_four_digits) without exposing secret."""
    conn = get_tenant_connection(user_id)
    try:
        cursor = _execute(conn, "SELECT key_last_four, is_valid FROM api_keys WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        if not row:
            return False, None
        return bool(row["is_valid" if IS_POSTGRES else 1]), row["key_last_four" if IS_POSTGRES else 0]
    finally:
        conn.close()


def delete_user_api_key(user_id: str):
    """Delete the user's stored Google key."""
    conn = get_tenant_connection(user_id)
    try:
        _execute(conn, "DELETE FROM api_keys WHERE user_id = ?", (user_id,))
        conn.commit()
    finally:
        conn.close()


# ==============================================================================
# Audit Logging, Data Export & Account Deletion
# ==============================================================================

def log_audit_event(user_id: Optional[str], action: str, ip: str, details: Optional[Dict] = None):
    """Append security and activity record to audit_log table."""
    conn = get_db()
    try:
        now_iso = get_pacific_now().isoformat()
        details_str = json.dumps(details or {})
        _execute(conn, """
        INSERT INTO audit_log (user_id, action, ip_address, details, created_at)
        VALUES (?, ?, ?, ?, ?)
        """, (user_id or "anonymous", action, ip, details_str, now_iso))
        conn.commit()
    finally:
        conn.close()


def get_recent_audit_logs(requesting_user_id: str, limit: int = 50) -> List[Dict[str, Any]]:
    """Owner-only: inspect security and activity audit records."""
    requester = get_user_by_id(requesting_user_id)
    if not requester or requester["role"] != "owner":
        raise PermissionError("Admin operation requires Owner role")
    conn = get_db()
    try:
        cursor = _execute(conn, """
        SELECT id, user_id, action, ip_address, details, created_at
        FROM audit_log
        ORDER BY created_at DESC
        LIMIT ?
        """, (limit,))
        rows = cursor.fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def export_user_data(user_id: str) -> Dict[str, Any]:
    """Generates complete GDPR/CCPA personal data export."""
    leads = get_user_leads(user_id)
    searches = get_user_searches(user_id, limit=200)
    
    conn = get_tenant_connection(user_id)
    try:
        cursor = _execute(conn, "SELECT * FROM usage_events WHERE user_id = ? ORDER BY created_at DESC", (user_id,))
        usage = [dict(r) for r in cursor.fetchall()]
    finally:
        conn.close()
        
    user = get_user_by_id(user_id)
    return {
        "export_date": get_pacific_now().isoformat(),
        "user_profile": user,
        "leads_count": len(leads),
        "leads": leads,
        "searches": searches,
        "usage_history": usage
    }


def delete_user_account(user_id: str):
    """
    Permanently deletes user, leads, searches, usage, and encrypted API keys.
    Cascades automatically via foreign keys.
    """
    conn = get_db()
    try:
        # Delete user record (cascading deletes leads, searches, usage_events, api_keys)
        _execute(conn, "DELETE FROM users WHERE id = ?", (user_id,))
        conn.commit()
    finally:
        conn.close()
