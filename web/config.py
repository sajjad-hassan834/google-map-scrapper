"""Central Application & Branding Configuration for MapLead.

Single source of truth for branding, versioning, and default application settings.
To rename or rebrand the application, edit BRAND_NAME here or set the APP_NAME
environment variable.
"""
import os

# ==============================================================================
# Brand & Identity
# ==============================================================================
BRAND_NAME = os.environ.get("APP_NAME", "MapLead").strip() or "MapLead"
BRAND_TAGLINE = os.environ.get("APP_TAGLINE", "Find local businesses that need a website").strip() or "Find local businesses that need a website"
APP_VERSION = "2.5.0"

# Metadata & Social Sharing
META_DESCRIPTION = (
    f"{BRAND_NAME} is a lead generation tool and CRM for web design freelancers. "
    f"Find local businesses with missing websites, weak review counts, and high opportunity."
)
SOCIAL_PREVIEW_TITLE = f"{BRAND_NAME} — Local Business Lead Finder & CRM"

# Default Credit & Search Settings
DEFAULT_MONTHLY_LIMIT = 1000
DEFAULT_SAFETY_BUFFER = 50
DEFAULT_PAGES_TO_FETCH = 3
DEFAULT_NO_WEBSITE_ONLY = True
DEFAULT_MIN_REVIEWS = 1
DEFAULT_MIN_RATING = 0.0
DEFAULT_MUST_HAVE_PHONE = True
