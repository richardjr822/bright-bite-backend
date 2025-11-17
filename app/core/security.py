from datetime import datetime, timedelta
from typing import Any, Union, Optional
from jose import jwt, JWTError
from passlib.context import CryptContext
from app.core.config import get_settings
import os
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer

# Initialize settings
settings = get_settings()

# Password hashing context (bcrypt)
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Verify a password against a hash.
    If the hash is not bcrypt (for dev/testing), compare as plain text.
    """
    if not hashed_password or not isinstance(hashed_password, str):
        return False
    # TEMPORARY: For testing with plaintext passwords
    if not hashed_password.startswith('$2'):
        return plain_password == hashed_password
    # Normal bcrypt verification
    try:
        return pwd_context.verify(plain_password, hashed_password)
    except Exception as e:
        print(f"Password verification error: {e}")
        return False

def get_password_hash(password: str) -> str:
    """Generate a bcrypt hash for the password."""
    return pwd_context.hash(password)

def create_access_token(
    subject: Union[str, Any], user_type: str = "user", expires_delta: Optional[timedelta] = None
) -> str:
    """Create a JWT access token."""
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(
            minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES
        )
    to_encode = {"exp": expire, "sub": str(subject), "type": user_type}
    # Prefer JWT_SECRET_KEY if provided to match auth module
    secret = os.getenv("JWT_SECRET_KEY") or getattr(settings, "SECRET_KEY", None) or "change-me"
    alg = os.getenv("ALGORITHM") or getattr(settings, "ALGORITHM", None) or "HS256"
    encoded_jwt = jwt.encode(to_encode, secret, algorithm=alg)
    return encoded_jwt

# OAuth2 scheme for FastAPI dependency
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

def get_current_user(token: str = Depends(oauth2_scheme)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        # Decode using same key/alg as auth endpoints
        secret = os.getenv("JWT_SECRET_KEY") or getattr(settings, "SECRET_KEY", None) or "change-me"
        alg = os.getenv("ALGORITHM") or getattr(settings, "ALGORITHM", None) or "HS256"
        payload = jwt.decode(token, secret, algorithms=[alg])
        user_id: str = payload.get("sub")
        if user_id is None:
            raise credentials_exception
        return payload  # Optionally, fetch user from DB here
    except JWTError:
        raise credentials_exception