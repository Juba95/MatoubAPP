from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
from app.database import get_db
from app.auth import get_current_user
from app.models.site import Site
from app.services.wp_installer import WPInstaller
from app.services.wp_publisher import WPPublisher

router = APIRouter(prefix="/generator", tags=["generator"], dependencies=[Depends(get_current_user)])


class WPInstallRequest(BaseModel):
    site_id: int
    reset_first: bool = False
    theme_mode: str = "custom"  # "custom" ou "divi"
    divi_api_key: Optional[str] = None
    business_name: Optional[str] = None
    business_address: Optional[str] = None
    business_phone: Optional[str] = None
    business_niche: Optional[str] = None
    content_mode: str = "prompt"  # "prompt" ou "competitor"
    content_prompt: Optional[str] = None
    competitor_url: Optional[str] = None


# Logs stockés en mémoire par site_id (simple pour v1)
_install_logs: dict[int, list[str]] = {}


def _log_for_site(site_id: int):
    if site_id not in _install_logs:
        _install_logs[site_id] = []

    def logger(msg: str):
        _install_logs[site_id].append(msg)

    return logger


@router.post("/install")
def install_wordpress(
    req: WPInstallRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Lance l'installation WordPress en tâche de fond"""
    site = db.query(Site).filter(Site.id == req.site_id).first()
    if not site:
        raise HTTPException(status_code=404, detail="Site not found")

    if not all([site.ftp_host, site.ftp_user, site.ftp_password, site.db_host, site.db_name]):
        raise HTTPException(status_code=400, detail="FTP and DB credentials required")

    _install_logs[site.id] = ["Installation démarrée..."]

    def run_install():
        log = _log_for_site(site.id)
        installer = WPInstaller(site, log_callback=log)
        try:
            if req.reset_first:
                installer.reset_site()
            result = installer.install_wordpress()
            log(f"SUCCESS: {result['url']}")
            log(f"Admin: {result['admin_url']}")
            log(f"Login: {result['username']} / {result['password']}")
        except Exception as e:
            log(f"ERROR: {str(e)}")

    background_tasks.add_task(run_install)
    return {"message": "Installation started", "site_id": site.id}


@router.get("/logs/{site_id}")
def get_install_logs(site_id: int):
    """Récupère les logs d'installation en cours"""
    logs = _install_logs.get(site_id, [])
    return {"site_id": site_id, "logs": logs, "count": len(logs)}


@router.post("/test-connection/{site_id}")
def test_wp_connection(site_id: int, db: Session = Depends(get_db)):
    """Teste la connexion WP REST API d'un site"""
    site = db.query(Site).filter(Site.id == site_id).first()
    if not site:
        raise HTTPException(status_code=404, detail="Site not found")
    if not site.wp_username or not site.wp_app_password:
        raise HTTPException(status_code=400, detail="WP credentials not configured")

    publisher = WPPublisher(site)
    result = publisher.test_connection()
    return result
