# app/api/auth.py
import hashlib
import hmac
import secrets
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.db_models import User
from app.api.dependencies import require_user
from app.core.security import create_access_token
from app.api.yandex_auth import router as yandex_router

router = APIRouter(prefix="/api/v1/auth", tags=["Authentication"])
router.include_router(yandex_router)

# --- Вспомогательные криптографические функции (Zero-Dependencies) ---
def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 100_000)
    return f"{salt}${key.hex()}"

def verify_password(password: str, hashed: Optional[str]) -> bool:
    if not hashed or "$" not in hashed:
        return False
    salt, original_key = hashed.split("$", 1)
    key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 100_000)
    return hmac.compare_digest(key.hex(), original_key)

# --- DTO-схемы (Pydantic v2) ---
class AuthResponse(BaseModel):
    token: str
    status: str = "ok"
    user_id: str
    is_guest: bool
    name: str
    car_id: str
    fuel_price_rub: float

class RegisterRequest(BaseModel):
    email: str = Field(..., min_length=5, max_length=128)
    password: str = Field(..., min_length=6, max_length=128)
    name: Optional[str] = "Водитель"
    car_id: Optional[str] = "unconfigured"

class LoginRequest(BaseModel):
    email: str
    password: str = Field(min_length=6, max_length=128)

class UpgradeGuestRequest(BaseModel):
    guest_user_id: str
    email: str = Field(min_length=5, max_length=128)
    password: str = Field(min_length=6, max_length=128)
    name: Optional[str] = None

# --- Обработчики маршрутов ---
@router.post("/guest", response_model=AuthResponse)
async def create_guest_session(db: AsyncSession = Depends(get_db)):
    """Мгновенное создание изолированного гостевого профиля в 1 клик."""
    guest_id = f"guest_{secrets.token_hex(6)}"
    user = User(
        id=guest_id,
        is_guest=True,
        name="Гость",
        car_id="unconfigured",
        fuel_price_rub=72.4,
        total_savings_rub=0.0
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    return AuthResponse(
        token=create_access_token(user.id),
        user_id=user.id,
        is_guest=user.is_guest,
        name=user.name or "Водитель",
        car_id=user.car_id or "unconfigured",
        fuel_price_rub=user.fuel_price_rub
    )

@router.post("/register", response_model=AuthResponse)
async def register_user(req: RegisterRequest, db: AsyncSession = Depends(get_db)):
    """Регистрация полноценного профиля с проверкой уникальности почты."""
    clean_email = req.email.strip().lower()
    res = await db.execute(select(User).where(User.email == clean_email))
    if res.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Пользователь с таким email уже существует")

    user_id = f"usr_{secrets.token_hex(8)}"
    user = User(
        id=user_id,
        is_guest=False,
        name=req.name or "Водитель",
        email=clean_email,
        hashed_password=hash_password(req.password),
        car_id=req.car_id or "unconfigured",
        fuel_price_rub=72.4
    )
    db.add(user)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, "Пользователь с таким email уже существует")
    await db.refresh(user)

    return AuthResponse(
        token=create_access_token(user.id),
        user_id=user.id,
        is_guest=user.is_guest,
        name=user.name or "Водитель",
        car_id=user.car_id or "unconfigured",
        fuel_price_rub=user.fuel_price_rub
    )

@router.post("/login", response_model=AuthResponse)
async def login_user(req: LoginRequest, db: AsyncSession = Depends(get_db)):
    """Вход по email и паролю."""
    clean_email = req.email.strip().lower()
    res = await db.execute(select(User).where(User.email == clean_email))
    user = res.scalar_one_or_none()

    if not user or not verify_password(req.password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Неверный email или пароль")

    return AuthResponse(
        token=create_access_token(user.id),
        user_id=user.id,
        is_guest=user.is_guest,
        name=user.name or "Водитель",
        car_id=user.car_id or "unconfigured",
        fuel_price_rub=user.fuel_price_rub
    )

@router.post("/upgrade-guest", response_model=AuthResponse)
async def upgrade_guest_account(req: UpgradeGuestRequest, db: AsyncSession = Depends(get_db),
                                current_user: User = Depends(require_user)):
    """Конвертация своего гостя в профиль с паролем без изменения владельца поездок."""
    if req.guest_user_id != current_user.id or not current_user.is_guest:
        raise HTTPException(status_code=403, detail="Можно преобразовать только свой гостевой профиль")
    user = current_user
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Гостевая сессия не найдена")

    clean_email = req.email.strip().lower()
    existing = await db.execute(select(User).where(User.email == clean_email))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Почта уже привязана к другому профилю")

    user.is_guest = False
    user.email = clean_email
    user.hashed_password = hash_password(req.password)
    if req.name:
        user.name = req.name

    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, "Почта уже привязана к другому профилю")
    await db.refresh(user)

    return AuthResponse(
        token=create_access_token(user.id),
        user_id=user.id,
        is_guest=user.is_guest,
        name=user.name or "Водитель",
        car_id=user.car_id or "unconfigured",
        fuel_price_rub=user.fuel_price_rub
    )

@router.get("/me", response_model=AuthResponse)
async def get_current_user_info(user_id: Optional[str] = None, user: User = Depends(require_user)):
    """Проверка валидности сессии устройства."""
    if user_id is not None and user_id != user.id:
        raise HTTPException(status_code=403, detail="Чужой профиль недоступен")

    return AuthResponse(
        token=create_access_token(user.id),
        user_id=user.id,
        is_guest=user.is_guest,
        name=user.name or "Водитель",
        car_id=user.car_id or "unconfigured",
        fuel_price_rub=user.fuel_price_rub
    )
