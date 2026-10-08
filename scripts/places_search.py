#!/usr/bin/env python3
"""Search Google Places API directly for businesses with NO WEBSITE and ACTIVE REVIEWS.

Usage:
    python scripts/places_search.py "roofers in Tampa FL" --api-key YOUR_KEY --no-website --min-reviews 1
    
Or save API key in environment:
    set PLACES_API_KEY=YOUR_KEY
    python scripts/places_search.py "handyman in Austin TX" --no-website --min-reviews 5
"""

import argparse
import csv
import io
import json
import os
import sys
import time
import urllib.request
import urllib.parse
import urllib.error

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

UA = "google-maps-scraper-kit/places-search/1.0"

def search_places(api_key, query, max_pages=3):
    url = "https://places.googleapis.com/v1/places:searchText"
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": "places.id,places.displayName,places.formattedAddress,places.nationalPhoneNumber,places.websiteUri,places.rating,places.userRatingCount,places.googleMapsUri,places.primaryTypeDisplayName,nextPageToken",
        "User-Agent": UA
    }
    
    all_places = []
    next_page_token = None
    
    for page in range(max_pages):
        body = {"textQuery": query, "pageSize": 20}
        if next_page_token:
            body["pageToken"] = next_page_token
            
        req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            sys.exit(f"✗ Google Places API Error {e.code}: {e.read().decode('utf-8')}")
        except Exception as e:
            sys.exit(f"✗ Network error: {e}")
            
        places = data.get("places", [])
        all_places.extend(places)
        next_page_token = data.get("nextPageToken")
        if not next_page_token or len(places) == 0:
            break
        time.sleep(1.5)
        
    return all_places


def main():
    ap = argparse.ArgumentParser(description="Find Google Places leads with reviews & no website.")
    ap.add_argument("query", help='e.g. "roofers in Tampa FL" or "auto detailing in Miami FL"')
    ap.add_argument("--api-key", default=os.environ.get("PLACES_API_KEY", ""), help="Google Places API key")
    ap.add_argument("--no-website", action="store_true", default=True, help="Only return businesses with NO website")
    ap.add_argument("--allow-website", dest="no_website", action="store_false", help="Include businesses that have websites")
    ap.add_argument("--min-reviews", type=int, default=1, help="Minimum Google reviews (default: 1)")
    ap.add_argument("--min-rating", type=float, default=0.0, help="Minimum rating (default: 0.0)")
    ap.add_argument("--has-phone", action="store_true", default=True, help="Must have phone number")
    ap.add_argument("--pages", type=int, default=3, help="Max pages to fetch from Google (max 3, 20 results/page)")
    ap.add_argument("--out", default=None, help="Output CSV filename")
    a = ap.parse_args()

    if not a.api_key:
        sys.exit("✗ Missing Google Places API Key. Pass --api-key YOUR_KEY or set PLACES_API_KEY environment variable.")

    print(f"▶ Querying Google Places API New for: \"{a.query}\"...")
    raw = search_places(a.api_key, a.query, max_pages=a.pages)
    print(f"✓ Retrieved {len(raw)} total places from Google Places API.")

    qualified = []
    for p in raw:
        name = (p.get("displayName") or {}).get("text", "")
        phone = p.get("nationalPhoneNumber") or ""
        website = p.get("websiteUri") or ""
        reviews = int(p.get("userRatingCount") or 0)
        rating = float(p.get("rating") or 0.0)
        address = p.get("formattedAddress") or ""
        category = (p.get("primaryTypeDisplayName") or {}).get("text", "")
        maps_url = p.get("googleMapsUri") or ""

        if a.no_website and website:
            continue
        if reviews < a.min_reviews:
            continue
        if rating < a.min_rating:
            continue
        if a.has_phone and not phone:
            continue

        qualified.append({
            "title": name,
            "phone": phone,
            "website": website,
            "review_rating": rating,
            "review_count": reviews,
            "address": address,
            "category": category,
            "maps_url": maps_url
        })

    qualified.sort(key=lambda x: (x["review_count"], x["review_rating"]), reverse=True)
    print(f"★ Filter Result: {len(qualified)}/{len(raw)} qualified leads (NO website + >={a.min_reviews} reviews).\n")

    if not qualified:
        print("No matching leads found. Try a different city or niche!")
        return

    out_file = a.out or f"leads_no_website_{int(time.time())}.csv"
    with open(out_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["title", "phone", "review_rating", "review_count", "category", "address", "website", "maps_url"])
        writer.writeheader()
        writer.writerows(qualified)
    print(f"✓ Saved {len(qualified)} qualified leads → {out_file}\n")

    for i, lead in enumerate(qualified[:8], 1):
        hot_badge = "🔥 [HOT LEAD]" if lead["review_count"] >= 15 else "✨"
        print(f"  {i}. {hot_badge} {lead['title']} | {lead['phone']} | {lead['review_count']} reviews ({lead['review_rating']}★)")


if __name__ == "__main__":
    main()
