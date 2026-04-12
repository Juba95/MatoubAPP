from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.database import init_db
from app.routers import (
    auth_router,
    sites_router,
    overview_router,
    actions_router,
    generator_router,
    geoloc_router,
)

app = FastAPI(
    title="PBN Manager",
    description="Dashboard privé de gestion de PBN avec agent SEO autonome",
    version="1.0.0",
    docs_url="/docs",  # Swagger UI accessible en local
    redoc_url=None,
)

# CORS — restreint en prod (localhost seulement)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers
app.include_router(auth_router)
app.include_router(sites_router)
app.include_router(overview_router)
app.include_router(actions_router)
app.include_router(generator_router)
app.include_router(geoloc_router)


@app.on_event("startup")
def on_startup():
    init_db()


@app.get("/")
def root():
    return {"app": "PBN Manager", "version": "1.0.0", "status": "running"}


@app.get("/health")
def health():
    return {"status": "ok"}
