"""Authentication, User Roles, and Session Security Module for MapLead.

Features:
- Dual authentication: Master APP_PASSWORD or Multi-user Accounts (Admin/Manager vs Member).
- Cryptographically signed 7-day session cookies & Bearer tokens using HMAC-SHA256.
- Strict IP rate limiting: max 5 failed attempts per 15 minutes.
- Role-based access control: Admin/Manager level with authority over all accounts.
- Never prints, logs, or exposes APP_PASSWORD or API keys.
"""
import os
import time
import hmac
import hashlib
import secrets
from typing import Dict, List, Optional, Tuple, Any
from fastapi import Request, HTTPException, status
from web.config import BRAND_NAME
from web.users_db import (
    init_users_db, get_user_with_hash, verify_password_hash,
    record_user_login, get_user_by_id
)

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


def create_session_token(user_id: int = 1, role: str = "admin") -> str:
    """Create a signed session token: <expiry>:<nonce>:<user_id>:<role>:<hmac>."""
    expiry = int(time.time()) + SESSION_DURATION_SECONDS
    nonce = secrets.token_hex(16)
    payload = f"{expiry}:{nonce}:{user_id}:{role}"
    secret = get_session_secret()
    signature = hmac.new(
        secret.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()
    return f"{payload}:{signature}"


def parse_session_token(token: str) -> Optional[Dict[str, Any]]:
    """Parse and cryptographically verify session token."""
    if not token or ":" not in token:
        return None
    parts = token.split(":")
    if len(parts) == 3:
        # Legacy format: expiry:nonce:signature (defaults to admin)
        expiry_str, nonce, signature = parts
        payload = f"{expiry_str}:{nonce}"
        user_id = 1
        role = "admin"
    elif len(parts) == 5:
        # Modern format: expiry:nonce:user_id:role:signature
        expiry_str, nonce, user_id_str, role, signature = parts
        payload = f"{expiry_str}:{nonce}:{user_id_str}:{role}"
        try:
            user_id = int(user_id_str)
        except ValueError:
            return None
    else:
        return None

    try:
        expiry = int(expiry_str)
    except ValueError:
        return None

    if time.time() > expiry:
        return None

    secret = get_session_secret()
    expected_signature = hmac.new(
        secret.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(signature, expected_signature):
        return None

    return {
        "user_id": user_id,
        "role": role,
        "expiry": expiry
    }


def verify_session_token(token: str) -> bool:
    """Verify if the token is valid and unexpired."""
    return parse_session_token(token) is not None


def extract_token_from_request(request: Request) -> Optional[str]:
    """Extract token from cookie or Authorization header."""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        auth_header = request.headers.get("authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header.split(" ", 1)[1].strip()
    return token


def get_current_user_from_request(request: Request) -> Optional[Dict[str, Any]]:
    """Return user record from database based on request token."""
    token = extract_token_from_request(request)
    if not token:
        return None
    data = parse_session_token(token)
    if not data:
        return None
    user_id = data["user_id"]
    user = get_user_by_id(user_id)
    if user:
        return user
    # Fallback to token role
    return {
        "id": user_id,
        "username": "admin",
        "display_name": "Super Admin",
        "role": data.get("role", "admin"),
        "status": "active"
    }


def authenticate_user(password: str, identifier: str = "", ip: str = "127.0.0.1") -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    """Authenticate via master password or user database credentials.

    Returns (success, message, user_dict).
    """
    allowed, wait_seconds = check_rate_limit(ip)
    if not allowed:
        minutes = max(1, (wait_seconds + 59) // 60)
        return False, f"Too many failed login attempts. Please wait {minutes} minute{'s' if minutes > 1 else ''}.", None

    clean_pwd = password.strip()
    clean_id = (identifier or "").strip().lower()

    # 1. Check if matches master APP_PASSWORD (Super Admin override)
    app_pwd = get_app_password()
    if app_pwd and hmac.compare_digest(clean_pwd, app_pwd):
        reset_rate_limit(ip)
        # Fetch or default to admin user
        admin_user = get_user_with_hash("admin") or {
            "id": 1,
            "username": "admin",
            "email": "admin@maplead.local",
            "display_name": "Super Admin (Manager)",
            "role": "admin",
            "status": "active"
        }
        admin_user.pop("password_hash", None)
        return True, "Login successful", admin_user

    # 2. Check user database
    # If no identifier provided, check if any user has this password
    user_record = None
    if clean_id:
        user_record = get_user_with_hash(clean_id)
    else:
        # Check admin account first if only password entered
        user_record = get_user_with_hash("admin")

    if user_record and verify_password_hash(clean_pwd, user_record.get("password_hash", "")):
        if user_record.get("status") == "disabled":
            return False, "This account has been disabled. Please contact your manager.", None
        
        record_user_login(user_record["id"])
        reset_rate_limit(ip)
        user_record.pop("password_hash", None)
        return True, "Login successful", user_record

    # Failed login
    record_failed_attempt(ip)
    return False, "Incorrect password. Please try again.", None


def authenticate_password(provided_password: str, ip: str) -> Tuple[bool, str]:
    """Backwards-compatible password check."""
    success, msg, _ = authenticate_user(password=provided_password, ip=ip)
    return success, msg


def get_client_ip(request: Request) -> str:
    """Extract client IP, taking X-Forwarded-For into account."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "127.0.0.1"


def verify_request_auth(request: Request) -> bool:
    """Check if request has a valid session token."""
    token = extract_token_from_request(request)
    return verify_session_token(token) if token else False


def require_session(request: Request):
    """Dependency protecting routes with 401."""
    if not verify_request_auth(request):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required. Please log in to continue."
        )


def require_admin(request: Request):
    """Dependency requiring Admin / Manager level role (403 Forbidden)."""
    user = get_current_user_from_request(request)
    if not user or user.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Manager / Administrator access required."
        )
    return user
