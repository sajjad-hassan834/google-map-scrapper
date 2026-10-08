import os
import csv
import io
import json
import time
import urllib.request
import urllib.parse
import urllib.error
import logging
from typing import List, Optional, Dict, Any
from datetime import datetime

# Load .env if present before importing or reading configuration
_env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
if os.path.exists(_env_path):
    try:
        with open(_env_path, "r", encoding="utf-8") as _f:
            for _line in _f:
                _line = _line.strip()
                if _line and not _line.startswith("#") and "=" in _line:
                    _k, _v = _line.split("=", 1)
                    os.environ.setdefault(_k.strip(), _v.strip())
    except Exception:
        pass

from fastapi import FastAPI, HTTPException, Query, Body, Request, Response, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from web.config import (
    BRAND_NAME, BRAND_TAGLINE, APP_VERSION, META_DESCRIPTION,
    SOCIAL_PREVIEW_TITLE, DEFAULT_MONTHLY_LIMIT, DEFAULT_SAFETY_BUFFER
)
from web.migrate import run_migrations
import web.dal as dal
from web.clerk_auth import get_current_user, require_owner, verify_clerk_token
from web.social_finder import extract_social_links
from web.location_helper import detect_query_region
from web.crypto import get_masked_key_suffix

logger = logging.getLogger("maplead.backend")
logging.basicConfig(level=logging.INFO)

# Run database migrations on startup
try:
    run_migrations()
except Exception as _e:
    logger.error(f"Startup migration check failed: {_e}", exc_info=True)

app = FastAPI(title=f"{BRAND_NAME} Multi-User Lead Engine", version=APP_VERSION)

# CORS configured for credentials support across local, Railway, and Vercel domains
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"https?://.*",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ----------------- Rate Limiting -----------------
_RATE_LIMITS: Dict[str, List[float]] = {}

def check_rate_limit(key: str, max_calls: int, window_seconds: int = 60):
    """Simple in-memory sliding window rate limiter."""
    now = time.time()
    history = _RATE_LIMITS.setdefault(key, [])
    # Evict calls older than window
    _RATE_LIMITS[key] = [t for t in history if now - t < window_seconds]
    if len(_RATE_LIMITS[key]) >= max_calls:
        raise HTTPException(status_code=429, detail="Too many requests. Please slow down and try again shortly.")
    _RATE_LIMITS[key].append(now)


@app.middleware("http")
async def rate_limiting_middleware(request: Request, call_next):
    """Enforce rate limits per user/IP."""
    path = request.url.path
    if path.startswith("/api/"):
        ip = request.client.host if request.client else "unknown"
        # Stricter on search and keys endpoints
        if "/search" in path:
            check_rate_limit(f"search:{ip}", max_calls=25, window_seconds=60)
        elif "/keys" in path or "/delete-account" in path:
            check_rate_limit(f"sensitive:{ip}", max_calls=15, window_seconds=60)
        else:
            check_rate_limit(f"general:{ip}", max_calls=120, window_seconds=60)

    return await call_next(request)


SCRAPER_BASE = os.environ.get("SCRAPER_BASE_URL", "http://localhost:8080")
UA = "google-maps-scraper-kit/2.5"

# ----------------- Pydantic Models -----------------

class PlacesSearchRequest(BaseModel):
    query: str
    target_lead_count: int = 20
    no_website_only: bool = False
    min_reviews: int = 0
    min_rating: float = 0.0
    must_have_phone: bool = False
    max_pages: int = 1

class UserKeyRequest(BaseModel):
    api_key: str

class DeleteAccountRequest(BaseModel):
    confirm_text: str

class UserStatusUpdateRequest(BaseModel):
    is_active: bool

class UserPlanUpdateRequest(BaseModel):
    plan_id: str

class PlatformSettingsUpdate(BaseModel):
    global_monthly_limit: Optional[int] = None
    global_safety_buffer: Optional[int] = None
    cache_retention_days: Optional[int] = None

class LeadStageUpdate(BaseModel):
    stage: str

class LeadNotesUpdate(BaseModel):
    notes: str
    follow_up_date: Optional[str] = ""

class CRMLeadItem(BaseModel):
    id: Optional[str] = None
    name: str
    phone: Optional[str] = ""
    website: Optional[str] = ""
    has_website: Optional[bool] = False
    rating: Optional[float] = 0.0
    reviews: Optional[int] = 0
    address: Optional[str] = ""
    category: Optional[str] = ""
    google_maps_url: Optional[str] = ""
    opportunity_tier: Optional[str] = "STANDARD"
    stage: Optional[str] = "New Lead"
    notes: Optional[str] = ""
    deal_value: Optional[float] = 1500.0
    follow_up_date: Optional[str] = ""
    social_links: Optional[Dict[str, str]] = None

class ScraperJobRequest(BaseModel):
    keywords: List[str]
    lat: float
    lon: float
    depth: int = 10
    email: bool = False
    max_time: int = 300

class ScraperFilterRequest(BaseModel):
    no_website_only: bool = False
    min_reviews: int = 0
    min_rating: float = 0.0
    must_have_phone: bool = False


def compute_opportunity(rating: float, reviews: int, has_website: bool) -> Dict[str, str]:
    if has_website:
        return {
            "tier": "STANDARD",
            "badge_color": "secondary",
            "reason": "Has website already"
        }
    if reviews >= 20 and rating >= 4.5:
        return {
            "tier": "🔥 ULTRA HOT",
            "badge_color": "danger",
            "reason": f"Thriving business with {reviews} reviews & {rating:.1f}★ rating, but ZERO website! Prime target."
        }
    elif reviews >= 5 and rating >= 4.0:
        return {
            "tier": "⚡ HIGH VALUE",
            "badge_color": "warning",
            "reason": f"Solid reputation ({reviews} reviews, {rating:.1f}★) without a web presence."
        }
    elif reviews >= 1:
        return {
            "tier": "✨ QUALIFIED",
            "badge_color": "info",
            "reason": f"Active listing ({reviews} reviews) with no website."
        }
    else:
        return {
            "tier": "UNRATED",
            "badge_color": "muted",
            "reason": "No reviews on record yet."
        }


# ----------------- Google Places API (New) -----------------

def search_google_places_new(api_key: str, query: str, max_pages: int = 1, region_code: Optional[str] = None):
    all_places = []
    next_page_token = None
    url = "https://places.googleapis.com/v1/places:searchText"
    requests_sent = 0
    
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": "places.id,places.displayName,places.formattedAddress,places.nationalPhoneNumber,places.internationalPhoneNumber,places.websiteUri,places.rating,places.userRatingCount,places.googleMapsUri,places.primaryTypeDisplayName,nextPageToken",
        "User-Agent": UA
    }

    for page in range(max_pages):
        body: Dict[str, Any] = {
            "textQuery": query,
            "pageSize": 20
        }
        if region_code:
            body["regionCode"] = region_code
        if next_page_token:
            body["pageToken"] = next_page_token

        req_data = json.dumps(body).encode("utf-8")
        request = urllib.request.Request(url, data=req_data, headers=headers, method="POST")
        requests_sent += 1

        try:
            with urllib.request.urlopen(request, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            err_msg = e.read().decode("utf-8", "replace")
            raise HTTPException(status_code=e.code, detail=f"Google Places API Error: {err_msg}")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Network error querying Google Places: {str(e)}")

        places = data.get("places", [])
        all_places.extend(places)
        
        next_page_token = data.get("nextPageToken")
        if not next_page_token or len(places) == 0:
            break
        time.sleep(1.2)

    return all_places, requests_sent


def format_places_new_item(item: Dict[str, Any], socials_map: Optional[Dict[str, Dict[str, str]]] = None) -> Dict[str, Any]:
    name = (item.get("displayName") or {}).get("text", "")
    address = item.get("formattedAddress", "")
    phone = item.get("nationalPhoneNumber") or item.get("internationalPhoneNumber") or ""
    website = item.get("websiteUri") or ""
    rating = float(item.get("rating") or 0.0)
    reviews = int(item.get("userRatingCount") or 0)
    maps_url = item.get("googleMapsUri") or ""
    category = (item.get("primaryTypeDisplayName") or {}).get("text", "")
    
    # Check social media links if website exists (from batch map or single fallback)
    socials = {}
    if website:
        if socials_map and website in socials_map:
            socials = socials_map[website]
        else:
            socials = extract_social_links(website)
    opp = compute_opportunity(rating, reviews, bool(website))

    return {
        "id": item.get("id", "") or (name + "_" + phone),
        "name": name,
        "phone": phone,
        "website": website,
        "has_website": bool(website),
        "rating": rating,
        "reviews": reviews,
        "address": address,
        "category": category,
        "google_maps_url": maps_url,
        "opportunity_tier": opp["tier"],
        "opportunity_badge": opp["badge_color"],
        "opportunity_reason": opp["reason"],
        "social_links": socials,
        "source": "Google Places API"
    }


# ==============================================================================
# Public Configuration & Auth Endpoints
# ==============================================================================

@app.get("/api/config")
def api_config():
    """Public application metadata."""
    return {
        "brand_name": BRAND_NAME,
        "brand_tagline": BRAND_TAGLINE,
        "app_version": APP_VERSION,
        "clerk_publishable_key": os.environ.get("CLERK_PUBLISHABLE_KEY", ""),
        "meta_description": META_DESCRIPTION,
        "social_preview_title": SOCIAL_PREVIEW_TITLE,
    }


@app.get("/api/auth/status")
def api_auth_status(request: Request):
    """Validates session and returns user profile & role."""
    try:
        user = get_current_user(request)
        credits_status = dal.get_user_credits_status(user["id"])
        return {
            "authenticated": True,
            "user": user,
            "credits": credits_status,
            "brand_name": BRAND_NAME
        }
    except HTTPException:
        return {
            "authenticated": False,
            "user": None,
            "brand_name": BRAND_NAME
        }


class LoginRequest(BaseModel):
    password: str


@app.post("/api/auth/login")
def api_auth_login(payload: LoginRequest, response: Response):
    """Master/Dev password login when Clerk is not loaded or for local access."""
    app_pwd = os.environ.get("APP_PASSWORD", "").strip()
    if app_pwd and payload.password != app_pwd:
        raise HTTPException(status_code=401, detail="Incorrect password. Please try again.")

    owner_email = os.environ.get("OWNER_EMAIL", "sajjad@maplead.local").strip().lower()
    token = f"dev_user_owner:{owner_email}:Sajjad Hassan"
    user = dal.sync_authenticated_user("dev_user_owner", owner_email, "Sajjad Hassan")

    response.set_cookie(
        key="__session",
        value=token,
        max_age=7 * 86400,
        httponly=True,
        samesite="lax",
        secure=False
    )
    return {
        "success": True,
        "token": token,
        "user": user,
        "message": "Authenticated successfully"
    }


# ==============================================================================
# User Profile, Credits & BYOK Keys
# ==============================================================================

@app.get("/api/user/me")
def api_user_me(user: Dict = Depends(get_current_user)):
    """Get current user details with live credit status."""
    credits_status = dal.get_user_credits_status(user["id"])
    return {
        "user": user,
        "credits": credits_status
    }


@app.get("/api/credits/status")
def api_credits_status(user: Dict = Depends(get_current_user)):
    """User-scoped credits status, pacing, and global safety status."""
    return dal.get_user_credits_status(user["id"])


@app.get("/api/user/keys")
def api_user_keys(user: Dict = Depends(get_current_user)):
    """Check if user has configured private BYOK Google key."""
    has_key, last_four = dal.get_user_api_key_status(user["id"])
    return {
        "has_own_key": has_key,
        "last_four": last_four
    }


@app.post("/api/user/keys")
def api_user_save_key(req: UserKeyRequest, request: Request, user: Dict = Depends(get_current_user)):
    """Encrypt and store user's Google Places API key (AES-256-GCM)."""
    clean_key = req.api_key.strip()
    if not clean_key or len(clean_key) < 10:
        raise HTTPException(status_code=400, detail="Invalid Google Places API key format.")
    dal.save_user_api_key(user["id"], clean_key)
    ip = request.client.host if request.client else ""
    dal.log_audit_event(user["id"], "key_update", ip, {"key_last_four": clean_key[-4:]})
    return {"success": True, "message": "Private API key encrypted and saved securely."}


@app.delete("/api/user/keys")
def api_user_delete_key(request: Request, user: Dict = Depends(get_current_user)):
    """Remove user's stored Google key."""
    dal.delete_user_api_key(user["id"])
    ip = request.client.host if request.client else ""
    dal.log_audit_event(user["id"], "key_delete", ip)
    return {"success": True, "message": "Private API key removed."}


# ==============================================================================
# Search Endpoint (Google Places New API)
# ==============================================================================

@app.post("/api/places/search")
def api_places_search(req: PlacesSearchRequest, request: Request, user: Dict = Depends(get_current_user)):
    query = req.query.strip()
    if not query:
        raise HTTPException(status_code=400, detail="Search query is required.")

    # 1. Determine which API Key to use (User's private key vs Platform shared key)
    own_key = dal.get_user_decrypted_api_key(user["id"])
    using_own_key = bool(own_key)
    api_key = own_key or os.environ.get("PLACES_API_KEY", "").strip() or os.environ.get("GOOGLE_PLACES_API_KEY", "").strip()

    if not api_key:
        raise HTTPException(status_code=400, detail="No Google Places API key configured. Provide your own key in Settings.")

    pages_to_fetch = max(1, min(3, req.max_pages))
    
    # 2. Check Credits Quota
    credits_status = dal.get_user_credits_status(user["id"])
    
    if not using_own_key:
        # Check global shared cap first
        if credits_status["global_cap_reached"]:
            raise HTTPException(
                status_code=429,
                detail="Platform shared free quota reached monthly buffer. Add your own Google Places API key in Settings to continue."
            )
        # Check user's own remaining credits
        if credits_status["user_remaining"] < pages_to_fetch:
            raise HTTPException(
                status_code=429,
                detail=f"You have {credits_status['user_remaining']} credits remaining this month. Upgrade plan or use your own API key."
            )

    # 3. Detect asked location / country code for biasing
    region_code, location_asked = detect_query_region(query)

    # 4. Execute Search
    raw_places, requests_used = search_google_places_new(
        api_key=api_key,
        query=query,
        max_pages=pages_to_fetch,
        region_code=region_code
    )

    # Record usage and audit event
    key_type = "own" if using_own_key else "shared"
    dal.record_usage_event(user["id"], credits=requests_used, key_type=key_type, desc=f"Search: {query[:50]}")
    dal.log_user_search(user["id"], query=query, pages=pages_to_fetch, credits=requests_used,
                        key_type=key_type, total_results=len(raw_places))
    
    ip = request.client.host if request.client else ""
    dal.log_audit_event(user["id"], "search", ip, {
        "query": query, "pages": pages_to_fetch, "key_type": key_type, "results": len(raw_places)
    })

    # Extract website URLs for fast batch social extraction
    from web.social_finder import batch_extract_social_links
    from web.location_helper import matches_requested_location
    
    websites = [p.get("websiteUri") for p in raw_places if p.get("websiteUri")]
    socials_map = batch_extract_social_links(websites)

    # Format places and apply requested user filters
    formatted = [format_places_new_item(p, socials_map) for p in raw_places]
    qualified = []
    excluded = []

    for item in formatted:
        reasons = []
        if location_asked and not matches_requested_location(item["address"], location_asked, region_code):
            reasons.append(f"Outside requested location ({location_asked})")
        if req.no_website_only and item["has_website"]:
            reasons.append(f"Has website ({item['website']})")
        if req.min_reviews > 0 and item["reviews"] < req.min_reviews:
            reasons.append(f"Has {item['reviews']} reviews (< {req.min_reviews})")
        if req.min_rating > 0 and item["rating"] < req.min_rating:
            reasons.append(f"Rating {item['rating']}★ (< {req.min_rating}★)")
        if req.must_have_phone and not item["phone"]:
            reasons.append("Missing phone number")

        if not reasons:
            qualified.append(item)
        else:
            item_copy = dict(item)
            item_copy["exclusion_reason"] = " • ".join(reasons)
            excluded.append(item_copy)

    # Sort hot leads first
    qualified.sort(key=lambda x: (x["reviews"], x["rating"]), reverse=True)

    explanation = (
        f"Google returned {len(raw_places)} businesses for '{query}'. "
        f"{len(qualified)} matched filters, {len(excluded)} excluded."
    )

    return {
        "total_scanned": len(raw_places),
        "qualified_count": len(qualified),
        "excluded_count": len(excluded),
        "results": qualified,
        "excluded_results": excluded,
        "explanation": explanation,
        "credits_used": requests_used,
        "credit_status": dal.get_user_credits_status(user["id"])
    }


# ==============================================================================
# CRM Endpoints (Strictly User Scoped)
# ==============================================================================

@app.get("/api/crm/leads")
def api_crm_list(user: Dict = Depends(get_current_user)):
    """List CRM leads strictly owned by the current user."""
    leads = dal.get_user_leads(user["id"])
    return {
        "leads": leads,
        "stats": {
            "total": len(leads),
            "contacted": sum(1 for l in leads if l.get("stage") == "Contacted"),
            "won": sum(1 for l in leads if l.get("stage") == "Closed Won")
        }
    }


@app.post("/api/crm/leads")
def api_crm_save_lead(lead: CRMLeadItem, user: Dict = Depends(get_current_user)):
    """Save or update a lead in the user's CRM."""
    saved = dal.save_user_lead(user["id"], lead.dict())
    return {"status": "success", "lead": saved}


@app.patch("/api/crm/leads/{lead_id}/stage")
def api_crm_update_stage(lead_id: str, payload: LeadStageUpdate, user: Dict = Depends(get_current_user)):
    ok = dal.update_lead_stage(user["id"], lead_id, payload.stage)
    if not ok:
        raise HTTPException(status_code=404, detail="Lead not found")
    return {"status": "success", "lead_id": lead_id, "stage": payload.stage}


@app.patch("/api/crm/leads/{lead_id}/notes")
def api_crm_update_notes(lead_id: str, payload: LeadNotesUpdate, user: Dict = Depends(get_current_user)):
    ok = dal.update_lead_notes(user["id"], lead_id, payload.notes, payload.follow_up_date or "")
    if not ok:
        raise HTTPException(status_code=404, detail="Lead not found")
    return {"status": "success", "lead_id": lead_id, "notes": payload.notes}


@app.delete("/api/crm/leads/{lead_id}")
def api_crm_delete(lead_id: str, user: Dict = Depends(get_current_user)):
    ok = dal.delete_user_lead(user["id"], lead_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Lead not found")
    return {"status": "success", "deleted": lead_id}


# ==============================================================================
# Data Export & Account Deletion
# ==============================================================================

@app.get("/api/user/export")
def api_user_export_json(request: Request, user: Dict = Depends(get_current_user)):
    """Export complete user data package (GDPR/CCPA)."""
    ip = request.client.host if request.client else ""
    dal.log_audit_event(user["id"], "export_json", ip)
    data = dal.export_user_data(user["id"])
    return data


@app.get("/api/user/export/csv")
def api_user_export_csv(request: Request, user: Dict = Depends(get_current_user)):
    """Export all user leads to CSV."""
    ip = request.client.host if request.client else ""
    dal.log_audit_event(user["id"], "export_csv", ip)
    leads = dal.get_user_leads(user["id"])
    
    fieldnames = [
        "name", "phone", "rating", "reviews", "opportunity_tier", "category",
        "address", "website", "stage", "deal_value", "notes", "google_maps_url"
    ]
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for item in leads:
        writer.writerow(item)

    csv_bytes = output.getvalue().encode("utf-8")
    return StreamingResponse(
        io.BytesIO(csv_bytes),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="my_leads.csv"'}
    )


@app.post("/api/user/delete-account")
def api_user_delete_account(req: DeleteAccountRequest, request: Request, user: Dict = Depends(get_current_user)):
    """Permanently delete user account and all owned rows."""
    if req.confirm_text.strip() != "DELETE":
        raise HTTPException(status_code=400, detail="Type 'DELETE' in uppercase to confirm account deletion.")
    
    ip = request.client.host if request.client else ""
    dal.log_audit_event(user["id"], "account_delete", ip)
    dal.delete_user_account(user["id"])
    return {"success": True, "message": "Account and all associated data permanently deleted."}


# ==============================================================================
# Owner-Only Admin Management Endpoints
# ==============================================================================

@app.get("/api/admin/users")
def api_admin_list_users(owner: Dict = Depends(require_owner)):
    """List all platform users with plan, credits, and active status."""
    return {"users": dal.list_users_for_admin(owner["id"])}


@app.get("/api/users")
def api_users_alias(user: Dict = Depends(get_current_user)):
    """User team list for UI modal (Owner sees full admin controls, Member sees team list)."""
    if user["role"] == "owner":
        return {"users": dal.list_users_for_admin(user["id"])}
    return {"users": [{"id": user["id"], "username": user.get("display_name") or user["email"], "role": user["role"], "status": "active"}]}


@app.get("/api/admin/distribution")
def api_admin_distribution(owner: Dict = Depends(require_owner)):
    """Owner monthly credits distribution, active quotas, and pacing metrics."""
    return dal.get_monthly_distribution(owner["id"])


@app.patch("/api/admin/users/{user_id}/status")
def api_admin_toggle_user_status(user_id: str, payload: UserStatusUpdateRequest, owner: Dict = Depends(require_owner)):
    """Deactivate or activate user account."""
    ok = dal.set_user_active_status(owner["id"], user_id, payload.is_active)
    return {"success": ok}


@app.patch("/api/admin/users/{user_id}/plan")
def api_admin_update_user_plan(user_id: str, payload: UserPlanUpdateRequest, owner: Dict = Depends(require_owner)):
    """Change user subscription plan tier."""
    ok = dal.update_user_plan(owner["id"], user_id, payload.plan_id)
    return {"success": ok}


@app.get("/api/admin/audit-logs")
def api_admin_audit_logs(owner: Dict = Depends(require_owner)):
    """Inspect recent platform security audit records."""
    return {"logs": dal.get_recent_audit_logs(owner["id"])}


# ==============================================================================
# Local Scraper Integration (Locked down to Owner only)
# ==============================================================================

@app.get("/api/scraper/health")
def api_scraper_health(user: Dict = Depends(get_current_user)):
    if user.get("role") != "owner":
        return {"status": "hidden"}
    try:
        req = urllib.request.Request(f"{SCRAPER_BASE}/api/v1/jobs", headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return {"status": "up", "base_url": SCRAPER_BASE, "jobs_count": len(data)}
    except Exception as e:
        return {"status": "down", "base_url": SCRAPER_BASE, "error": str(e)}


@app.post("/api/scraper/start")
def api_scraper_start(req: ScraperJobRequest, owner: Dict = Depends(require_owner)):
    """Local scraper execution: Owner Only."""
    body = {
        "name": "web-lead-job",
        "keywords": req.keywords,
        "lang": "en",
        "zoom": 15,
        "lat": str(req.lat),
        "lon": str(req.lon),
        "fast_mode": False,
        "radius": 10000,
        "depth": req.depth,
        "email": req.email,
        "max_time": req.max_time
    }
    data = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        f"{SCRAPER_BASE}/api/v1/jobs",
        data=data,
        headers={"Content-Type": "application/json", "User-Agent": UA},
        method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as resp:
            res = json.loads(resp.read().decode("utf-8"))
            return {"job_id": res.get("id"), "status": "started"}
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", "replace")
        raise HTTPException(status_code=e.code, detail=f"Local scraper error: {err}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to connect to local scraper container: {str(e)}")


@app.get("/api/scraper/status/{job_id}")
def api_scraper_status(job_id: str, owner: Dict = Depends(require_owner)):
    try:
        req = urllib.request.Request(f"{SCRAPER_BASE}/api/v1/jobs/{job_id}", headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return {"status": data.get("Status", "unknown")}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/scraper/results/{job_id}")
def api_scraper_results(job_id: str, filters: ScraperFilterRequest, owner: Dict = Depends(require_owner)):
    try:
        req = urllib.request.Request(f"{SCRAPER_BASE}/api/v1/jobs/{job_id}/download", headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw_csv = resp.read().decode("utf-8", "replace")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to download results: {str(e)}")

    reader = csv.DictReader(io.StringIO(raw_csv))
    rows = list(reader)
    formatted = []
    for r in rows:
        name = r.get("title", "").strip()
        phone = r.get("phone", "").strip()
        website = r.get("website", "").strip()
        rating = float(r.get("review_rating") or 0.0) if r.get("review_rating") else 0.0
        reviews = int(float(r.get("review_count") or 0)) if r.get("review_count") else 0
        opp = compute_opportunity(rating, reviews, bool(website))
        socials = extract_social_links(website) if website else {}
        
        formatted.append({
            "id": r.get("place_id") or (name + "_" + phone),
            "name": name,
            "phone": phone,
            "website": website,
            "has_website": bool(website),
            "rating": rating,
            "reviews": reviews,
            "address": r.get("address", ""),
            "category": r.get("category", ""),
            "google_maps_url": r.get("link", ""),
            "opportunity_tier": opp["tier"],
            "opportunity_badge": opp["badge_color"],
            "opportunity_reason": opp["reason"],
            "social_links": socials,
            "source": "Local Scraper (Docker)"
        })

    return {
        "total_scanned": len(rows),
        "results": formatted
    }


# Static assets
static_dir = os.path.join(os.path.dirname(__file__), "static")
os.makedirs(static_dir, exist_ok=True)
app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
