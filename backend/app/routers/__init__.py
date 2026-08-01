from app.routers.auth import router as auth_router
from app.routers.sites import router as sites_router
from app.routers.overview import router as overview_router
from app.routers.actions import router as actions_router
from app.routers.generator import router as generator_router
from app.routers.geoloc import router as geoloc_router
from app.routers.keywords import router as keywords_router
from app.routers.geo import router as geo_router
from app.routers.indexation import router as indexation_router
from app.routers.crawl import router as crawl_router
from app.routers.positions import router as positions_router
from app.routers.detector import router as detector_router

__all__ = [
    "auth_router",
    "sites_router",
    "overview_router",
    "actions_router",
    "generator_router",
    "geoloc_router",
    "keywords_router",
    "geo_router",
    "indexation_router",
    "crawl_router",
    "positions_router",
    "detector_router",
]
