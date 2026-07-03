"""
Suivi de position (rank tracking) via DataForSEO — sans proxy.

DataForSEO renvoie la SERP Google réelle de façon légale : on y lit la position
du domaine cible pour chaque mot-clé. Aucun scraping direct de Google, donc aucun
proxy à gérer. Coût indicatif : ~0,002 $ par mot-clé (SERP live/advanced).
"""
import hashlib
import logging
import traceback
from datetime import datetime

from fastapi import APIRouter, Depends, BackgroundTasks, HTTPException
from pydantic import BaseModel

from app.auth import get_current_user
from app.config import get_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/positions", tags=["positions"], dependencies=[Depends(get_current_user)])

# Suivis en mémoire (comme les autres modules)
_runs: dict[str, dict] = {}


class TrackRequest(BaseModel):
    domain: str
    keywords: list[str]
    location: str = "France"
    language: str = "fr"


@router.post("/estimate")
def estimate_positions(req: TrackRequest):
    """Coût/durée estimés avant de lancer le suivi."""
    kws = [k.strip() for k in req.keywords if k.strip()]
    n = len(kws)
    return {
        "keywords": n,
        "estimated_cost_usd": round(n * 0.002, 3),
        "estimated_seconds": max(2, round(n * 1.2)),
        "note": "DataForSEO renvoie la SERP Google réelle — aucun proxy nécessaire.",
    }


@router.post("/track")
def track_positions(req: TrackRequest, background_tasks: BackgroundTasks):
    """Lance un suivi de position (1 appel SERP live par mot-clé) en tâche de fond."""
    settings = get_settings()
    if not (settings.dataforseo_login and settings.dataforseo_password):
        raise HTTPException(status_code=400, detail="Identifiants DataForSEO non configurés")

    domain = req.domain.strip()
    if not domain:
        raise HTTPException(status_code=400, detail="Domaine requis")
    keywords = [k.strip() for k in req.keywords if k.strip()][:100]
    if not keywords:
        raise HTTPException(status_code=400, detail="Aucun mot-clé")

    key = hashlib.md5(f"{domain}-{datetime.now().timestamp()}".encode()).hexdigest()[:12]
    _runs[key] = {"status": "running", "done": 0, "total": len(keywords), "results": [], "error": ""}

    def run():
        from app.services.dataforseo import DataForSEOClient
        client = DataForSEOClient()
        state = _runs[key]
        results = []
        for kw in keywords:
            try:
                r = client.get_domain_position(kw, domain, location=req.location, language=req.language)
            except Exception as exc:
                r = {"keyword": kw, "rank": None, "url": "", "top3": [], "error": str(exc)[:150]}
            results.append(r)
            state["done"] = len(results)
            state["results"] = results
        # Synthèse
        ranked = [r for r in results if r.get("rank")]
        top10 = sum(1 for r in ranked if r["rank"] <= 10)
        top3 = sum(1 for r in ranked if r["rank"] <= 3)
        avg = round(sum(r["rank"] for r in ranked) / len(ranked), 1) if ranked else None
        _runs[key] = {
            "status": "done",
            "done": len(results),
            "total": len(keywords),
            "results": results,
            "error": "",
            "summary": {
                "tracked": len(results),
                "ranked": len(ranked),
                "not_ranked": len(results) - len(ranked),
                "top3": top3,
                "top10": top10,
                "avg_position": avg,
            },
        }

    background_tasks.add_task(run)
    return {"key": key, "total": len(keywords)}


@router.get("/status/{key}")
def positions_status(key: str):
    c = _runs.get(key)
    if not c:
        raise HTTPException(status_code=404, detail="Suivi introuvable")
    return {
        "status": c["status"], "done": c["done"], "total": c["total"],
        "error": c["error"],
        "results": c["results"],
        "summary": c.get("summary"),
    }
