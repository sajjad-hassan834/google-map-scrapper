"""Authentication & Session Security Module for MapLead.

Features:
- Password protection via APP_PASSWORD environment variable.
- Cryptographically signed 7-day session cookies using HMAC-SHA256.
- Strict IP rate limiting: max 5 failed attempts per 15 minutes.
- Constant-time password comparison to prevent timing attacks.
- Never prints, logs, or exposes APP_PASSWORD or API keys.
"""
import os
import time
import hmac
import hashlib
import secrets
from typing import Dict, List, Tuple
from fastapi import Request, HTTPException, status
from web.config import BRAND_NAME

_fallback_secret = secrets.token_hex(32)

def get_session_secret() -> str:
    return os.environ.get("SESSION_SECRET", "").strip() or _fallback_secret

def get_app_password() -> str:
    return os.environ.get("APP_PASSWORD", "").strip()

COOKIE_NAME = "maplead_session"
SESSION_DURATION_SECONDS = 7 * 24 * 3600  # 7 days

# Rate limiting storage: { ip: [timestamp, timestamp, ...] }
_failed_login_attempts: Dict[str, List[float]] = {}
MAX_FAILED_ATTEMPTS = 5
RATE_LIMIT_WINDOW_SECONDS = 15 * 60  # 15 minutes


def _clean_rate_limit_records(ip: str) -> List[float]:
    """Removes timestamps older than 15 minutes for the given IP."""
    now = time.time()
    cutoff = now - RATE_LIMIT_WINDOW_SECONDS
    attempts = [t for t in _failed_login_attempts.get(ip, []) if t > cutoff]
    _failed_login_attempts[ip] = attempts
    return attempts


def check_rate_limit(ip: str) -> Tuple[bool, int]:
    """Check if IP is allowed to attempt login. Returns (is_allowed, wait_seconds)."""
    attempts = _clean_rate_limit_records(ip)
    if len(attempts) >= MAX_FAILED_ATTEMPTS:
        # Calculate time until the oldest attempt drops off
        oldest = attempts[0]
        wait_seconds = max(1, int((oldest + RATE_LIMIT_WINDOW_SECONDS) - time.time()))
        return False, wait_seconds
    return True, 0


def record_failed_attempt(ip: str):
    """Record a failed login attempt for the IP."""
    attempts = _clean_rate_limit_records(ip)
    attempts.append(time.time())
    _failed_login_attempts[ip] = attempts


def reset_rate_limit(ip: str):
    """Clear rate limiting history on successful authentication."""
    if ip in _failed_login_attempts:
        del _failed_login_attempts[ip]


def create_session_token() -> str:
    """Create a signed session token lasting 7 days: <expiry>:<nonce>:<hmac>."""
    expiry = int(time.time()) + SESSION_DURATION_SECONDS
    nonce = secrets.token_hex(16)
    payload = f"{expiry}:{nonce}"
    secret = get_session_secret()
    signature = hmac.new(
        secret.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()
    return f"{payload}:{signature}"


def verify_session_token(token: str) -> bool:
    """Verify that the session token has a valid HMAC signature and has not expired."""
    if not token or ":" not in token:
        return False
    parts = token.split(":")
    if len(parts) != 3:
        return False
    expiry_str, nonce, signature = parts
    try:
        expiry = int(expiry_str)
    except ValueError:
        return False

    # Check if expired
    if time.time() > expiry:
        return False

    payload = f"{expiry}:{nonce}"
    secret = get_session_secret()
    expected_signature = hmac.new(
        secret.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()

    return hmac.compare_digest(signature, expected_signature)


def authenticate_password(provided_password: str, ip: str) -> Tuple[bool, str]:
    """Validate user password against APP_PASSWORD with rate-limiting.

    Returns (success, message).
    """
    # Rate limit check
    allowed, wait_seconds = check_rate_limit(ip)
    if not allowed:
        minutes = max(1, (wait_seconds + 59) // 60)
        return False, f"Too many failed login attempts. Please wait {minutes} minute{'s' if minutes > 1 else ''}."

    app_pwd = get_app_password()
    # If APP_PASSWORD is not configured in environment
    if not app_pwd:
        return False, "Application password is not configured on the server. Please set APP_PASSWORD in your environment."

    # Constant-time comparison
    is_valid = hmac.compare_digest(provided_password.strip(), app_pwd)
    if not is_valid:
        record_failed_attempt(ip)
        return False, "Incorrect password. Please try again."

    # Success: reset rate limit
    reset_rate_limit(ip)
    return True, "Login successful"


def get_client_ip(request: Request) -> str:
    """Extract client IP, taking X-Forwarded-For into account for reverse proxies."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "127.0.0.1"


def verify_request_auth(request: Request) -> bool:
    """Check if the incoming request has a valid session cookie."""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        # Also check Authorization: Bearer <token> if provided
        auth_header = request.headers.get("authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header.split(" ", 1)[1].strip()
    return verify_session_token(token) if token else False


def require_session(request: Request):
    """FastAPI dependency to protect routes with 401 Unauthorized."""
    if not verify_request_auth(request):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required. Please log in to continue."
        )
