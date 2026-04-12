from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.config import get_settings
from app.auth import create_token, verify_password, hash_password

router = APIRouter(prefix="/auth", tags=["auth"])

# Au premier lancement, le hash du mot de passe admin est généré
_admin_hash = None


def _get_admin_hash():
    global _admin_hash
    if _admin_hash is None:
        _admin_hash = hash_password(get_settings().admin_password)
    return _admin_hash


class LoginRequest(BaseModel):
    email: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


@router.post("/login", response_model=TokenResponse)
def login(req: LoginRequest):
    s = get_settings()
    if req.email != s.admin_email:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if not verify_password(req.password, _get_admin_hash()):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = create_token(req.email)
    return TokenResponse(access_token=token)


@router.get("/me")
def me():
    return {"email": get_settings().admin_email}
