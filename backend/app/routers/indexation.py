"""
Module Indexation — pousser les URLs vers les moteurs et suivre leur indexation.

- Clé IndexNow par site (déterministe), avec upload FTP du fichier de clé
- Soumission d'URLs par lots à IndexNow (Bing, Yandex, Seznam... instantané)
- Vérification d'indexation Google via l'URL Inspection API de la Search Console
"""
import hashlib
import io
import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.config import get_settings
from app.database import get_db
from app.models.site import Site

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/indexation", tags=["indexation"], dependencies=[Depends(get_current_user)])

INDEXNOW_ENDPOINT = "https://api.indexnow.org/indexnow"


def _clean_host(domain: str) -> str:
    return domain.replace("https://", "").replace("http://", "").replace("www.", "").rstrip("/")


def indexnow_key_for(domain: str) -> str:
    """Clé IndexNow déterministe par domaine (stable entre redémarrages)."""
    secret = get_settings().jwt_secret
    return hashlib.md5(f"indexnow:{_clean_host(domain)}:{secret}".encode()).hexdigest()


def _get_site(db: Session, site_id: int) -> Site:
    site = db.query(Site).filter(Site.id == site_id).first()
    if not site:
        raise HTTPException(status_code=404, detail="Site not found")
    return site


@router.get("/key/{site_id}")
def get_indexnow_key(site_id: int, db: Session = Depends(get_db)):
    """Clé IndexNow du site + emplacement attendu du fichier de clé."""
    site = _get_site(db, site_id)
    host = _clean_host(site.domain)
    key = indexnow_key_for(site.domain)
    return {
        "host": host,
        "key": key,
        "key_file_url": f"https://{host}/{key}.txt",
        "ftp_configured": bool(site.ftp_host and site.ftp_user and site.ftp_password),
        "instructions": (
            f"Le fichier {key}.txt (contenant uniquement la clé) doit être accessible "
            f"à https://{host}/{key}.txt — utilise le bouton d'upload FTP ou dépose-le manuellement."
        ),
    }


@router.post("/key-upload/{site_id}")
def upload_indexnow_key(site_id: int, db: Session = Depends(get_db)):
    """Upload le fichier de clé IndexNow à la racine du site via ses accès FTP."""
    import ftplib

    site = _get_site(db, site_id)
    if not (site.ftp_host and site.ftp_user and site.ftp_password):
        raise HTTPException(status_code=400, detail="Accès FTP non configurés pour ce site")

    key = indexnow_key_for(site.domain)
    try:
        ftp = ftplib.FTP(site.ftp_host, timeout=30)
        ftp.login(site.ftp_user, site.ftp_password)
        if site.ftp_path and site.ftp_path != "/":
            ftp.cwd(site.ftp_path)
        ftp.storbinary(f"STOR {key}.txt", io.BytesIO(key.encode()))
        ftp.quit()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Upload FTP impossible : {exc}")

    return {"message": "Clé IndexNow uploadée", "key_file_url": f"https://{_clean_host(site.domain)}/{key}.txt"}


class SubmitRequest(BaseModel):
    site_id: int
    urls: list[str]


@router.post("/submit")
def submit_indexnow(req: SubmitRequest, db: Session = Depends(get_db)):
    """Soumet un lot d'URLs à IndexNow (max 10 000 par appel)."""
    site = _get_site(db, req.site_id)
    urls = [u.strip() for u in req.urls if u.strip().startswith("http")][:10000]
    if not urls:
        raise HTTPException(status_code=400, detail="Aucune URL valide (elles doivent commencer par http)")

    host = _clean_host(site.domain)
    key = indexnow_key_for(site.domain)
    payload = {
        "host": host,
        "key": key,
        "keyLocation": f"https://{host}/{key}.txt",
        "urlList": urls,
    }
    try:
        resp = httpx.post(INDEXNOW_ENDPOINT, json=payload, timeout=30)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"IndexNow injoignable : {exc}")

    # 200/202 = accepté ; 403 = fichier de clé absent/incorrect ; 422 = URLs hors host
    if resp.status_code in (200, 202):
        return {"message": f"{len(urls)} URLs soumises à IndexNow", "submitted": len(urls), "status_code": resp.status_code}
    detail = {
        403: "Clé refusée — le fichier de clé n'est pas accessible sur le site (utilise l'upload FTP)",
        422: "URLs refusées — elles n'appartiennent pas au domaine déclaré",
        429: "Trop de requêtes — réessaie plus tard",
    }.get(resp.status_code, resp.text[:200])
    raise HTTPException(status_code=502, detail=f"IndexNow {resp.status_code} : {detail}")


def _url_language(url: str, known: set[str]) -> str:
    """Déduit la langue d'une URL depuis son premier segment de chemin.

    Google indexe chaque version linguistique indépendamment ; on regroupe donc
    par préfixe de langue (/en/, /de/…). Retourne le code ou '' (langue par défaut,
    sans préfixe)."""
    from urllib.parse import urlparse
    from app.services.i18n import normalize
    seg = urlparse(url).path.strip("/").split("/")
    first = seg[0].lower() if seg and seg[0] else ""
    if len(first) == 2 and first.isalpha():
        code = normalize(first)
        if not known or code in known:
            return code
    return ""


def _language_breakdown(results: list[dict]) -> list[dict]:
    """Taux d'indexation par langue (préfixe d'URL)."""
    from collections import defaultdict
    from app.services.i18n import lang_name
    known = {r.get("language", "") for r in results if r.get("language")}
    groups: dict[str, list] = defaultdict(list)
    for r in results:
        lg = r.get("language") or _url_language(r["url"], known)
        groups[lg].append(r)
    out = []
    for lg, items in groups.items():
        idx = sum(1 for r in items if r.get("indexed"))
        code = lg or "default"
        label = "Langue principale (sans préfixe)" if not lg else lang_name(lg, "native")
        out.append({
            "language": code,
            "label": label,
            "total": len(items),
            "indexed": idx,
            "not_indexed": len(items) - idx,
            "rate": round(idx / len(items) * 100) if items else 0,
        })
    return sorted(out, key=lambda x: -x["total"])


class CheckRequest(BaseModel):
    site_id: int
    urls: list[str]


@router.post("/check")
def check_indexation(req: CheckRequest, db: Session = Depends(get_db)):
    """Vérifie l'indexation Google d'un lot d'URLs (URL Inspection API GSC).

    Limité à 50 URLs par appel pour rester loin du quota (~2000/jour).
    Retourne un taux d'indexation global ET par langue (Google indexe chaque
    version linguistique indépendamment)."""
    from app.services.search_console import SearchConsoleClient

    site = _get_site(db, req.site_id)
    if not site.sc_token_json:
        raise HTTPException(status_code=400, detail="Search Console non connectée pour ce site")

    urls = [u.strip() for u in req.urls if u.strip().startswith("http")][:50]
    if not urls:
        raise HTTPException(status_code=400, detail="Aucune URL valide")

    sc = SearchConsoleClient(site)
    results = []
    errors = 0
    for url in urls:
        try:
            results.append(sc.inspect_url(url))
        except Exception as exc:
            errors += 1
            results.append({"url": url, "verdict": "ERROR", "coverage_state": str(exc)[:150],
                            "last_crawl": "", "indexed": False})
            if errors >= 3 and errors == len(results):
                # Trois échecs d'affilée dès le début : inutile de brûler le quota
                raise HTTPException(status_code=502, detail=f"Inspection GSC en échec : {exc}")

    indexed = sum(1 for r in results if r["indexed"])
    total = len(results)
    buckets = _classify(results)
    return {
        "total": total,
        "indexed": indexed,
        "not_indexed": total - indexed,
        "indexation_rate": round(indexed / total * 100) if total else 0,
        "by_language": _language_breakdown(results),
        "to_enrich": buckets["to_enrich"],
        "to_resubmit": buckets["to_resubmit"],
        "to_prune": buckets["to_prune"],
        "recommendation": buckets["recommendation"],
        "results": results,
    }


def _classify(results: list[dict]) -> dict:
    """Classe les URLs non indexées en actions concrètes.

    - to_resubmit : découverte mais pas encore explorée → re-soumettre (IndexNow)
    - to_enrich   : explorée/détectée mais jugée sans valeur → enrichir le contenu
    - to_prune    : erreur/exclue durable → envisager la suppression
    """
    to_enrich, to_resubmit, to_prune = [], [], []
    for r in results:
        if r.get("indexed"):
            continue
        state = (r.get("coverage_state") or "").lower()
        url = r["url"]
        # 1. Erreur durable → élaguer (testé en premier)
        if r.get("verdict") in ("ERROR", "FAIL") or "error" in state or "not found" in state or "404" in state:
            to_prune.append(url)
        # 2. Crawlée/détectée mais non indexée → Google juge la page faible → enrichir
        elif "explor" in state or "crawled" in state or "détect" in state or "detected" in state or "duplicate" in state or "soft 404" in state:
            to_enrich.append(url)
        # 3. Découverte mais pas encore explorée → re-soumettre
        elif "découverte" in state or "discovered" in state or not state:
            to_resubmit.append(url)
        # 4. Inconnu → enrichir par prudence
        else:
            to_enrich.append(url)

    if to_enrich and len(to_enrich) >= max(1, len(results) // 3):
        rec = ("Beaucoup de pages sont crawlées mais non indexées : c'est le signal "
               "« contenu à faible valeur ». Enrichis-les (données locales réelles, "
               "profondeur) ou élague les plus faibles — c'est le levier n°1.")
    elif to_resubmit:
        rec = "Des pages sont découvertes mais pas encore explorées : re-soumets-les via IndexNow et patiente."
    elif to_prune:
        rec = "Des pages sont en erreur : vérifie qu'elles répondent en 200, sinon supprime-les."
    else:
        rec = "Indexation saine."
    return {"to_enrich": to_enrich, "to_resubmit": to_resubmit, "to_prune": to_prune, "recommendation": rec}
