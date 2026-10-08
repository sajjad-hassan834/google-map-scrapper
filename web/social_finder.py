"""Social Media Profile Extractor for MapLead.

Inspects business website domains for linked social media profiles:
  - Facebook
  - Instagram
  - LinkedIn
  - Twitter / X
  - YouTube

Optimized with:
  - In-memory cache
  - Fast concurrent execution (ThreadPoolExecutor)
  - 3.0s timeout per website
  - Fails silently ("let it go") if unreachable
"""
import re
import urllib.request
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional

_SOCIAL_CACHE: Dict[str, Dict[str, str]] = {}

_SOCIAL_PATTERNS = {
    "facebook": re.compile(r'https?://(?:www\.)?facebook\.com/(?:pages/|people/)?([a-zA-Z0-9.\-_]+)/?', re.IGNORECASE),
    "instagram": re.compile(r'https?://(?:www\.)?instagram\.com/([a-zA-Z0-9.\-_]+)/?', re.IGNORECASE),
    "linkedin": re.compile(r'https?://(?:www\.)?linkedin\.com/(?:company|in)/([a-zA-Z0-9.\-_]+)/?', re.IGNORECASE),
    "twitter": re.compile(r'https?://(?:www\.)?(?:twitter\.com|x\.com)/([a-zA-Z0-9.\-_]+)/?', re.IGNORECASE),
    "youtube": re.compile(r'https?://(?:www\.)?youtube\.com/(?:c/|channel/|user/|@)?([a-zA-Z0-9.\-_]+)/?', re.IGNORECASE),
}

def extract_social_links(website_url: Optional[str]) -> Dict[str, str]:
    """
    Given a single website URL, attempts a fast HTTP GET and extracts social links.
    Returns dict like {"facebook": "https://...", "instagram": "..."}.
    """
    if not website_url or not website_url.strip():
        return {}
        
    url = website_url.strip()
    if not url.startswith("http://") and not url.startswith("https://"):
        url = "https://" + url

    # Cache hit
    if url in _SOCIAL_CACHE:
        return _SOCIAL_CACHE[url]

    results: Dict[str, str] = {}
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
            }
        )
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            content_type = resp.headers.get("Content-Type", "")
            if "text/html" not in content_type.lower():
                _SOCIAL_CACHE[url] = {}
                return {}
            # Read first 64KB
            html = resp.read(65536).decode("utf-8", "ignore")

        for platform, pattern in _SOCIAL_PATTERNS.items():
            matches = pattern.findall(html)
            if matches:
                for m in matches:
                    handle = m.strip("/?# ")
                    if handle and handle.lower() not in ("sharer", "share", "intent", "home", "login", "signup", "p"):
                        results[platform] = f"https://www.{platform}.com/{handle}" if platform != "twitter" else f"https://x.com/{handle}"
                        break
    except Exception:
        # Silently let it go if unreachable
        pass

    _SOCIAL_CACHE[url] = results
    return results


def batch_extract_social_links(urls: List[str], max_workers: int = 8) -> Dict[str, Dict[str, str]]:
    """
    Parallel batch extraction for all businesses in search results.
    Runs concurrently so 20 websites take only ~2.5s total.
    """
    clean_urls = list({u.strip() for u in urls if u and u.strip()})
    results_map: Dict[str, Dict[str, str]] = {}
    
    # Pre-populate from cache
    needed_urls = []
    for u in clean_urls:
        formatted = u if u.startswith("http") else f"https://{u}"
        if formatted in _SOCIAL_CACHE:
            results_map[u] = _SOCIAL_CACHE[formatted]
        else:
            needed_urls.append(u)

    if not needed_urls:
        return results_map

    with ThreadPoolExecutor(max_workers=min(max_workers, len(needed_urls))) as executor:
        future_to_url = {executor.submit(extract_social_links, url): url for url in needed_urls}
        for future in as_completed(future_to_url):
            url = future_to_url[future]
            try:
                results_map[url] = future.result()
            except Exception:
                results_map[url] = {}

    return results_map
