import sqlite3
import os
import json
from typing import List, Dict, Any, Optional
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "crm_leads.db")

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_crm_db():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS crm_leads (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        phone TEXT,
        website TEXT,
        has_website INTEGER DEFAULT 0,
        rating REAL DEFAULT 0,
        reviews INTEGER DEFAULT 0,
        address TEXT,
        category TEXT,
        google_maps_url TEXT,
        opportunity_tier TEXT DEFAULT 'STANDARD',
        stage TEXT DEFAULT 'New Lead',
        notes TEXT DEFAULT '',
        deal_value REAL DEFAULT 1500.0,
        follow_up_date TEXT DEFAULT '',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS lead_activities (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        lead_id TEXT NOT NULL,
        activity_type TEXT NOT NULL,
        description TEXT NOT NULL,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (lead_id) REFERENCES crm_leads (id) ON DELETE CASCADE
    )
    """)
    conn.commit()
    conn.close()

def get_all_crm_leads() -> List[Dict[str, Any]]:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM crm_leads ORDER BY updated_at DESC")
    rows = cursor.fetchall()
    leads = [dict(r) for r in rows]
    conn.close()
    return leads

def save_or_update_lead(lead: Dict[str, Any]) -> Dict[str, Any]:
    conn = get_db()
    cursor = conn.cursor()
    lead_id = lead.get("id") or lead.get("name", "lead") + "_" + str(lead.get("phone", ""))
    
    cursor.execute("SELECT * FROM crm_leads WHERE id = ?", (lead_id,))
    existing = cursor.fetchone()
    
    now = datetime.now().isoformat()
    if existing:
        cursor.execute("""
        UPDATE crm_leads SET
            name = COALESCE(?, name),
            phone = COALESCE(?, phone),
            website = COALESCE(?, website),
            has_website = COALESCE(?, has_website),
            rating = COALESCE(?, rating),
            reviews = COALESCE(?, reviews),
            address = COALESCE(?, address),
            category = COALESCE(?, category),
            google_maps_url = COALESCE(?, google_maps_url),
            opportunity_tier = COALESCE(?, opportunity_tier),
            stage = COALESCE(?, stage),
            notes = COALESCE(?, notes),
            deal_value = COALESCE(?, deal_value),
            follow_up_date = COALESCE(?, follow_up_date),
            updated_at = ?
        WHERE id = ?
        """, (
            lead.get("name"), lead.get("phone"), lead.get("website"),
            1 if lead.get("has_website") else 0, lead.get("rating"), lead.get("reviews"),
            lead.get("address"), lead.get("category"), lead.get("google_maps_url"),
            lead.get("opportunity_tier"), lead.get("stage"), lead.get("notes"),
            lead.get("deal_value"), lead.get("follow_up_date"), now, lead_id
        ))
    else:
        cursor.execute("""
        INSERT INTO crm_leads (
            id, name, phone, website, has_website, rating, reviews,
            address, category, google_maps_url, opportunity_tier,
            stage, notes, deal_value, follow_up_date, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            lead_id, lead.get("name", "Unnamed"), lead.get("phone", ""),
            lead.get("website", ""), 1 if lead.get("has_website") else 0,
            lead.get("rating", 0.0), lead.get("reviews", 0), lead.get("address", ""),
            lead.get("category", ""), lead.get("google_maps_url", ""),
            lead.get("opportunity_tier", "STANDARD"), lead.get("stage", "New Lead"),
            lead.get("notes", ""), lead.get("deal_value", 1500.0),
            lead.get("follow_up_date", ""), now, now
        ))
        # Log initial creation activity
        cursor.execute("""
        INSERT INTO lead_activities (lead_id, activity_type, description)
        VALUES (?, 'Created', 'Lead imported into CRM')
        """, (lead_id,))

    conn.commit()
    cursor.execute("SELECT * FROM crm_leads WHERE id = ?", (lead_id,))
    updated = dict(cursor.fetchone())
    conn.close()
    return updated

def import_bulk_leads(leads: List[Dict[str, Any]]) -> int:
    added = 0
    for l in leads:
        save_or_update_lead(l)
        added += 1
    return added

def update_lead_stage(lead_id: str, new_stage: str) -> bool:
    conn = get_db()
    cursor = conn.cursor()
    now = datetime.now().isoformat()
    cursor.execute("""
    UPDATE crm_leads SET stage = ?, updated_at = ? WHERE id = ?
    """, (new_stage, now, lead_id))
    cursor.execute("""
    INSERT INTO lead_activities (lead_id, activity_type, description)
    VALUES (?, 'Stage Changed', ?)
    """, (lead_id, f"Stage updated to {new_stage}"))
    conn.commit()
    success = cursor.rowcount > 0
    conn.close()
    return success

def update_lead_notes(lead_id: str, notes: str, follow_up_date: Optional[str] = None) -> bool:
    conn = get_db()
    cursor = conn.cursor()
    now = datetime.now().isoformat()
    if follow_up_date is not None:
        cursor.execute("""
        UPDATE crm_leads SET notes = ?, follow_up_date = ?, updated_at = ? WHERE id = ?
        """, (notes, follow_up_date, now, lead_id))
    else:
        cursor.execute("""
        UPDATE crm_leads SET notes = ?, updated_at = ? WHERE id = ?
        """, (notes, now, lead_id))
    conn.commit()
    success = cursor.rowcount > 0
    conn.close()
    return success

def delete_lead(lead_id: str) -> bool:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM crm_leads WHERE id = ?", (lead_id,))
    conn.commit()
    success = cursor.rowcount > 0
    conn.close()
    return success

def get_crm_stats() -> Dict[str, Any]:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) as total, SUM(deal_value) as pipeline_value FROM crm_leads")
    row = cursor.fetchone()
    total = row["total"] or 0
    pipeline_value = row["pipeline_value"] or 0.0

    cursor.execute("SELECT stage, COUNT(*) as count FROM crm_leads GROUP BY stage")
    stages = {r["stage"]: r["count"] for r in cursor.fetchall()}

    conn.close()
    return {
        "total_leads": total,
        "pipeline_value": pipeline_value,
        "stages": stages
    }

# Initialize table on import
init_crm_db()
