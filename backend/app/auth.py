from datetime import datetime, timedelta, timezone
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import jwt, JWTError
from passlib.context import CryptContext
from app.config import get_settings

security = HTTPBearer()
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def create_token(email: str) -> str:
    s = get_settings()
    expire = datetime.now(timezone.utc) + timedelta(minutes=s.jwt_expire_minutes)
    return jwt.encode(
        {"sub": email, "exp": expire},
        s.jwt_secret,
        algorithm=s.jwt_algorithm,
    )


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def get_current_user(
    creds: HTTPAuthorizationCredentials = Depends(security),
) -> str:
    s = get_settings()
    try:
        payload = jwt.decode(creds.credentials, s.jwt_secret, algorithms=[s.jwt_algorithm])
        email: str = payload.get("sub")
        if email is None:
            raise HTTPException(status_code=401, detail="Invalid token")
        return email
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")
