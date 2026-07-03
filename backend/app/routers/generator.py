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


class FullInstallRequest(BaseModel):
    # Site
    domain: str
    site_title: str
    admin_user: str = "admin"
    admin_email: str = ""
    admin_pass: str = ""
    # FTP
    ftp_host: str
    ftp_user: str
    ftp_pass: str
    ftp_root: str = "/public_html"
    # MySQL
    db_host: str = "localhost"
    db_name: str
    db_user: str
    db_pass: str
    db_prefix: str = "wp_"
    # Options
    reset_first: bool = False
    use_divi: bool = True
    et_username: str = ""
    et_api_key: str = ""
    # Content generation
    generation_mode: str = "manuel"  # "manuel" | "concurrent"
    site_prompt: str = ""
    # Langue principale du site généré (thème, contenu, WPLANG, hreflang)
    main_language: str = "fr"
    # Langues additionnelles : un jeu de pages par langue (/en/, /ru/, /zh-tw/…)
    # + switcher + hreflang croisés
    languages: list[str] = []
    # Couleur principale (ex #1e6fd9) → palette dérivée automatiquement
    primary_color: str = ""
    competitor_urls: list[str] = []
    # Web Archive
    use_webarchive: bool = False
    # Logo : généré par IA (OpenAI gpt-image-1) ou repris depuis une URL,
    # redimensionné automatiquement (header / retina / favicon)
    generate_logo: bool = False
    logo_url: str = ""
    # Images du site : 5-10 images générées (OpenAI) et réparties dans le thème
    generate_images: bool = False
    image_count: int = 6
    # EAT
    eat_enabled: bool = True
    business_name: str = ""
    business_address: str = ""
    business_phone: str = ""
    business_email: str = ""
    business_lat: str = ""
    business_lng: str = ""
    business_hours: dict = {}
    # Blocs EAT
    eat_faq: bool = True
    eat_reviews: bool = True
    eat_hours: bool = True
    eat_map: bool = True
    eat_reassurance: bool = True
    eat_how_it_works: bool = True


# Logs stockes en memoire par site_id (simple pour v1)
_install_logs: dict[int, list[str]] = {}

# Logs pour le pipeline full-install, indexes par cle unique
_pipeline_logs: dict[str, dict] = {}


def _log_for_site(site_id: int):
    if site_id not in _install_logs:
        _install_logs[site_id] = []

    def logger(msg: str):
        _install_logs[site_id].append(msg)

    return logger


def _log_for_pipeline(key: str):
    """Cree un logger pour le pipeline full-install."""
    if key not in _pipeline_logs:
        _pipeline_logs[key] = {"step": "init", "logs": [], "done": False, "error": None}

    def logger(msg: str):
        _pipeline_logs[key]["logs"].append(msg)
        # Detecter l'etape courante a partir des messages
        lower = msg.lower()
        if "ftp" in lower:
            _pipeline_logs[key]["step"] = "ftp_upload"
        elif "mysql" in lower or "database" in lower or "bdd" in lower:
            _pipeline_logs[key]["step"] = "database"
        elif "wordpress" in lower and "install" in lower:
            _pipeline_logs[key]["step"] = "wp_install"
        elif "logo" in lower:
            _pipeline_logs[key]["step"] = "logo"
        elif "divi" in lower or "theme" in lower:
            _pipeline_logs[key]["step"] = "theme"
        elif "contenu" in lower or "content" in lower or "generation" in lower:
            _pipeline_logs[key]["step"] = "content"
        elif "eat" in lower or "e-a-t" in lower:
            _pipeline_logs[key]["step"] = "eat"
        elif "termin" in lower or "success" in lower:
            _pipeline_logs[key]["step"] = "done"

    return logger


@router.post("/install")
def install_wordpress(
    req: WPInstallRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Lance l'installation WordPress en tache de fond"""
    site = db.query(Site).filter(Site.id == req.site_id).first()
    if not site:
        raise HTTPException(status_code=404, detail="Site not found")

    if not all([site.ftp_host, site.ftp_user, site.ftp_password, site.db_host, site.db_name]):
        raise HTTPException(status_code=400, detail="FTP and DB credentials required")

    _install_logs[site.id] = ["Installation demarree..."]

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
    """Recupere les logs d'installation en cours"""
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


@router.post("/full-install")
def full_install(
    req: FullInstallRequest,
    background_tasks: BackgroundTasks,
):
    """Lance le pipeline complet d'installation WordPress en tache de fond."""
    import hashlib, time

    # Generer une cle unique pour cette installation
    raw = f"{req.domain}-{time.time()}"
    key = hashlib.md5(raw.encode()).hexdigest()[:12]

    _pipeline_logs[key] = {"step": "init", "logs": ["Pipeline demarre..."], "done": False, "error": None}

    # Normaliser le domaine (le pipeline attend une URL complète)
    domain = req.domain.strip().rstrip("/")
    if not domain.startswith(("http://", "https://")):
        domain = f"https://{domain}"

    # Mapping FullInstallRequest → schéma de config attendu par WPGeneratorPipeline.run()
    config = {
        "domain": domain,
        "site_name": req.site_title,
        "ftp_host": req.ftp_host,
        "ftp_user": req.ftp_user,
        "ftp_pass": req.ftp_pass,
        "ftp_root": req.ftp_root,
        "db_host": req.db_host,
        "db_name": req.db_name,
        "db_user": req.db_user,
        "db_pass": req.db_pass,
        "admin_user": req.admin_user,
        "wp_username": req.admin_user,
        "site_prompt": req.site_prompt,
        "mode": "divi" if req.use_divi else "custom",
        "reset": req.reset_first,
        "install_wp": True,
        "install_divi": req.use_divi,
        "et_username": req.et_username,
        "et_api_key": req.et_api_key,
        "scrape_archive": req.use_webarchive,
        "generate_logo": req.generate_logo,
        "logo_url": req.logo_url,
        "generate_images": req.generate_images,
        "image_count": req.image_count,
        "image_provider": "openai",
        "main_language": req.main_language,
        "languages": req.languages,
        "palette": (
            __import__("app.services.colors", fromlist=["build_palette"]).build_palette(req.primary_color)
            if req.primary_color else None
        ),
        "competitor_urls": req.competitor_urls if req.generation_mode == "concurrent" else [],
        "eat": {
            "enabled": req.eat_enabled,
            "name": req.business_name,
            "address": req.business_address,
            "phone": req.business_phone,
            "email": req.business_email,
            "lat": req.business_lat,
            "lng": req.business_lng,
            "hours": req.business_hours,
            "faq": req.eat_faq,
            "reviews": req.eat_reviews,
            "hours_block": req.eat_hours,
            "map": req.eat_map,
            "reassurance": req.eat_reassurance,
            "how_it_works": req.eat_how_it_works,
        },
    }
    if req.admin_pass:
        config["admin_pass"] = req.admin_pass

    def run_pipeline():
        log = _log_for_pipeline(key)
        try:
            from app.services.wp_generator import WPGeneratorPipeline

            pipeline = WPGeneratorPipeline()
            result = pipeline.run(config, log_callback=log)

            errors = result.get("errors", [])
            for err in errors:
                log(f"ERREUR étape « {err.get('step', '?')} » : {err.get('error', '')}")

            _pipeline_logs[key]["done"] = True
            if errors:
                summary = "; ".join(f"{e.get('step', '?')}: {e.get('error', '')}" for e in errors)
                _pipeline_logs[key]["error"] = summary
                log(f"TERMINE AVEC ERREURS: {len(result.get('steps_completed', []))} étapes OK, {len(errors)} en échec")
            else:
                _pipeline_logs[key]["step"] = "done"
                log(f"SUCCESS: Pipeline terminé — {len(result.get('steps_completed', []))} étapes, "
                    f"{len(result.get('pages_created', []))} pages créées — {result.get('admin_url', '')}")
        except ImportError:
            log("ERROR: Module app.services.wp_generator non disponible")
            _pipeline_logs[key]["done"] = True
            _pipeline_logs[key]["error"] = "Module wp_generator non disponible"
        except Exception as e:
            log(f"ERROR: {str(e)}")
            _pipeline_logs[key]["done"] = True
            _pipeline_logs[key]["error"] = str(e)

    background_tasks.add_task(run_pipeline)
    return {"message": "Pipeline started", "key": key}


class PreviewRequest(BaseModel):
    site_title: str = "Mon Site"
    site_prompt: str = ""
    main_language: str = "fr"
    primary_color: str = ""
    business_name: str = ""
    business_address: str = ""
    business_phone: str = ""
    business_email: str = ""


@router.post("/preview")
def generate_preview(req: PreviewRequest):
    """Génère une maquette HTML autonome de la page d'accueil (aperçu avant
    installation). Ne fait AUCUN appel FTP/WP : juste un rendu Claude."""
    from app.config import get_settings
    from app.services.wp_generator import build_preview_html
    from app.services.colors import build_palette

    settings = get_settings()
    if not settings.anthropic_api_key:
        raise HTTPException(status_code=400, detail="Clé API Claude non configurée")

    config = {
        "site_name": req.site_title or "Mon Site",
        "site_prompt": req.site_prompt,
        "main_language": req.main_language,
        "palette": build_palette(req.primary_color) if req.primary_color else None,
        "eat": {
            "name": req.business_name,
            "address": req.business_address,
            "phone": req.business_phone,
            "email": req.business_email,
        },
    }
    try:
        html = build_preview_html(config, settings.anthropic_api_key)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur de génération : {e}")
    return {"html": html}


@router.get("/pipeline-status/{key}")
def get_pipeline_status(key: str):
    """Recupere le statut et les logs du pipeline full-install."""
    entry = _pipeline_logs.get(key)
    if not entry:
        raise HTTPException(status_code=404, detail="Pipeline not found")
    return {
        "key": key,
        "step": entry["step"],
        "logs": entry["logs"],
        "done": entry["done"],
        "error": entry["error"],
        "count": len(entry["logs"]),
    }
