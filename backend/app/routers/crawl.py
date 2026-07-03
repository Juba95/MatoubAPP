"""
Routeur Crawl & Maillage interne — mini Screaming Frog (gratuit).

Lance un crawl en tâche de fond, expose la progression, le rapport (pages +
maillage) et l'export Excel.
"""
import io
import logging
import traceback
from datetime import datetime

from fastapi import APIRouter, Depends, BackgroundTasks, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.auth import get_current_user
from app.services.crawler import SiteCrawler, build_crawl_excel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/crawl", tags=["crawl"], dependencies=[Depends(get_current_user)])

# Résultats de crawl en mémoire (comme les autres modules)
_crawls: dict[str, dict] = {}


class CrawlRequest(BaseModel):
    url: str
    max_pages: int = 300
    include_sitemap: bool = True
    sitemap_url: str = ""


@router.post("/start")
def start_crawl(req: CrawlRequest, background_tasks: BackgroundTasks):
    """Démarre un crawl en tâche de fond."""
    if not req.url.strip():
        raise HTTPException(status_code=400, detail="URL requise")

    import hashlib
    key = hashlib.md5(f"{req.url}-{datetime.now().timestamp()}".encode()).hexdigest()[:12]
    _crawls[key] = {"status": "running", "done": 0, "total": 0, "report": None, "error": ""}

    def run():
        state = _crawls[key]

        def progress(done, total):
            state["done"] = done
            state["total"] = max(total, done)

        try:
            crawler = SiteCrawler(
                start_url=req.url,
                max_pages=req.max_pages,
                include_sitemap=req.include_sitemap,
                sitemap_url=req.sitemap_url,
                progress=progress,
            )
            report = crawler.run()
            _crawls[key] = {
                "status": "done",
                "done": report["summary"]["total_pages"],
                "total": report["summary"]["total_pages"],
                "report": report,
                "error": "",
            }
        except Exception as exc:
            logger.error("crawl error: %s", traceback.format_exc())
            _crawls[key] = {"status": "error", "done": state.get("done", 0),
                            "total": state.get("total", 0), "report": None, "error": str(exc)}

    background_tasks.add_task(run)
    return {"key": key, "message": f"Crawl démarré ({req.url})"}


@router.get("/status/{key}")
def crawl_status(key: str):
    c = _crawls.get(key)
    if not c:
        raise HTTPException(status_code=404, detail="Crawl introuvable")
    return {"status": c["status"], "done": c["done"], "total": c["total"], "error": c["error"]}


@router.get("/result/{key}")
def crawl_result(key: str):
    """Rapport complet : résumé + pages (sans les liens bruts) + maillage."""
    c = _crawls.get(key)
    if not c or c["status"] != "done" or not c["report"]:
        raise HTTPException(status_code=404, detail="Rapport non disponible")
    report = c["report"]
    # Allège les pages pour le transport (retire les listes de liens volumineuses)
    light_pages = []
    for p in report["pages"]:
        lp = {k: v for k, v in p.items() if k not in ("internal_links", "anchors_out", "hn")}
        light_pages.append(lp)
    return {"summary": report["summary"], "pages": light_pages, "maillage": report["maillage"]}


@router.get("/page/{key}")
def crawl_page_detail(key: str, url: str):
    """Détail d'une page (Hn + liens sortants) pour la vue détaillée."""
    c = _crawls.get(key)
    if not c or c["status"] != "done" or not c["report"]:
        raise HTTPException(status_code=404, detail="Rapport non disponible")
    page = next((p for p in c["report"]["pages"] if p["url"] == url), None)
    if not page:
        raise HTTPException(status_code=404, detail="Page introuvable dans ce crawl")
    return {
        "url": page["url"], "hn": page.get("hn", []),
        "anchors_out": page.get("anchors_out", []),
        "internal_out": page.get("internal_out", 0),
        "external_links": page.get("external_links", 0),
    }


@router.get("/download/{key}")
def crawl_download(key: str):
    c = _crawls.get(key)
    if not c or c["status"] != "done" or not c["report"]:
        raise HTTPException(status_code=404, detail="Rapport non disponible")
    data = build_crawl_excel(c["report"])
    host = c["report"]["summary"]["start_url"].replace("https://", "").replace("http://", "").rstrip("/")
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="crawl_{host}.xlsx"'},
    )
