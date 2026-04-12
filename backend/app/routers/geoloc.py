import csv
import os
from fastapi import APIRouter, Depends, HTTPException, Query
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
    dept_list = departments.split(",") if departments else None
    region_list = regions.split(",") if regions else None
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
        keyword = req.keyword_template.replace("{ville}", v["name"])
        slug = req.keyword_template.replace("{ville}", v["slug"]).replace(" ", "-").lower()
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
        keyword = req.keyword_template.replace("{ville}", v["name"])
        action = Action(
            site_id=site.id,
            action_type=ActionType.GEOLOC,
            status=ActionStatus.PENDING,
            title=f"{site.domain} — {keyword}",
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
