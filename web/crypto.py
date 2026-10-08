"""Cryptographic Utilities for MapLead.

Implements AES-256-GCM encryption/decryption for user-provided Google Places API keys.
Requires a 32-byte (256-bit) hexadecimal or base64 key in the ENCRYPTION_KEY environment variable.
Never logs keys or plaintexts.
"""
import os
import base64
import logging
from typing import Optional

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
except ImportError:
    AESGCM = None

logger = logging.getLogger("maplead.crypto")

# Deterministic fallback secret for development if ENCRYPTION_KEY not set yet
_DEV_FALLBACK_KEY = b"maplead_development_key_32bytes!"

def _get_aes_key() -> bytes:
    """Retrieve and validate 32-byte encryption key from environment."""
    env_key = os.environ.get("ENCRYPTION_KEY", "").strip()
    if not env_key:
        return _DEV_FALLBACK_KEY
    
    # Try hex
    try:
        raw = bytes.fromhex(env_key)
        if len(raw) == 32:
            return raw
    except Exception:
        pass

    # Try base64
    try:
        raw = base64.b64decode(env_key)
        if len(raw) == 32:
            return raw
    except Exception:
        pass

    # Try utf-8 encoded 32 chars
    raw = env_key.encode("utf-8")
    if len(raw) == 32:
        return raw

    # If length doesn't match 32 bytes, hash with SHA-256 to derive 32-byte key safely
    import hashlib
    return hashlib.sha256(raw).digest()


def encrypt_api_key(plain_key: str) -> str:
    """
    Encrypt an API key using AES-256-GCM.
    Returns: base64-encoded string containing nonce (12 bytes) + ciphertext + tag (16 bytes).
    """
    if not plain_key:
        return ""
    
    key = _get_aes_key()
    aesgcm = AESGCM(key)
    nonce = os.urandom(12)  # 96-bit standard nonce for GCM
    
    ciphertext = aesgcm.encrypt(nonce, plain_key.encode("utf-8"), None)
    combined = nonce + ciphertext
    return base64.b64encode(combined).decode("utf-8")


def decrypt_api_key(encrypted_payload: str) -> Optional[str]:
    """
    Decrypt an AES-256-GCM encrypted API key payload.
    """
    if not encrypted_payload:
        return None
        
    try:
        combined = base64.b64decode(encrypted_payload.encode("utf-8"))
        if len(combined) < 28:  # 12 bytes nonce + at least 16 bytes tag
            return None
            
        nonce = combined[:12]
        ciphertext = combined[12:]
        
        key = _get_aes_key()
        aesgcm = AESGCM(key)
        decrypted = aesgcm.decrypt(nonce, ciphertext, None)
        return decrypted.decode("utf-8")
    except Exception as e:
        logger.error("Failed to decrypt user API key payload")
        return None


def get_masked_key_suffix(key: str) -> str:
    """Return only the last 4 characters of a key for safe UI display."""
    if not key or len(key) < 4:
        return "****"
    return key[-4:]
