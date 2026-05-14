import csv
import os
import io
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
from app.database import get_db
from app.auth import get_current_user
from app.models.action import Action, ActionType, ActionStatus
from app.models.site import Site

router = APIRouter(prefix="/geoloc", tags=["geoloc"], dependencies=[Depends(get_current_user)])

VILLES_CSV = os.path.join(os.path.dirname(__file__), "..", "..", "villes_france.csv")

# Mapping département → région
DEPT_TO_REGION = {
    "01": "Auvergne-Rhône-Alpes", "03": "Auvergne-Rhône-Alpes", "07": "Auvergne-Rhône-Alpes",
    "15": "Auvergne-Rhône-Alpes", "26": "Auvergne-Rhône-Alpes", "38": "Auvergne-Rhône-Alpes",
    "42": "Auvergne-Rhône-Alpes", "43": "Auvergne-Rhône-Alpes", "63": "Auvergne-Rhône-Alpes",
    "69": "Auvergne-Rhône-Alpes", "73": "Auvergne-Rhône-Alpes", "74": "Auvergne-Rhône-Alpes",
    "21": "Bourgogne-Franche-Comté", "25": "Bourgogne-Franche-Comté", "39": "Bourgogne-Franche-Comté",
    "58": "Bourgogne-Franche-Comté", "70": "Bourgogne-Franche-Comté", "71": "Bourgogne-Franche-Comté",
    "89": "Bourgogne-Franche-Comté", "90": "Bourgogne-Franche-Comté",
    "22": "Bretagne", "29": "Bretagne", "35": "Bretagne", "56": "Bretagne",
    "18": "Centre-Val de Loire", "28": "Centre-Val de Loire", "36": "Centre-Val de Loire",
    "37": "Centre-Val de Loire", "41": "Centre-Val de Loire", "45": "Centre-Val de Loire",
    "2A": "Corse", "2B": "Corse",
    "08": "Grand Est", "10": "Grand Est", "51": "Grand Est", "52": "Grand Est",
    "54": "Grand Est", "55": "Grand Est", "57": "Grand Est", "67": "Grand Est", "68": "Grand Est", "88": "Grand Est",
    "02": "Hauts-de-France", "59": "Hauts-de-France", "60": "Hauts-de-France",
    "62": "Hauts-de-France", "80": "Hauts-de-France",
    "75": "Île-de-France", "77": "Île-de-France", "78": "Île-de-France", "91": "Île-de-France",
    "92": "Île-de-France", "93": "Île-de-France", "94": "Île-de-France", "95": "Île-de-France",
    "14": "Normandie", "27": "Normandie", "50": "Normandie", "61": "Normandie", "76": "Normandie",
    "16": "Nouvelle-Aquitaine", "17": "Nouvelle-Aquitaine", "19": "Nouvelle-Aquitaine",
    "23": "Nouvelle-Aquitaine", "24": "Nouvelle-Aquitaine", "33": "Nouvelle-Aquitaine",
    "40": "Nouvelle-Aquitaine", "47": "Nouvelle-Aquitaine", "64": "Nouvelle-Aquitaine",
    "79": "Nouvelle-Aquitaine", "86": "Nouvelle-Aquitaine", "87": "Nouvelle-Aquitaine",
    "09": "Occitanie", "11": "Occitanie", "12": "Occitanie", "30": "Occitanie",
    "31": "Occitanie", "32": "Occitanie", "34": "Occitanie", "46": "Occitanie",
    "48": "Occitanie", "65": "Occitanie", "66": "Occitanie", "81": "Occitanie", "82": "Occitanie",
    "44": "Pays de la Loire", "49": "Pays de la Loire", "53": "Pays de la Loire",
    "72": "Pays de la Loire", "85": "Pays de la Loire",
    "04": "Provence-Alpes-Côte d'Azur", "05": "Provence-Alpes-Côte d'Azur",
    "06": "Provence-Alpes-Côte d'Azur", "13": "Provence-Alpes-Côte d'Azur",
    "83": "Provence-Alpes-Côte d'Azur", "84": "Provence-Alpes-Côte d'Azur",
}


def load_villes(
    departments: list[str] | None = None,
    regions: list[str] | None = None,
    min_population: int = 0,
):
    """Charge les villes depuis le CSV avec filtres"""
    villes = []
    with open(VILLES_CSV, encoding="utf-8") as f:
        reader = csv.reader(f)
        for row in reader:
            try:
                dept = row[1]
                name = row[5]
                slug = row[2]
                postal = row[8].split("-")[0] if "-" in row[8] else row[8]
                pop = int(row[14])
                lng = float(row[19])
                lat = float(row[20])
                region = DEPT_TO_REGION.get(dept, "")

                if min_population and pop < min_population:
                    continue
                if departments and dept not in departments:
                    continue
                if regions and region not in regions:
                    continue

                villes.append({
                    "name": name,
                    "slug": slug,
                    "department": dept,
                    "postal_code": postal,
                    "population": pop,
                    "region": region,
                    "lat": lat,
                    "lng": lng,
                })
            except (IndexError, ValueError):
                continue
    return sorted(villes, key=lambda v: v["population"], reverse=True)


@router.get("/regions")
def list_regions():
    """Liste des régions disponibles"""
    regions = sorted(set(DEPT_TO_REGION.values()))
    return regions


@router.get("/departments")
def list_departments(region: Optional[str] = Query(None)):
    """Liste des départements, filtrable par région"""
    if region:
        depts = [d for d, r in DEPT_TO_REGION.items() if r == region]
    else:
        depts = list(DEPT_TO_REGION.keys())
    return sorted(depts)


@router.get("/villes")
def list_villes(
    departments: Optional[str] = Query(None, description="Départements séparés par virgule"),
    regions: Optional[str] = Query(None, description="Régions séparées par virgule"),
    min_population: int = Query(0),
    limit: int = Query(100, le=500),
):
    """Liste des villes avec filtres"""
    dept_list = [d.strip() for d in departments.split(",") if d.strip()] if departments else None
    region_list = [r.strip() for r in regions.split(",") if r.strip()] if regions else None
    villes = load_villes(dept_list, region_list, min_population)
    return {
        "total": len(villes),
        "villes": villes[:limit],
    }


class GeolocRequest(BaseModel):
    site_id: int
    keyword_template: str  # ex: "serrurier {ville}"
    departments: list[str] | None = None
    regions: list[str] | None = None
    min_population: int = 5000


@router.post("/preview")
def preview_geoloc(req: GeolocRequest, db: Session = Depends(get_db)):
    """Preview des pages qui seront générées — sans rien consommer"""
    site = db.query(Site).filter(Site.id == req.site_id).first()
    if not site:
        raise HTTPException(status_code=404, detail="Site not found")

    villes = load_villes(req.departments, req.regions, req.min_population)
    pages = []
    for v in villes:
        if "{ville}" in req.keyword_template:
            keyword = req.keyword_template.replace("{ville}", v["name"])
            slug = req.keyword_template.replace("{ville}", v["slug"]).replace(" ", "-").lower()
        else:
            keyword = f"{req.keyword_template} {v['name']}"
            slug = f"{req.keyword_template.replace(' ', '-').lower()}-{v['slug']}"
        pages.append({
            "keyword": keyword,
            "slug": slug,
            "city": v["name"],
            "department": v["department"],
            "postal_code": v["postal_code"],
            "region": v["region"],
            "population": v["population"],
        })

    estimated_cost = len(pages) * 0.03  # ~0.03€ par page via Claude API
    return {
        "site": site.domain,
        "total_pages": len(pages),
        "estimated_cost_eur": round(estimated_cost, 2),
        "pages": pages[:20],  # preview des 20 premières
        "keyword_template": req.keyword_template,
    }


@router.post("/generate")
def generate_geoloc(req: GeolocRequest, db: Session = Depends(get_db)):
    """Créer les actions de génération géoloc dans la file de validation"""
    site = db.query(Site).filter(Site.id == req.site_id).first()
    if not site:
        raise HTTPException(status_code=404, detail="Site not found")

    villes = load_villes(req.departments, req.regions, req.min_population)
    created = 0
    for v in villes:
        if "{ville}" in req.keyword_template:
            keyword = req.keyword_template.replace("{ville}", v["name"])
        else:
            keyword = f"{req.keyword_template} {v['name']}"
        action = Action(
            site_id=site.id,
            action_type=ActionType.GEOLOC,
            status=ActionStatus.PENDING,
            title=f"{site.name} — {keyword}",
            keyword=keyword,
            description=f"Page géolocalisée pour {v['name']} ({v['department']}) - {v['population']} hab.",
            impact_score=v["population"] / 100,
            estimated_api_cost=0.03,
            extra_data={
                "city": v["name"],
                "department": v["department"],
                "postal_code": v["postal_code"],
                "region": v["region"],
                "population": v["population"],
                "lat": v["lat"],
                "lng": v["lng"],
            },
        )
        db.add(action)
        created += 1

    db.commit()
    return {
        "message": f"{created} actions géoloc créées dans la file de validation",
        "total": created,
        "estimated_total_cost": round(created * 0.03, 2),
    }


class GeolocFileRequest(BaseModel):
    keyword_template: str         # ex: "serrurier" ou "serrurier {ville}"
    site_name: str                # ex: "L'epaviste Pro"
    site_domain: str              # ex: "lepaviste-pro.fr"
    post_author: str = "admin"
    post_category: str = ""
    post_status: str = "draft"
    post_thumbnail: str = ""
    departments: list[str] | None = None
    regions: list[str] | None = None
    min_population: int = 5000
    use_divi: bool = False
    generate_content: bool = False  # Si True, génère le contenu via Claude (lent + coûteux)


# Cache des fichiers générés
_generated_files: dict[str, dict] = {}


@router.post("/generate-file")
def generate_file(req: GeolocFileRequest, background_tasks: BackgroundTasks):
    """Génère un fichier Excel d'import WP avec toutes les pages géolocalisées."""
    villes = load_villes(req.departments, req.regions, req.min_population)
    file_key = f"{req.site_domain}_{req.keyword_template}_{len(villes)}"
    _generated_files[file_key] = {"status": "running", "total": len(villes), "done": 0}

    def run():
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Import WP"

        # Colonnes
        headers = [
            "Ville actuelle", "SLUG", "post_title", "H1", "post_description",
            "post_content", "post_thumbnail", "post_date", "post_author",
            "post_category", "post_tag", "post_status", "Population", "Type",
        ]
        if req.use_divi:
            headers.extend(["_et_pb_use_builder", "_et_pb_old_content"])
        ws.append(headers)

        today = datetime.now().strftime("%Y-%m-%d")
        claude = None
        site_obj = None

        if req.generate_content:
            try:
                from app.services.claude_content import ClaudeContentService
                from app.models.site import Site as SiteModel
                claude = ClaudeContentService()
                # Créer un objet Site minimal pour Claude
                site_obj = type("Site", (), {
                    "niche": req.post_category or req.keyword_template,
                    "editorial_tone": "conversationnel",
                    "editorial_style": "",
                    "avg_article_length": 1200,
                })()
            except Exception:
                claude = None

        for i, v in enumerate(villes):
            # Keyword avec ville
            if "{ville}" in req.keyword_template:
                kw = req.keyword_template.replace("{ville}", v["name"])
                slug_kw = req.keyword_template.replace("{ville}", v["slug"]).replace(" ", "-").lower()
            else:
                kw = f"{req.keyword_template} {v['name']}"
                slug_kw = f"{req.keyword_template.replace(' ', '-').lower()}-{v['slug']}"

            title = f"{kw.title()} | {req.site_name}"
            h1 = kw.title()
            meta_desc = f"Expert en {req.keyword_template} à {v['name']} ({v['department']}). Intervention rapide dans le {v['department']}. Devis gratuit."
            tags = f"{req.keyword_template},{v['name'].lower()},{v['department']}"

            # Contenu
            content = ""
            if claude and site_obj and req.generate_content:
                try:
                    result = claude.generate_geoloc_article(
                        site_obj, keyword=kw, city=v["name"],
                        department=v["department"], postal_code=v["postal_code"],
                        region=v["region"],
                    )
                    import json, re
                    raw = result["raw"]
                    try:
                        match = re.search(r'```(?:json)?\s*(\{[\s\S]*?\})\s*```', raw)
                        if match:
                            data = json.loads(match.group(1))
                        else:
                            depth = 0
                            start = None
                            data = {}
                            for ci, c in enumerate(raw):
                                if c == '{':
                                    if depth == 0: start = ci
                                    depth += 1
                                elif c == '}':
                                    depth -= 1
                                    if depth == 0 and start is not None:
                                        try:
                                            data = json.loads(raw[start:ci+1])
                                            break
                                        except json.JSONDecodeError:
                                            start = None
                        content = data.get("content", raw)
                        if data.get("meta_description"):
                            meta_desc = data["meta_description"]
                        if data.get("title"):
                            title = data["title"]
                        if data.get("slug"):
                            slug_kw = data["slug"]
                    except Exception:
                        content = raw
                except Exception:
                    content = f"<h2>{h1}</h2><p>Contenu à rédiger pour {v['name']}.</p>"
            else:
                content = f"<h2>{h1}</h2><p>Contenu à rédiger pour {v['name']} ({v['department']}).</p>"

            # Divi wrapper
            if req.use_divi:
                divi_content = (
                    f"[et_pb_section _builder_version='4.27.0' background_color='#FFFFFF']"
                    f"[et_pb_row _builder_version='4.27.0'][et_pb_column type='4_4' _builder_version='4.27.0']"
                    f"[et_pb_text _builder_version='4.27.0']{content}[/et_pb_text]"
                    f"[/et_pb_column][/et_pb_row][/et_pb_section]"
                )
                row = [
                    v["name"], slug_kw, title, h1, meta_desc,
                    divi_content, req.post_thumbnail, today, req.post_author,
                    req.post_category, tags, req.post_status,
                    v["population"], "Ville", "on", "",
                ]
            else:
                row = [
                    v["name"], slug_kw, title, h1, meta_desc,
                    content, req.post_thumbnail, today, req.post_author,
                    req.post_category, tags, req.post_status,
                    v["population"], "Ville",
                ]

            ws.append(row)
            _generated_files[file_key]["done"] = i + 1

        # Sauvegarder en mémoire
        buffer = io.BytesIO()
        wb.save(buffer)
        buffer.seek(0)
        _generated_files[file_key] = {
            "status": "done",
            "total": len(villes),
            "done": len(villes),
            "data": buffer.getvalue(),
            "filename": f"import_{req.site_domain}_{len(villes)}_pages.xlsx",
        }

    background_tasks.add_task(run)
    return {"message": f"Génération en cours ({len(villes)} pages)", "file_key": file_key, "total": len(villes)}


@router.get("/file-status/{file_key}")
def file_status(file_key: str):
    """Vérifie le statut de la génération du fichier."""
    data = _generated_files.get(file_key)
    if not data:
        raise HTTPException(status_code=404, detail="Fichier non trouvé")
    return {"status": data["status"], "total": data.get("total", 0), "done": data.get("done", 0)}


@router.get("/download/{file_key}")
def download_file(file_key: str):
    """Télécharge le fichier Excel généré."""
    data = _generated_files.get(file_key)
    if not data or data["status"] != "done":
        raise HTTPException(status_code=404, detail="Fichier pas encore prêt")
    buffer = io.BytesIO(data["data"])
    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{data["filename"]}"'},
    )
