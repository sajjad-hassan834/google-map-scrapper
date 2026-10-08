import os
import csv
import io
import json
import urllib.request
import urllib.parse
import urllib.error
from typing import List, Optional, Dict, Any
from fastapi import FastAPI, HTTPException, Query, Body
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from web.crm_db import (
    get_all_crm_leads, save_or_update_lead, import_bulk_leads,
    update_lead_stage, update_lead_notes, delete_lead, get_crm_stats
)
from web.credits_db import (
    get_credit_status, update_credit_settings, increment_credit_usage,
    log_places_search, generate_cache_key, get_cached_search_results,
    store_cached_search_results
)

app = FastAPI(title="MapLead Pro & CRM Engine", version="2.5.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

SCRAPER_BASE = os.environ.get("SCRAPER_BASE_URL", "http://localhost:8080")
UA = "google-maps-scraper-kit/2.5"

# Load .env if present
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

class PlacesSearchRequest(BaseModel):
    api_key: Optional[str] = ""
    query: str
    target_lead_count: int = 5
    no_website_only: bool = True
    min_reviews: int = 1
    min_rating: float = 0.0
    must_have_phone: bool = True
    max_pages: int = 3

class CreditSettingsUpdate(BaseModel):
    monthly_limit: int = Field(default=1000, ge=1)
    safety_buffer: int = Field(default=50, ge=0)

class ScraperJobRequest(BaseModel):
    keywords: List[str]
    lat: str
    lon: str
    depth: int = 5
    email: bool = False
    max_time: int = 300

class ScraperFilterRequest(BaseModel):
    no_website_only: bool = True
    min_reviews: int = 1
    min_rating: float = 0.0
    must_have_phone: bool = True

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

def search_google_places_new(api_key: str, query: str, max_pages: int = 3):
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

    import time
    for page in range(max_pages):
        body: Dict[str, Any] = {
            "textQuery": query,
            "pageSize": 20
        }
        if next_page_token:
            body["pageToken"] = next_page_token

        req_data = json.dumps(body).encode("utf-8")
        request = urllib.request.Request(url, data=req_data, headers=headers, method="POST")

        # Increment count BEFORE/AS request is dispatched so failed requests are counted
        increment_credit_usage(1)
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


def format_places_new_item(item: Dict[str, Any]) -> Dict[str, Any]:
    name = (item.get("displayName") or {}).get("text", "")
    address = item.get("formattedAddress", "")
    phone = item.get("nationalPhoneNumber") or item.get("internationalPhoneNumber") or ""
    website = item.get("websiteUri") or ""
    rating = float(item.get("rating") or 0.0)
    reviews = int(item.get("userRatingCount") or 0)
    maps_url = item.get("googleMapsUri") or ""
    category = (item.get("primaryTypeDisplayName") or {}).get("text", "")
    
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
        "source": "Google Places API"
    }


@app.get("/api/places/config")
def api_places_config():
    has_key = bool(os.environ.get("PLACES_API_KEY") or os.environ.get("GOOGLE_PLACES_API_KEY"))
    return {"has_env_key": has_key}


@app.get("/api/credits/status")
def api_credits_status():
    return get_credit_status()


@app.patch("/api/credits/settings")
def api_credits_settings(settings: CreditSettingsUpdate):
    return update_credit_settings(settings.monthly_limit, settings.safety_buffer)


@app.post("/api/places/search")
def api_places_search(req: PlacesSearchRequest):
    api_key = (req.api_key or "").strip() or os.environ.get("PLACES_API_KEY", "").strip() or os.environ.get("GOOGLE_PLACES_API_KEY", "").strip()
    if not api_key:
        raise HTTPException(status_code=400, detail="Google Places API Key is required. Please save PLACES_API_KEY in your environment.")
    if not req.query.strip():
        raise HTTPException(status_code=400, detail="Search query is required.")

    # 1. Check 30-Day Result Cache
    pages_to_fetch = max(1, min(3, req.max_pages))
    filters_dict = {
        "no_website_only": req.no_website_only,
        "min_reviews": req.min_reviews,
        "min_rating": req.min_rating,
        "must_have_phone": req.must_have_phone,
        "max_pages": pages_to_fetch
    }
    cache_key = generate_cache_key(req.query, filters_dict)
    cached_data = get_cached_search_results(cache_key)
    if cached_data:
        log_places_search(req.query.strip(), pages_to_fetch, credits_used=0, cached=True)
        cached_data["from_cache"] = True
        cached_data["credits_used"] = 0
        cached_data["credit_status"] = get_credit_status()
        return cached_data

    # 2. Enforce Free Tier Credit Hard Stop
    credit_status = get_credit_status()
    safe_left = credit_status["safe_credits_left"]
    if safe_left < pages_to_fetch:
        raise HTTPException(
            status_code=429,
            detail="You've reached this month's free limit. Use Deep search (free, local scraper) or wait until the 1st."
        )

    # 3. Execute Search
    raw_places, requests_used = search_google_places_new(api_key, req.query.strip(), max_pages=pages_to_fetch)
    log_places_search(req.query.strip(), pages_to_fetch, credits_used=requests_used, cached=False)

    total_scanned = len(raw_places)
    formatted = [format_places_new_item(p) for p in raw_places]

    qualified = []
    excluded = []
    
    breakdown = {
        "excluded_has_website": 0,
        "excluded_low_reviews": 0,
        "excluded_low_rating": 0,
        "excluded_no_phone": 0
    }

    for item in formatted:
        reasons = []
        if req.no_website_only and item["has_website"]:
            reasons.append(f"Has website ({item['website']})")
            breakdown["excluded_has_website"] += 1
        if item["reviews"] < req.min_reviews:
            reasons.append(f"Has {item['reviews']} reviews (minimum {req.min_reviews} required)")
            breakdown["excluded_low_reviews"] += 1
        if item["rating"] < req.min_rating:
            reasons.append(f"Rating {item['rating']}★ (minimum {req.min_rating}★ required)")
            breakdown["excluded_low_rating"] += 1
        if req.must_have_phone and not item["phone"]:
            reasons.append("Missing phone number")
            breakdown["excluded_no_phone"] += 1

        if not reasons:
            qualified.append(item)
        else:
            excluded_item = dict(item)
            excluded_item["exclusion_reason"] = " • ".join(reasons)
            excluded.append(excluded_item)

    # Sort hot leads first
    qualified.sort(key=lambda x: (x["reviews"], x["rating"]), reverse=True)
    hot_leads_count = sum(1 for x in qualified if "HOT" in x["opportunity_tier"])

    explanation = (
        f"Google returned {total_scanned} total businesses for '{req.query}'. "
        f"{len(qualified)} matched all your filters. "
        f"{len(excluded)} were excluded ({breakdown['excluded_has_website']} already have websites, "
        f"{breakdown['excluded_low_reviews']} have < {req.min_reviews} reviews, "
        f"{breakdown['excluded_no_phone']} missing phone)."
    )

    response_payload = {
        "total_scanned": total_scanned,
        "qualified_count": len(qualified),
        "excluded_count": len(excluded),
        "hot_leads_count": hot_leads_count,
        "results": qualified,
        "excluded_results": excluded,
        "filtering_breakdown": breakdown,
        "explanation": explanation,
        "from_cache": False,
        "credits_used": requests_used,
        "credit_status": get_credit_status()
    }

    # 4. Store in 30-Day Cache
    store_cached_search_results(cache_key, req.query.strip(), response_payload)
    return response_payload


# ----------------- CRM Endpoints -----------------

@app.get("/api/crm/leads")
def api_crm_list():
    return {"leads": get_all_crm_leads(), "stats": get_crm_stats()}


@app.post("/api/crm/leads")
def api_crm_save_lead(lead: CRMLeadItem):
    saved = save_or_update_lead(lead.dict())
    return {"status": "success", "lead": saved, "stats": get_crm_stats()}


@app.post("/api/crm/import")
def api_crm_import(leads: List[CRMLeadItem]):
    data = [l.dict() for l in leads]
    count = import_bulk_leads(data)
    return {"status": "success", "imported_count": count, "stats": get_crm_stats()}


@app.patch("/api/crm/leads/{lead_id}/stage")
def api_crm_update_stage(lead_id: str, payload: LeadStageUpdate):
    ok = update_lead_stage(lead_id, payload.stage)
    if not ok:
        raise HTTPException(status_code=404, detail="Lead not found")
    return {"status": "success", "lead_id": lead_id, "stage": payload.stage, "stats": get_crm_stats()}


@app.patch("/api/crm/leads/{lead_id}/notes")
def api_crm_update_notes(lead_id: str, payload: LeadNotesUpdate):
    ok = update_lead_notes(lead_id, payload.notes, payload.follow_up_date)
    if not ok:
        raise HTTPException(status_code=404, detail="Lead not found")
    return {"status": "success", "lead_id": lead_id, "notes": payload.notes}


@app.delete("/api/crm/leads/{lead_id}")
def api_crm_delete(lead_id: str):
    ok = delete_lead(lead_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Lead not found")
    return {"status": "success", "deleted": lead_id, "stats": get_crm_stats()}


@app.get("/api/crm/stats")
def api_crm_get_stats():
    return get_crm_stats()


# ----------------- Local Scraper Integration (gosom) -----------------

@app.get("/api/scraper/health")
def api_scraper_health():
    try:
        req = urllib.request.Request(f"{SCRAPER_BASE}/api/v1/jobs", headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return {"status": "up", "base_url": SCRAPER_BASE, "jobs_count": len(data)}
    except Exception as e:
        return {"status": "down", "base_url": SCRAPER_BASE, "error": str(e)}


@app.post("/api/scraper/start")
def api_scraper_start(req: ScraperJobRequest):
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
def api_scraper_status(job_id: str):
    try:
        req = urllib.request.Request(f"{SCRAPER_BASE}/api/v1/jobs/{job_id}", headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return {"status": data.get("Status", "unknown")}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/scraper/results/{job_id}")
def api_scraper_results(job_id: str, filters: ScraperFilterRequest):
    try:
        req = urllib.request.Request(f"{SCRAPER_BASE}/api/v1/jobs/{job_id}/download", headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw_csv = resp.read().decode("utf-8", "replace")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to download results: {str(e)}")

    reader = csv.DictReader(io.StringIO(raw_csv))
    rows = list(reader)
    total_scanned = len(rows)

    qualified = []
    excluded = []
    breakdown = {
        "excluded_has_website": 0,
        "excluded_low_reviews": 0,
        "excluded_low_rating": 0,
        "excluded_no_phone": 0
    }

    for r in rows:
        name = r.get("title", "").strip()
        website = r.get("website", "").strip()
        phone = r.get("phone", "").strip()
        
        try:
            reviews = int(float(r.get("review_count") or 0))
        except (ValueError, TypeError):
            reviews = 0
            
        try:
            rating = float(r.get("review_rating") or 0.0)
        except (ValueError, TypeError):
            rating = 0.0

        has_site = bool(website)
        reasons = []

        if filters.no_website_only and has_site:
            reasons.append(f"Has website ({website})")
            breakdown["excluded_has_website"] += 1
        if reviews < filters.min_reviews:
            reasons.append(f"Has {reviews} reviews (minimum {filters.min_reviews} required)")
            breakdown["excluded_low_reviews"] += 1
        if rating < filters.min_rating:
            reasons.append(f"Rating {rating}★ (minimum {filters.min_rating}★ required)")
            breakdown["excluded_low_rating"] += 1
        if filters.must_have_phone and not phone:
            reasons.append("Missing phone number")
            breakdown["excluded_no_phone"] += 1

        opp = compute_opportunity(rating, reviews, has_site)

        item = {
            "id": r.get("place_id") or r.get("cid") or (name + "_" + phone),
            "name": name,
            "phone": phone,
            "website": website,
            "has_website": has_site,
            "rating": rating,
            "reviews": reviews,
            "address": r.get("address", "") or r.get("complete_address", ""),
            "category": r.get("category", ""),
            "google_maps_url": r.get("link", ""),
            "emails": r.get("emails", ""),
            "opportunity_tier": opp["tier"],
            "opportunity_badge": opp["badge_color"],
            "opportunity_reason": opp["reason"],
            "source": "Local Scraper (Docker)"
        }

        if not reasons:
            qualified.append(item)
        else:
            item["exclusion_reason"] = " • ".join(reasons)
            excluded.append(item)

    qualified.sort(key=lambda x: (x["reviews"], x["rating"]), reverse=True)
    hot_leads_count = sum(1 for x in qualified if "HOT" in x["opportunity_tier"])

    return {
        "total_scanned": total_scanned,
        "qualified_count": len(qualified),
        "excluded_count": len(excluded),
        "hot_leads_count": hot_leads_count,
        "results": qualified,
        "excluded_results": excluded,
        "filtering_breakdown": breakdown,
        "explanation": f"Scraped {total_scanned} listings. {len(qualified)} matched all filters, {len(excluded)} excluded."
    }


@app.get("/api/geocode")
def api_geocode(place: str = Query(...)):
    q = urllib.parse.urlencode({"format": "json", "limit": 1, "q": place})
    url = f"https://nominatim.openstreetmap.org/search?{q}"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            hits = json.loads(resp.read().decode("utf-8"))
            if hits:
                return {"lat": str(hits[0]["lat"]), "lon": str(hits[0]["lon"]), "display_name": hits[0].get("display_name")}
            raise HTTPException(status_code=404, detail="Location not found")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/export/csv")
def api_export_csv(leads: List[Dict[str, Any]] = Body(...)):
    fieldnames = [
        "name", "phone", "rating", "reviews", "opportunity_tier", "category",
        "address", "website", "stage", "deal_value", "notes", "google_maps_url", "opportunity_reason"
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
        headers={"Content-Disposition": 'attachment; filename="leads_crm_pipeline.csv"'}
    )

static_dir = os.path.join(os.path.dirname(__file__), "static")
os.makedirs(static_dir, exist_ok=True)
app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
