from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from pydantic import BaseModel
from app.auth import get_current_user

router = APIRouter(prefix="/geo", tags=["geo"], dependencies=[Depends(get_current_user)])

# Cache des analyses en mémoire
_analyses: dict[str, dict] = {}


class GEORequest(BaseModel):
    url: str


@router.post("/analyze")
def start_analysis(req: GEORequest, background_tasks: BackgroundTasks):
    """Lance une analyse GEO domaine en tâche de fond."""
    raw = req.url.strip()
    # Nettoyer : enlever protocol, www, trailing slash
    domain = raw
    for prefix in ("https://", "http://", "www."):
        if domain.startswith(prefix):
            domain = domain[len(prefix):]
    domain = domain.rstrip("/").split("/")[0]

    _analyses[domain] = {"status": "running", "domain": domain}

    def run():
        from app.services.geo_analyzer import GEOAnalyzer
        try:
            analyzer = GEOAnalyzer()
            result = analyzer.analyze_domain(domain)
            _analyses[domain] = {"status": "done", **result}
        except Exception as e:
            import traceback
            traceback.print_exc()
            _analyses[domain] = {"status": "error", "domain": domain, "error": str(e)}

    background_tasks.add_task(run)
    return {"message": "Analyse lancée", "domain": domain}


@router.get("/result")
def get_result(url: str):
    """Récupère le résultat d'une analyse GEO."""
    # Nettoyer la clé
    key = url.strip()
    for prefix in ("https://", "http://", "www."):
        if key.startswith(prefix):
            key = key[len(prefix):]
    key = key.rstrip("/").split("/")[0]

    data = _analyses.get(key)
    if not data:
        raise HTTPException(status_code=404, detail="Aucune analyse trouvée pour ce domaine")
    return data
