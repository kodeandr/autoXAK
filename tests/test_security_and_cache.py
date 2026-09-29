import pytest
from datetime import timedelta
from app.core.security import (
    get_password_hash,
    hash_password,
    verify_password,
    create_access_token,
    decode_access_token
)


def test_password_hashing_and_verification():
    """Проверка криптографического хеширования PBKDF2-HMAC-SHA256."""
    password = "SafePassword4AutoXAK!"
    hashed = get_password_hash(password)
    
    assert hashed != password
    assert "$" in hashed
    assert verify_password(password, hashed) is True
    assert verify_password("WrongPassword123", hashed) is False
    assert verify_password(password, "invalid_hash_format") is False


def test_jwt_token_dict_claims_lifecycle():
    """Проверка JWT при передаче словаря claims."""
    payload = {"sub": "user_coverage_dev", "role": "engineer", "tier": "gold"}
    token = create_access_token(payload)
    
    assert isinstance(token, str)
    assert len(token) > 20

    decoded = decode_access_token(token)
    assert decoded is not None
    assert decoded["sub"] == "user_coverage_dev"
    assert decoded["role"] == "engineer"
    assert decoded["tier"] == "gold"


def test_jwt_token_string_id_lifecycle():
    """Проверка JWT при вызове с строковым user_id (как в эндпоинтах main.py)."""
    token = create_access_token("driver_autoXAK_99")
    decoded = decode_access_token(token)
    assert decoded is not None
    assert decoded["sub"] == "driver_autoXAK_99"


def test_jwt_invalid_token_handling():
    """Проверка безопасного отклонения поврежденных токенов."""
    assert decode_access_token("not.a.valid.token") is None
    assert decode_access_token("corrupted_string") is None
    assert decode_access_token("") is None


def test_jwt_expired_token_handling():
    """Проверка отклонения токена с истекшим сроком жизни."""
    # Токен, истекший 10 секунд назад
    token = create_access_token("expired_user", expires_delta=timedelta(seconds=-10))
    decoded = decode_access_token(token)
    assert decoded is None