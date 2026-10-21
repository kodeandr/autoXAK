import os
import hmac
import hashlib
import base64
import json
import time
import secrets
import math
from datetime import timedelta
from typing import Optional, Dict, Any

SECRET_KEY = os.getenv("SECRET_KEY", "").strip()
if len(SECRET_KEY) < 32:
    raise RuntimeError("Set SECRET_KEY to a private, persistent random signing key of at least 32 characters")
ALGORITHM = "HS256"
TOKEN_EXPIRE_SECONDS = int(os.getenv("ACCESS_TOKEN_EXPIRE_SECONDS", 60 * 60 * 24 * 365))  # 1 год по умолчанию


def _base64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("utf-8").rstrip("=")


def _base64url_decode(data: str) -> bytes:
    padding = "=" * (4 - (len(data) % 4)) if len(data) % 4 != 0 else ""
    return base64.b64decode(data + padding, altchars=b"-_", validate=True)


def hash_password(password: str) -> str:
    """Криптографическое хеширование пароля PBKDF2-HMAC-SHA256 (чистый stdlib)."""
    salt = secrets.token_hex(16)
    pw_hash = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 100000).hex()
    return f"{salt}${pw_hash}"


# Алиас для совместимости
get_password_hash = hash_password


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Верификация пароля против PBKDF2 хеша."""
    try:
        salt, pw_hash = hashed_password.split("$", 1)
        check_hash = hashlib.pbkdf2_hmac("sha256", plain_password.encode("utf-8"), salt.encode("utf-8"), 100000).hex()
        return hmac.compare_digest(pw_hash, check_hash)
    except Exception:
        return False


def create_access_token(
    user_id_or_data: Any = None,
    custom_claims: Optional[Dict[str, Any]] = None,
    expires_delta: Optional[timedelta] = None,
    **kwargs
) -> str:
    """Генерация подписанного JWT-токена доступа (RFC 7519 HMAC-SHA256)."""
    target = user_id_or_data if user_id_or_data is not None else kwargs.get("data", kwargs.get("user_id", ""))
    
    claims: Dict[str, Any] = {}
    if isinstance(target, dict):
        user_id = str(target.get("sub", target.get("user_id", "")))
        claims = {k: v for k, v in target.items() if k not in ("sub", "iat", "exp")}
    else:
        user_id = str(target)
        
    if custom_claims:
        claims.update(custom_claims)

    now = int(time.time())
    if expires_delta is not None:
        exp = now + int(expires_delta.total_seconds())
    else:
        exp = now + TOKEN_EXPIRE_SECONDS

    header = {"alg": ALGORITHM, "typ": "JWT"}
    payload = {
        "sub": user_id,
        "iat": now,
        "exp": exp,
        **{k: v for k, v in claims.items() if k not in {"sub", "iat", "exp"}}
    }

    encoded_header = _base64url_encode(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    encoded_payload = _base64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))

    signature_input = f"{encoded_header}.{encoded_payload}".encode("utf-8")
    signature = hmac.new(SECRET_KEY.encode("utf-8"), signature_input, hashlib.sha256).digest()
    encoded_signature = _base64url_encode(signature)

    return f"{encoded_header}.{encoded_payload}.{encoded_signature}"


def decode_access_token(token: str) -> Optional[Dict[str, Any]]:
    """Декодирование и верификация подписи JWT-токена."""
    try:
        if not isinstance(token, str) or len(token) > 8192:
            return None
        parts = token.split(".")
        if len(parts) != 3:
            return None

        encoded_header, encoded_payload, encoded_signature = parts

        signature_input = f"{encoded_header}.{encoded_payload}".encode("utf-8")
        expected_sig = hmac.new(SECRET_KEY.encode("utf-8"), signature_input, hashlib.sha256).digest()
        provided_sig = _base64url_decode(encoded_signature)

        if not hmac.compare_digest(expected_sig, provided_sig):
            return None

        header = json.loads(_base64url_decode(encoded_header))
        if not isinstance(header, dict) or header.get("alg") != ALGORITHM or header.get("typ", "JWT") != "JWT":
            return None
        payload_bytes = _base64url_decode(encoded_payload)
        payload = json.loads(payload_bytes.decode("utf-8"))

        if not isinstance(payload, dict):
            return None
        subject, expires = payload.get("sub"), payload.get("exp")
        if not isinstance(subject, str) or not subject or len(subject) > 64:
            return None
        if isinstance(expires, bool) or not isinstance(expires, (int, float)):
            return None
        if not math.isfinite(expires) or expires <= time.time():
            return None
        if "nbf" in payload and (not isinstance(payload["nbf"], (int, float)) or not math.isfinite(payload["nbf"]) or payload["nbf"] > time.time()):
            return None

        return payload
    except Exception:
        return None