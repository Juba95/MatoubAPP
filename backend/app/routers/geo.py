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
    """Lance une analyse GEO en tâche de fond."""
    url = req.url.strip()
    if not url.startswith("http"):
        url = "https://" + url

    _analyses[url] = {"status": "running", "url": url}

    def run():
        from app.services.geo_analyzer import GEOAnalyzer
        try:
            analyzer = GEOAnalyzer()
            result = analyzer.analyze(url)
            _analyses[url] = {"status": "done", **result}
        except Exception as e:
            _analyses[url] = {"status": "error", "url": url, "error": str(e)}

    background_tasks.add_task(run)
    return {"message": "Analyse lancée", "url": url}


@router.get("/result")
def get_result(url: str):
    """Récupère le résultat d'une analyse GEO."""
    data = _analyses.get(url)
    if not data:
        # Essayer avec/sans trailing slash
        data = _analyses.get(url.rstrip("/")) or _analyses.get(url + "/")
    if not data:
        raise HTTPException(status_code=404, detail="Aucune analyse trouvée pour cette URL")
    return data
