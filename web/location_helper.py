"""Location Helper & Country Biasing for Google Places API (New).

Parses queries for explicit country/city/state indicators and provides:
  1. `regionCode` for Google Places API (New) (CLDR 2-character uppercase, e.g., 'GB' for UK, 'US', 'CA', 'AU')
  2. Location-matching verification to ensure results conform strictly to the requested geography.
"""
import re
from typing import Optional, Tuple, Set, Dict

_REGION_KEYWORDS: Dict[str, Set[str]] = {
    "GB": {
        "uk", "u.k.", "united kingdom", "england", "scotland", "wales", "great britain", "gb",
        "london", "manchester", "birmingham", "leeds", "glasgow", "liverpool", "bristol",
        "edinburgh", "sheffield", "cardiff", "belfast", "newcastle", "nottingham", "southampton",
        "oxford", "cambridge", "brighton", "york", "leicester", "coventry", "hull", "bradford"
    },
    "US": {
        "us", "u.s.", "usa", "u.s.a.", "united states", "america",
        "florida", "california", "texas", "new york", "ohio", "illinois", "georgia", "north carolina",
        "michigan", "pennsylvania", "arizona", "washington", "colorado", "nevada", "utah",
        "miami", "austin", "tampa", "orlando", "houston", "dallas", "chicago", "los angeles",
        "seattle", "denver", "atlanta", "phoenix", "boston", "san diego", "san francisco", "las vegas"
    },
    "CA": {
        "ca", "canada", "ontario", "quebec", "british columbia", "alberta", "toronto",
        "vancouver", "montreal", "calgary", "ottawa", "edmonton", "winnipeg", "mississauga"
    },
    "AU": {
        "au", "australia", "sydney", "melbourne", "brisbane", "perth", "adelaide", "gold coast"
    },
    "NZ": {
        "nz", "new zealand", "auckland", "wellington", "christchurch"
    },
    "IE": {
        "ie", "ireland", "dublin", "cork", "galway", "limerick"
    }
}

# Country aliases to check in formattedAddress
_COUNTRY_ADDRESS_MARKERS: Dict[str, Set[str]] = {
    "GB": {"united kingdom", "uk", "england", "scotland", "wales", "great britain", "u.k."},
    "US": {"united states", "usa", "u.s.a.", "us", "u.s."},
    "CA": {"canada"},
    "AU": {"australia"},
    "NZ": {"new zealand"},
    "IE": {"ireland"}
}


def detect_query_region(query: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Analyzes search query for location/country cues.
    Returns: (region_code, detected_location_string)
    Example: 'roofers in London UK' -> ('GB', 'London UK')
             'plumbers in uk' -> ('GB', 'uk')
    """
    if not query:
        return None, None
        
    q_lower = query.lower().strip()
    
    # 1. Extract location clause (e.g. 'in <location>', 'near <location>')
    location_text = ""
    match = re.search(r'\b(?:in|near|around)\s+([a-zA-Z0-9\s,.-]+)$', q_lower)
    if match:
        location_text = match.group(1).strip()
    else:
        # Check if query ends with a known location word
        tokens = [t.strip(",. ") for t in q_lower.split()]
        if tokens and tokens[-1] in {"uk", "usa", "us", "canada", "australia"}:
            location_text = tokens[-1]

    # 2. Match region code
    detected_region = None
    for region, keywords in _REGION_KEYWORDS.items():
        for kw in keywords:
            # Match whole word
            if re.search(r'\b' + re.escape(kw) + r'\b', q_lower):
                detected_region = region
                if not location_text:
                    location_text = kw
                break
        if detected_region:
            break

    return detected_region, location_text


def matches_requested_location(address: str, requested_location: Optional[str], region_code: Optional[str] = None) -> bool:
    """
    Verifies if formattedAddress matches the requested location/country.
    Returns True if address matches or if no explicit location was requested.
    """
    if not requested_location and not region_code:
        return True

    addr_lower = (address or "").lower()
    if not addr_lower:
        return True

    # 1. Region-level check: if a region was detected, verify address matches the country markers
    if region_code and region_code in _COUNTRY_ADDRESS_MARKERS:
        country_markers = _COUNTRY_ADDRESS_MARKERS[region_code]
        # Check if address contains ANY of this region's country markers
        has_region_marker = any(m in addr_lower for m in country_markers)
        
        # Also check other regions to prevent false cross-country results
        for other_region, other_markers in _COUNTRY_ADDRESS_MARKERS.items():
            if other_region != region_code:
                # If it explicitly matches another country's marker (and not ours), exclude it
                for om in other_markers:
                    if len(om) > 2 and om in addr_lower and not has_region_marker:
                        return False

    # 2. Specific location tokens (e.g., city, state)
    if requested_location:
        req_clean = requested_location.lower().strip()
        tokens = [t.strip(" ,.") for t in re.split(r'[, ]+', req_clean) if len(t.strip(" ,.")) >= 2]
        
        # If tokens are just country markers (e.g. 'uk'), we handled it via region check
        non_country_tokens = [t for t in tokens if t not in {"uk", "us", "usa", "in", "near"}]
        if non_country_tokens:
            # At least one specific city/state token must be present in address
            token_found = any(t in addr_lower for t in non_country_tokens)
            if not token_found:
                # If specific city like 'London' was asked but address doesn't mention it
                return False

    return True
