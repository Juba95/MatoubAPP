from app.routers.auth import router as auth_router
from app.routers.sites import router as sites_router
from app.routers.overview import router as overview_router
from app.routers.actions import router as actions_router
from app.routers.generator import router as generator_router
from app.routers.geoloc import router as geoloc_router
from app.routers.keywords import router as keywords_router
from app.routers.geo import router as geo_router

__all__ = [
    "auth_router",
    "sites_router",
    "overview_router",
    "actions_router",
    "generator_router",
    "geoloc_router",
    "keywords_router",
    "geo_router",
]
