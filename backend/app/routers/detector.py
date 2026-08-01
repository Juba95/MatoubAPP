"""
Routeur Détection IA — texte collé, URL de page, ou site entier (sitemap).

100 % gratuit : moteur stylométrique local (~45 signaux), aucun appel API.
"""
import hashlib
import logging
import traceback
from datetime import datetime

from fastapi import APIRouter, Depends, BackgroundTasks, HTTPException
from pydantic import BaseModel

from app.auth import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/detector", tags=["detector"],
                   dependencies=[Depends(get_current_user)])

_runs: dict[str, dict] = {}


class TextRequest(BaseModel):
    text: str
    language: str = ""


class UrlRequest(BaseModel):
    url: str
    mode: str = "page"        # "page" | "site"
    max_pages: int = 8


@router.post("/text")
def detect_text(req: TextRequest):
    """Analyse un contenu collé — synchrone (rapide, local)."""
    from app.services.ai_detector import analyze_text
    if not req.text.strip():
        raise HTTPException(status_code=400, detail="Texte requis")
    result = analyze_text(req.text, req.language)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.post("/url")
def detect_url(req: UrlRequest, background_tasks: BackgroundTasks):
    """Analyse une URL (page seule) ou un site (échantillon sitemap) en fond."""
    url = req.url.strip()
    if not url:
        raise HTTPException(status_code=400, detail="URL requise")

    key = hashlib.md5(f"{url}-{datetime.now().timestamp()}".encode()).hexdigest()[:12]
    _runs[key] = {"status": "running", "done": 0, "total": 1, "result": None, "error": ""}

    def run():
        from app.services.ai_detector import analyze_page, analyze_site
        state = _runs[key]

        def progress(done, total):
            state["done"] = done
            state["total"] = total

        try:
            if req.mode == "site":
                result = analyze_site(url, max_pages=max(3, min(req.max_pages, 15)),
                                      progress=progress)
            else:
                result = analyze_page(url)
            if "error" in result:
                _runs[key] = {"status": "error", "done": 0, "total": 1,
                              "result": None, "error": result["error"]}
            else:
                _runs[key] = {"status": "done", "done": state["total"],
                              "total": state["total"], "result": result, "error": ""}
        except Exception as exc:
            logger.error("detector error: %s", traceback.format_exc())
            _runs[key] = {"status": "error", "done": 0, "total": 1,
                          "result": None, "error": str(exc)}

    background_tasks.add_task(run)
    return {"key": key}


@router.get("/status/{key}")
def detect_status(key: str):
    run = _runs.get(key)
    if not run:
        raise HTTPException(status_code=404, detail="Analyse introuvable")
    return run
