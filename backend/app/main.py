from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from app.database import init_db
from app.config import get_settings
from app.routers import (
    auth_router,
    sites_router,
    overview_router,
    actions_router,
    generator_router,
    geoloc_router,
    keywords_router,
)

app = FastAPI(
    title="MatoubeAPP",
    description="SEO Automation Dashboard",
    version="1.0.0",
    docs_url="/docs",
    redoc_url=None,
)

_origins = [o.strip() for o in get_settings().allowed_origins.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(sites_router)
app.include_router(overview_router)
app.include_router(actions_router)
app.include_router(generator_router)
app.include_router(geoloc_router)
app.include_router(keywords_router)


@app.on_event("startup")
def on_startup():
    init_db()


import os
_static_dir = os.path.join(os.path.dirname(__file__), "..", "static")


@app.get("/")
def root():
    return FileResponse(os.path.join(_static_dir, "dashboard.html"))


@app.get("/health")
def health():
    s = get_settings()
    return {
        "status": "ok",
        "config": {
            "database": bool(s.database_url),
            "redis": bool(s.redis_url),
            "anthropic": bool(s.anthropic_api_key),
            "dataforseo": bool(s.dataforseo_login),
            "gsc_oauth": bool(s.gsc_client_id and s.gsc_client_secret),
            "encryption": bool(s.encryption_key),
        }
    }
