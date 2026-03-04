from datetime import datetime

from pydantic import BaseModel, EmailStr


class SignupRequest(BaseModel):
    email: EmailStr
    password: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    csrf_token: str


class UserResponse(BaseModel):
    id: str
    email: EmailStr
    created_at: datetime
    settings_json: dict
