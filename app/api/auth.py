# app/api/auth.py
import hashlib
import hmac
import secrets
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.db_models import User

router = APIRouter(prefix="/api/v1/auth", tags=["Authentication"])

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
    status: str = "ok"
    user_id: str
    is_guest: bool
    name: str
    car_id: str
    fuel_price_rub: float
    total_savings_rub: float

class RegisterRequest(BaseModel):
    email: str = Field(..., min_length=5, max_length=128)
    password: str = Field(..., min_length=6, max_length=128)
    name: Optional[str] = "Водитель"
    car_id: Optional[str] = "audi_q3_20tfsi_180_culb"

class LoginRequest(BaseModel):
    email: str
    password: str

class UpgradeGuestRequest(BaseModel):
    guest_user_id: str
    email: str
    password: str
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
        car_id="audi_q3_20tfsi_180_culb",
        fuel_price_rub=72.4,
        total_savings_rub=0.0
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    return AuthResponse(
        user_id=user.id,
        is_guest=user.is_guest,
        name=user.name,
        car_id=user.car_id,
        fuel_price_rub=user.fuel_price_rub,
        total_savings_rub=user.total_savings_rub
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
        car_id=req.car_id or "audi_q3_20tfsi_180_culb",
        fuel_price_rub=72.4
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    return AuthResponse(
        user_id=user.id,
        is_guest=user.is_guest,
        name=user.name,
        car_id=user.car_id,
        fuel_price_rub=user.fuel_price_rub,
        total_savings_rub=user.total_savings_rub
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
        user_id=user.id,
        is_guest=user.is_guest,
        name=user.name,
        car_id=user.car_id,
        fuel_price_rub=user.fuel_price_rub,
        total_savings_rub=user.total_savings_rub
    )

@router.post("/upgrade-guest", response_model=AuthResponse)
async def upgrade_guest_account(req: UpgradeGuestRequest, db: AsyncSession = Depends(get_db)):
    """Конвертация гостя в зарегистрированного пользователя с сохранением баланса копилки."""
    user = await db.get(User, req.guest_user_id)
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

    await db.commit()
    await db.refresh(user)

    return AuthResponse(
        user_id=user.id,
        is_guest=user.is_guest,
        name=user.name,
        car_id=user.car_id,
        fuel_price_rub=user.fuel_price_rub,
        total_savings_rub=user.total_savings_rub
    )

@router.get("/me", response_model=AuthResponse)
async def get_current_user_info(user_id: str, db: AsyncSession = Depends(get_db)):
    """Проверка валидности сессии устройства."""
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Пользователь не найден")

    return AuthResponse(
        user_id=user.id,
        is_guest=user.is_guest,
        name=user.name,
        car_id=user.car_id,
        fuel_price_rub=user.fuel_price_rub,
        total_savings_rub=user.total_savings_rub
    )