import json
import httpx
from urllib.parse import urlencode
from fastapi import APIRouter, HTTPException, Depends, Query
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel
from app.config import get_settings
from app.auth import create_token, verify_password, hash_password, get_current_user
from app.database import get_db
from app.models.site import Site

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
def me(email: str = Depends(get_current_user)):
    return {"email": email}


# ─── GOOGLE SEARCH CONSOLE OAUTH ──────────────────────────────────────────────

GSC_SCOPES = "https://www.googleapis.com/auth/webmasters.readonly"
GSC_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GSC_TOKEN_URL = "https://oauth2.googleapis.com/token"

# CSRF tokens pour OAuth (en mémoire, token → (site_id, timestamp))
import secrets
import time as _time
_oauth_states: dict[str, tuple[int, float]] = {}

def _cleanup_oauth_states():
    """Supprime les tokens OAuth de plus de 10 minutes."""
    now = _time.time()
    expired = [k for k, (_, ts) in _oauth_states.items() if now - ts > 600]
    for k in expired:
        del _oauth_states[k]


@router.get("/gsc/connect/{site_id}")
def gsc_connect(site_id: int, _: str = Depends(get_current_user)):
    """Redirige vers Google OAuth pour connecter la Search Console d'un site."""
    s = get_settings()
    if not s.gsc_client_id or not s.gsc_client_secret:
        raise HTTPException(status_code=400, detail="GSC_CLIENT_ID et GSC_CLIENT_SECRET non configurés dans .env")

    _cleanup_oauth_states()
    state_token = secrets.token_urlsafe(32)
    _oauth_states[state_token] = (site_id, _time.time())

    params = {
        "client_id": s.gsc_client_id,
        "redirect_uri": s.gsc_redirect_uri,
        "response_type": "code",
        "scope": GSC_SCOPES,
        "access_type": "offline",
        "prompt": "consent",
        "state": state_token,
    }
    return {"url": f"{GSC_AUTH_URL}?{urlencode(params)}"}


@router.get("/gsc/callback")
def gsc_callback(
    code: str = Query(...),
    state: str = Query(""),
    db: Session = Depends(get_db),
):
    """Callback Google OAuth — échange le code contre un refresh_token et le stocke."""
    s = get_settings()
    entry = _oauth_states.pop(state, None)
    site_id = entry[0] if entry else None
    if not site_id:
        raise HTTPException(status_code=400, detail="Token OAuth invalide ou expiré — relance la connexion")

    site = db.query(Site).filter(Site.id == site_id).first()
    if not site:
        raise HTTPException(status_code=404, detail="Site not found")

    # Échanger le code contre les tokens
    resp = httpx.post(GSC_TOKEN_URL, data={
        "code": code,
        "client_id": s.gsc_client_id,
        "client_secret": s.gsc_client_secret,
        "redirect_uri": s.gsc_redirect_uri,
        "grant_type": "authorization_code",
    })

    if resp.status_code != 200:
        raise HTTPException(status_code=400, detail=f"Google OAuth error: {resp.text}")

    tokens = resp.json()
    if "refresh_token" not in tokens:
        raise HTTPException(status_code=400, detail="Pas de refresh_token — révoque l'accès dans ton compte Google et réessaie")

    # Stocker le token complet (client_id + client_secret + refresh_token)
    token_data = {
        "client_id": s.gsc_client_id,
        "client_secret": s.gsc_client_secret,
        "refresh_token": tokens["refresh_token"],
    }
    site.sc_token_json = json.dumps(token_data)
    db.commit()

    # Rediriger vers le dashboard avec un message de succès
    return RedirectResponse(url="/?gsc=ok")


@router.get("/gsc/properties/{site_id}")
def gsc_list_properties(site_id: int, db: Session = Depends(get_db), _: str = Depends(get_current_user)):
    """Liste les propriétés Search Console disponibles sur le compte connecté."""
    from app.services.search_console import SearchConsoleClient
    site = db.query(Site).filter(Site.id == site_id).first()
    if not site:
        raise HTTPException(status_code=404, detail="Site not found")
    if not site.sc_token_json:
        raise HTTPException(status_code=400, detail="GSC non connecté pour ce site")
    sc = SearchConsoleClient(site)
    properties = sc.list_properties()
    return {"properties": properties, "current": site.sc_property}


class GSCSelectRequest(BaseModel):
    property: str


@router.post("/gsc/select/{site_id}")
def gsc_select_property(site_id: int, data: GSCSelectRequest, db: Session = Depends(get_db), _: str = Depends(get_current_user)):
    """Sélectionne une propriété Search Console pour un site."""
    site = db.query(Site).filter(Site.id == site_id).first()
    if not site:
        raise HTTPException(status_code=404, detail="Site not found")
    site.sc_property = data.property
    db.commit()
    return {"message": "Propriété GSC sélectionnée", "sc_property": site.sc_property}
