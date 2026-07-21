from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
import bcrypt
import re
import uuid
from pydantic import BaseModel
from app.database import get_db
from app.models import User
from app.auth.jwt_handler import create_token
from app.auth.dependencies import get_current_user

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    account: str
    password: str


class RegisterRequest(BaseModel):
    account: str
    password: str


class AuthResponse(BaseModel):
    token: str
    user: dict


class UserResponse(BaseModel):
    id: str
    email: str | None = None
    phone: str | None = None
    sandbox_id: str | None = None


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))


def normalize_account(account: str) -> tuple[str, str]:
    value = account.strip()
    if not value:
        raise HTTPException(status_code=422, detail="Account is required")
    if "@" in value:
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value):
            raise HTTPException(status_code=422, detail="Invalid email format")
        return "email", value.lower()
    phone = re.sub(r"[\s-]", "", value)
    if not re.fullmatch(r"\+?\d{5,20}", phone):
        raise HTTPException(status_code=422, detail="Invalid phone format")
    return "phone", phone


async def find_user_by_account(db: AsyncSession, account_type: str, account: str) -> User | None:
    column = User.email if account_type == "email" else User.phone
    result = await db.execute(select(User).where(column == account))
    return result.scalar_one_or_none()


def auth_user(user: User) -> dict:
    return {"id": user.id, "email": user.email, "phone": user.phone, "sandbox_id": user.sandbox_id}


@router.post("/register", response_model=AuthResponse)
async def register(req: RegisterRequest, db: AsyncSession = Depends(get_db)):
    account_type, account = normalize_account(req.account)
    if await find_user_by_account(db, account_type, account):
        detail = "Email already registered" if account_type == "email" else "Phone already registered"
        raise HTTPException(status_code=400, detail=detail)
    user = User(id=uuid.uuid4().hex, password_hash=hash_password(req.password))
    if account_type == "email":
        user.email = account
    else:
        user.phone = account
    db.add(user)
    await db.commit()
    token = create_token(user.id)
    return AuthResponse(token=token, user=auth_user(user))


@router.post("/login", response_model=AuthResponse)
async def login(req: LoginRequest, db: AsyncSession = Depends(get_db)):
    account_type, account = normalize_account(req.account)
    user = await find_user_by_account(db, account_type, account)
    if not user or not verify_password(req.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = create_token(user.id)
    return AuthResponse(token=token, user=auth_user(user))


@router.get("/me", response_model=UserResponse)
async def me(user: User = Depends(get_current_user)):
    return UserResponse(id=user.id, email=user.email, phone=user.phone, sandbox_id=user.sandbox_id)
