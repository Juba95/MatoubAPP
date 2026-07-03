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


# ---------------------------------------------------------------------------
# Visibilité IA en direct : questions réelles aux LLM + AI Overview Google
# ---------------------------------------------------------------------------

_ai_vis_runs: dict[str, dict] = {}


class AIVisibilityRequest(BaseModel):
    domain: str
    brand: str = ""
    questions: list[str] = []
    platforms: list[str] = []          # chat_gpt / gemini / claude / perplexity
    keywords: list[str] = []           # mots-clés pour la détection AI Overview
    location: str = "France"
    language: str = "fr"


@router.post("/ai-visibility/estimate")
def ai_visibility_estimate(req: AIVisibilityRequest):
    """Coût estimé avant lancement (affiché au clic)."""
    from app.services.ai_visibility import (
        PLATFORMS, COST_PER_LLM_QUESTION, COST_PER_AIO_KEYWORD,
    )
    platforms = [p for p in req.platforms if p in PLATFORMS] or list(PLATFORMS)
    nq = len([q for q in req.questions if q.strip()])
    nk = len([k for k in req.keywords if k.strip()])
    llm_calls = nq * len(platforms)
    cost = llm_calls * COST_PER_LLM_QUESTION + nk * COST_PER_AIO_KEYWORD
    return {
        "llm_calls": llm_calls,
        "aio_checks": nk,
        "estimated_cost_usd": round(cost, 3),
        "estimated_seconds": max(5, llm_calls * 8 + nk * 3),
    }


@router.post("/ai-visibility/start")
def ai_visibility_start(req: AIVisibilityRequest, background_tasks: BackgroundTasks):
    """Lance le test de visibilité IA en tâche de fond.

    Pour chaque question × plateforme : la question est réellement posée au
    LLM (via DataForSEO) et on vérifie mention + citation du domaine. Pour
    chaque mot-clé : détection du bloc AI Overview Google + citation.
    """
    from app.services.ai_visibility import PLATFORMS

    domain = req.domain.strip()
    if not domain:
        raise HTTPException(status_code=400, detail="Domaine requis")
    questions = [q.strip() for q in req.questions if q.strip()][:20]
    keywords = [k.strip() for k in req.keywords if k.strip()][:20]
    platforms = [p for p in req.platforms if p in PLATFORMS]
    if not platforms:
        platforms = list(PLATFORMS)
    if not questions and not keywords:
        raise HTTPException(status_code=400, detail="Ajoutez au moins une question ou un mot-clé")

    import hashlib
    from datetime import datetime
    key = hashlib.md5(f"{domain}-{datetime.now().timestamp()}".encode()).hexdigest()[:12]
    total = len(questions) * len(platforms) + len(keywords)
    _ai_vis_runs[key] = {"status": "running", "done": 0, "total": total,
                         "llm_results": [], "aio_results": [], "error": ""}

    def run():
        from app.services.ai_visibility import AIVisibilityTester
        state = _ai_vis_runs[key]
        tester = None
        try:
            tester = AIVisibilityTester()
            for q in questions:
                for p in platforms:
                    state["llm_results"].append(tester.ask_llm(p, q, domain, req.brand))
                    state["done"] += 1
            for kw in keywords:
                state["aio_results"].append(
                    tester.check_ai_overview(kw, domain, req.location, req.language))
                state["done"] += 1
            # Synthèse par plateforme
            summary = {}
            for r in state["llm_results"]:
                s = summary.setdefault(r["platform"], {
                    "label": r["label"], "asked": 0, "mentioned": 0, "cited": 0, "errors": 0})
                s["asked"] += 1
                s["mentioned"] += 1 if r["mentioned"] else 0
                s["cited"] += 1 if r["cited"] else 0
                s["errors"] += 1 if r["error"] else 0
            aio_present = sum(1 for r in state["aio_results"] if r["has_aio"])
            aio_cited = sum(1 for r in state["aio_results"] if r["cited"])
            state["summary"] = {
                "platforms": summary,
                "aio": {"checked": len(state["aio_results"]),
                        "present": aio_present, "cited": aio_cited},
            }
            state["status"] = "done"
        except Exception as exc:
            state["status"] = "error"
            state["error"] = str(exc)
        finally:
            if tester:
                tester.close()

    background_tasks.add_task(run)
    return {"key": key, "total": total}


@router.get("/ai-visibility/status/{key}")
def ai_visibility_status(key: str):
    run = _ai_vis_runs.get(key)
    if not run:
        raise HTTPException(status_code=404, detail="Test introuvable")
    return run
