import csv
import os
import io
import re
import logging
import traceback
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
from app.services.geoloc_engine import (
    GeolocEngine,
    slugify,
    build_jsonld,
    parse_faq,
    layout_seed_from,
    replace_ville,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/geoloc", tags=["geoloc"], dependencies=[Depends(get_current_user)])

# Utiliser le CSV premium s'il existe, sinon fallback sur l'ancien
_candidates = [
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "data", "villes_france_premium.csv"),  # dev local
    os.path.join(os.path.dirname(__file__), "..", "..", "data", "villes_france_premium.csv"),        # Docker /app/data/
    "/app/data/villes_france_premium.csv",                                                           # Docker absolu
]
_csv_legacy = os.path.join(os.path.dirname(__file__), "..", "..", "villes_france.csv")
VILLES_CSV = next((p for p in _candidates if os.path.exists(p)), _csv_legacy)

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


# Nom officiel de chaque département (pour les pages de type « Département »)
DEPT_NAMES = {
    "01": "Ain", "02": "Aisne", "03": "Allier", "04": "Alpes-de-Haute-Provence",
    "05": "Hautes-Alpes", "06": "Alpes-Maritimes", "07": "Ardèche", "08": "Ardennes",
    "09": "Ariège", "10": "Aube", "11": "Aude", "12": "Aveyron",
    "13": "Bouches-du-Rhône", "14": "Calvados", "15": "Cantal", "16": "Charente",
    "17": "Charente-Maritime", "18": "Cher", "19": "Corrèze", "2A": "Corse-du-Sud",
    "2B": "Haute-Corse", "21": "Côte-d'Or", "22": "Côtes-d'Armor", "23": "Creuse",
    "24": "Dordogne", "25": "Doubs", "26": "Drôme", "27": "Eure",
    "28": "Eure-et-Loir", "29": "Finistère", "30": "Gard", "31": "Haute-Garonne",
    "32": "Gers", "33": "Gironde", "34": "Hérault", "35": "Ille-et-Vilaine",
    "36": "Indre", "37": "Indre-et-Loire", "38": "Isère", "39": "Jura",
    "40": "Landes", "41": "Loir-et-Cher", "42": "Loire", "43": "Haute-Loire",
    "44": "Loire-Atlantique", "45": "Loiret", "46": "Lot", "47": "Lot-et-Garonne",
    "48": "Lozère", "49": "Maine-et-Loire", "50": "Manche", "51": "Marne",
    "52": "Haute-Marne", "53": "Mayenne", "54": "Meurthe-et-Moselle", "55": "Meuse",
    "56": "Morbihan", "57": "Moselle", "58": "Nièvre", "59": "Nord",
    "60": "Oise", "61": "Orne", "62": "Pas-de-Calais", "63": "Puy-de-Dôme",
    "64": "Pyrénées-Atlantiques", "65": "Hautes-Pyrénées", "66": "Pyrénées-Orientales",
    "67": "Bas-Rhin", "68": "Haut-Rhin", "69": "Rhône", "70": "Haute-Saône",
    "71": "Saône-et-Loire", "72": "Sarthe", "73": "Savoie", "74": "Haute-Savoie",
    "75": "Paris", "76": "Seine-Maritime", "77": "Seine-et-Marne", "78": "Yvelines",
    "79": "Deux-Sèvres", "80": "Somme", "81": "Tarn", "82": "Tarn-et-Garonne",
    "83": "Var", "84": "Vaucluse", "85": "Vendée", "86": "Vienne",
    "87": "Haute-Vienne", "88": "Vosges", "89": "Yonne", "90": "Territoire de Belfort",
    "91": "Essonne", "92": "Hauts-de-Seine", "93": "Seine-Saint-Denis",
    "94": "Val-de-Marne", "95": "Val-d'Oise",
}

# Limite Excel : 32 767 caractères par cellule. Au-delà, le fichier de sortie
# serait tronqué brutalement par Excel (shortcodes Divi cassés).
EXCEL_CELL_LIMIT = 32767


def safe_cell(text: str) -> str:
    """Tronque proprement un contenu à la limite d'une cellule Excel."""
    if not isinstance(text, str) or len(text) <= EXCEL_CELL_LIMIT:
        return text
    cut = text[:EXCEL_CELL_LIMIT - 20]
    # Couper à la fin du dernier shortcode/balise complet pour ne pas casser le rendu
    boundary = max(cut.rfind("]"), cut.rfind(">"))
    if boundary > 0:
        cut = cut[:boundary + 1]
    logger.warning("Contenu tronqué à la limite Excel (%d caractères)", EXCEL_CELL_LIMIT)
    return cut


YOAST_HEADERS = ["_yoast_wpseo_title", "_yoast_wpseo_metadesc", "_yoast_wpseo_focuskw"]


def build_sitemaps_zip(site_domain: str, url_entries: list[dict]) -> bytes:
    """Construit un zip : sitemap index + un sitemap par département + urls.txt.

    Chaque entrée : {"loc": url, "lastmod": "YYYY-MM-DD", "dept": "69"}.
    Les lastmod suivent les dates de publication étalées : Google découvre
    le site par tranches, aligné sur le rythme de mise en ligne.
    """
    import zipfile

    base = f"https://{site_domain.replace('https://', '').replace('http://', '').rstrip('/')}"
    by_dept: dict[str, list[dict]] = {}
    for e in url_entries:
        by_dept.setdefault(e.get("dept") or "autres", []).append(e)

    def urlset(entries: list[dict]) -> str:
        lines = ['<?xml version="1.0" encoding="UTF-8"?>',
                 '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
        for e in entries:
            lines.append(
                f"  <url><loc>{e['loc']}</loc><lastmod>{e['lastmod']}</lastmod>"
                f"<changefreq>monthly</changefreq></url>"
            )
        lines.append("</urlset>")
        return "\n".join(lines)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        index_lines = ['<?xml version="1.0" encoding="UTF-8"?>',
                       '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
        for dept, entries in sorted(by_dept.items()):
            fname = f"sitemap-geoloc-{slugify(dept)}.xml"
            zf.writestr(fname, urlset(entries))
            last = max(e["lastmod"] for e in entries)
            index_lines.append(
                f"  <sitemap><loc>{base}/{fname}</loc><lastmod>{last}</lastmod></sitemap>"
            )
        index_lines.append("</sitemapindex>")
        zf.writestr("sitemap-geoloc.xml", "\n".join(index_lines))
        zf.writestr("urls.txt", "\n".join(e["loc"] for e in url_entries))
        zf.writestr("LISEZMOI.txt", (
            "SITEMAPS GÉOLOC — mode d'emploi\n"
            "================================\n"
            f"1. Uploade tous les fichiers .xml à la racine du site ({base}/)\n"
            "2. Déclare sitemap-geoloc.xml dans Google Search Console (Sitemaps)\n"
            "3. Ajoute la ligne suivante à ton robots.txt :\n"
            f"   Sitemap: {base}/sitemap-geoloc.xml\n"
            "4. urls.txt : liste brute des URLs, à coller dans l'onglet Indexation\n"
            "   de MatoubAPP pour la soumission IndexNow et le suivi GSC.\n"
            "Les lastmod suivent les dates de publication étalées de l'export.\n"
        ))
    return buf.getvalue()


_WORD_RE = re.compile(r"[a-zà-ÿ0-9]+")


def _shingles(text: str, n: int = 5) -> set:
    words = _WORD_RE.findall(text.lower())
    return {" ".join(words[i:i + n]) for i in range(max(0, len(words) - n + 1))}


def strip_markup(content: str) -> str:
    """Texte brut d'un contenu (retire shortcodes Divi, balises, scripts JSON-LD)."""
    content = re.sub(r"<script[^>]*>.*?</script>", " ", content, flags=re.DOTALL)
    content = re.sub(r"\[[^\]]*\]", " ", content)
    content = re.sub(r"<[^>]+>", " ", content)
    return content


class UniquenessTracker:
    """Score d'unicité du contenu entre pages partageant la même variante.

    En dessous de ~60 % d'unicité, Google désindexe en masse (« détectée,
    actuellement non indexée ») — autant le savoir AVANT l'import.
    """

    def __init__(self, max_samples: int = 60):
        self._last_by_variant: dict[int, set] = {}
        self.similarities: list[float] = []
        self.max_samples = max_samples

    def add(self, variant_idx: int, content: str):
        if len(self.similarities) >= self.max_samples:
            return
        sh = _shingles(strip_markup(content))
        prev = self._last_by_variant.get(variant_idx)
        if prev and sh:
            inter = len(prev & sh)
            union = len(prev | sh)
            if union:
                self.similarities.append(inter / union)
        self._last_by_variant[variant_idx] = sh

    def score(self) -> int | None:
        """Unicité moyenne en % (100 = pages totalement différentes)."""
        if not self.similarities:
            return None
        return round((1 - sum(self.similarities) / len(self.similarities)) * 100)


def build_department_rows(
    departments: list[str] | None,
    regions: list[str] | None,
) -> list[dict]:
    """Construit une pseudo-ville par département sélectionné (Type=Département).

    Population = somme des communes du département ; coordonnées = celles de
    la commune la plus peuplée (pour le maillage par proximité).
    """
    all_communes = load_villes(departments, regions, 0)
    by_dept: dict[str, list[dict]] = {}
    for v in all_communes:
        by_dept.setdefault(v["department"], []).append(v)

    rows = []
    for dept, communes in by_dept.items():
        name = DEPT_NAMES.get(dept, dept)
        biggest = max(communes, key=lambda c: c["population"])
        rows.append({
            "name": name,
            "slug": slugify(name),
            "department": dept,
            "postal_code": "",
            "population": sum(c["population"] for c in communes),
            "region": communes[0]["region"],
            "lat": biggest["lat"],
            "lng": biggest["lng"],
            "type": "Département",
        })
    return sorted(rows, key=lambda r: r["population"], reverse=True)


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
            slug = slugify(req.keyword_template.replace("{ville}", v["slug"]))
        else:
            keyword = f"{req.keyword_template} {v['name']}"
            slug = f"{slugify(req.keyword_template)}-{v['slug']}"
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

    # Charger les keywords existants (PENDING/VALIDATED/EXECUTING) pour éviter les doublons
    existing_keywords = set(
        kw for (kw,) in db.query(Action.keyword).filter(
            Action.site_id == site.id,
            Action.action_type == ActionType.GEOLOC,
            Action.status.in_([ActionStatus.PENDING, ActionStatus.VALIDATED, ActionStatus.EXECUTING]),
        ).all()
    )

    created = 0
    skipped = 0
    for v in villes:
        if "{ville}" in req.keyword_template:
            keyword = req.keyword_template.replace("{ville}", v["name"])
        else:
            keyword = f"{req.keyword_template} {v['name']}"

        if keyword in existing_keywords:
            skipped += 1
            continue

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
        existing_keywords.add(keyword)
        created += 1

    db.commit()
    return {
        "message": f"{created} actions géoloc créées dans la file de validation" + (f" ({skipped} doublons ignorés)" if skipped else ""),
        "total": created,
        "skipped": skipped,
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
    include_departments: bool = False  # Ajoute une page par département (Type=Département)
    generate_sitemaps: bool = True     # Génère aussi le zip de sitemaps XML segmentés


# Cache des fichiers générés
_generated_files: dict[str, dict] = {}


@router.post("/generate-file")
def generate_file(req: GeolocFileRequest, background_tasks: BackgroundTasks):
    """Génère un fichier Excel d'import WP avec toutes les pages géolocalisées."""
    villes = load_villes(req.departments, req.regions, req.min_population)
    if req.include_departments:
        villes = build_department_rows(req.departments, req.regions) + villes
    if not villes:
        raise HTTPException(status_code=400, detail="Aucune ville ne correspond aux filtres")
    file_key = f"{req.site_domain}_{req.keyword_template}_{len(villes)}_{datetime.now().strftime('%H%M%S')}"
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
        ] + YOAST_HEADERS
        if req.use_divi:
            headers.extend(["_et_pb_use_builder", "_et_pb_old_content"])
        ws.append(headers)

        today = datetime.now().strftime("%Y-%m-%d")
        url_entries: list[dict] = []
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
                slug_kw = slugify(req.keyword_template.replace("{ville}", v["slug"]))
            else:
                kw = f"{req.keyword_template} {v['name']}"
                slug_kw = f"{slugify(req.keyword_template)}-{v['slug']}"

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
            yoast_cols = [title, meta_desc, kw]
            if req.use_divi:
                row = [
                    v["name"], slug_kw, title, h1, meta_desc,
                    safe_cell(divi_content), req.post_thumbnail, today, req.post_author,
                    req.post_category, tags, req.post_status,
                    v["population"], v.get("type", "Ville"),
                ] + yoast_cols + ["on", ""]
            else:
                row = [
                    v["name"], slug_kw, title, h1, meta_desc,
                    safe_cell(content), req.post_thumbnail, today, req.post_author,
                    req.post_category, tags, req.post_status,
                    v["population"], v.get("type", "Ville"),
                ] + yoast_cols

            base_url = req.site_domain.replace("https://", "").replace("http://", "").rstrip("/")
            url_entries.append({"loc": f"https://{base_url}/{slug_kw}/", "lastmod": today, "dept": v["department"]})

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
            "sitemaps": build_sitemaps_zip(req.site_domain, url_entries) if req.generate_sitemaps else None,
        }

    background_tasks.add_task(run)
    return {"message": f"Génération en cours ({len(villes)} pages)", "file_key": file_key, "total": len(villes)}


@router.get("/file-status/{file_key}")
def file_status(file_key: str):
    """Vérifie le statut de la génération du fichier."""
    data = _generated_files.get(file_key)
    if not data:
        raise HTTPException(status_code=404, detail="Fichier non trouvé")
    return {
        "status": data["status"],
        "total": data.get("total", 0),
        "done": data.get("done", 0),
        "step": data.get("step", ""),
        "error": data.get("error", ""),
        "uniqueness": data.get("uniqueness"),
        "quality": data.get("quality"),
        "has_sitemaps": bool(data.get("sitemaps")),
    }


@router.get("/download-sitemaps/{file_key}")
def download_sitemaps(file_key: str):
    """Télécharge le zip de sitemaps XML segmentés associé à un export."""
    data = _generated_files.get(file_key)
    if not data or data["status"] != "done" or not data.get("sitemaps"):
        raise HTTPException(status_code=404, detail="Sitemaps non disponibles pour cet export")
    return StreamingResponse(
        io.BytesIO(data["sitemaps"]),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="sitemaps_{file_key}.zip"'},
    )


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


# ======================================================================
# Advanced Geoloc Content Engine — new endpoints
# ======================================================================


class SourceAnalysisRequest(BaseModel):
    source_text: str
    brief: dict = {}


class AnchorsRequest(BaseModel):
    ctx: dict


class BlocksRequest(BaseModel):
    source_text: str
    ctx: dict
    anchors: list[str] = []


class CompetitorRequest(BaseModel):
    urls: list[str]
    ctx: dict


class VariantsRequest(BaseModel):
    blocs: dict
    ctx: dict
    count: int = 3


class FullPipelineRequest(BaseModel):
    source_text: str = ""
    keyword_template: str = ""
    brief: dict = {}
    site_name: str
    site_domain: str
    post_author: str = "admin"
    post_category: str = ""
    post_status: str = "draft"
    post_thumbnail: str = ""
    images: list[str] = []
    departments: list[str] | None = None
    regions: list[str] | None = None
    min_population: int = 5000
    use_divi: bool = False
    nb_variants: int = 3
    competitor_urls: list[str] = []
    colors: dict = {}
    video_url: str = ""              # URL vidéo (YouTube/Vimeo) insérée dans chaque page
    include_departments: bool = False  # Ajoute une page par département (Type=Département)
    schema_org: bool = True          # Injecte LocalBusiness/FAQPage/Breadcrumb JSON-LD
    use_paa: bool = False            # FAQ basée sur les People Also Ask réels (DataForSEO)
    generate_sitemaps: bool = True   # Génère aussi le zip de sitemaps XML segmentés
    vary_structure: bool = True      # Ordre des sections propre au domaine (anti-footprint)
    local_data: bool = True          # Bloc de contexte local réel (communes voisines + distances)
    serp_gain: bool = False          # Analyse SERP réelle → information gain (DataForSEO + Claude)
    quality_check: bool = False      # Agent juge qualité (Haiku) sur chaque variante
    real_reviews: bool = False       # Émettre l'AggregateRating (uniquement si vrais avis)
    business_info: dict = {}         # NAP réel : name/phone/email/address/city/postal_code/lat/lng


@router.post("/analyze-source")
def analyze_source(req: SourceAnalysisRequest):
    """Analyze source text and extract context for geoloc page generation."""
    try:
        engine = GeolocEngine()
        ctx = engine.analyze_source(req.source_text, req.brief or None)
        return {"ctx": ctx}
    except Exception as exc:
        logger.error("analyze-source error: %s", traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Erreur d'analyse : {exc}")


@router.post("/generate-anchors")
def generate_anchors(req: AnchorsRequest):
    """Generate internal linking anchor texts based on context."""
    try:
        engine = GeolocEngine()
        anchors = engine.generate_anchors(req.ctx)
        return {"anchors": anchors}
    except Exception as exc:
        logger.error("generate-anchors error: %s", traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Erreur génération ancres : {exc}")


@router.post("/optimize-blocks")
def optimize_blocks(req: BlocksRequest):
    """Split and optimize content into 5 SEO blocks."""
    try:
        engine = GeolocEngine()
        blocs = engine.optimize_blocks(req.source_text, req.ctx, req.anchors)
        return {"blocs": blocs}
    except Exception as exc:
        logger.error("optimize-blocks error: %s", traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Erreur optimisation blocs : {exc}")


@router.post("/analyze-competitors")
def analyze_competitors(req: CompetitorRequest):
    """Analyze competitor URLs and return missing themes, key arguments, enrichment suggestions."""
    if not req.urls:
        raise HTTPException(status_code=400, detail="Au moins une URL est requise")
    try:
        engine = GeolocEngine()
        result = engine.analyze_competitors(req.urls, req.ctx)
        return result
    except Exception as exc:
        logger.error("analyze-competitors error: %s", traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Erreur analyse concurrents : {exc}")


@router.post("/generate-variants")
def generate_variants(req: VariantsRequest):
    """Generate content variants to avoid duplicate content across cities."""
    try:
        engine = GeolocEngine()
        variants = engine.generate_variants(req.blocs, req.ctx, req.count)
        return {"variants": variants, "count": len(variants)}
    except Exception as exc:
        logger.error("generate-variants error: %s", traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Erreur génération variantes : {exc}")


@router.post("/build-file")
def build_file(req: FullPipelineRequest, background_tasks: BackgroundTasks):
    """Full pipeline: analyze, generate blocks/anchors/variants, assemble all city pages, build Excel."""
    villes = load_villes(req.departments, req.regions, req.min_population)
    if req.include_departments:
        villes = build_department_rows(req.departments, req.regions) + villes
    if not villes:
        raise HTTPException(status_code=400, detail="Aucune ville ne correspond aux filtres")

    file_key = f"adv_{req.site_domain}_{len(villes)}_{datetime.now().strftime('%H%M%S')}"
    _generated_files[file_key] = {
        "status": "running",
        "total": len(villes),
        "done": 0,
        "step": "init",
    }

    def run_pipeline():
        import openpyxl

        engine = GeolocEngine()
        state = _generated_files[file_key]

        try:
            # Texte source : si vide, on synthétise un brief pour la génération 100% IA
            brief = dict(req.brief or {})
            if req.keyword_template and "keyword" not in brief:
                brief["keyword"] = req.keyword_template
            source_text = req.source_text.strip()
            if not source_text:
                source_text = (
                    f"Rédige un contenu expert et commercial pour le mot-clé « {req.keyword_template} ». "
                    f"Activité : {brief.get('activite', '')}. Services : {brief.get('services', '')}. "
                    f"Attentes clients : {brief.get('mots_clients', '')}."
                )

            # --- Step 1: Analyze source text ---
            state["step"] = "analyze"
            ctx = engine.analyze_source(source_text, brief)

            # --- Step 2: Generate anchors ---
            state["step"] = "anchors"
            anchors = engine.generate_anchors(ctx)

            # --- Step 2b: People Also Ask réels (optionnel, DataForSEO) ---
            paa_questions: list[str] = []
            if req.use_paa:
                state["step"] = "paa"
                try:
                    from app.config import get_settings as _gs
                    if _gs().dataforseo_login:
                        from app.services.dataforseo import DataForSEOClient
                        paa_questions = DataForSEOClient().get_people_also_ask(
                            ctx.get("keyword") or req.keyword_template
                        )
                        logger.info("PAA récupérées : %d questions", len(paa_questions))
                except Exception as exc:
                    logger.warning("PAA indisponibles (%s) — FAQ générée par Claude", exc)

            # --- Step 2c: SERP information gain (optionnel, DataForSEO + Claude) ---
            if req.serp_gain:
                state["step"] = "serp"
                try:
                    from app.config import get_settings as _gs
                    if _gs().dataforseo_login:
                        from app.services.dataforseo import DataForSEOClient
                        serp = DataForSEOClient().get_serp_top_results(
                            ctx.get("keyword") or req.keyword_template
                        )
                        gain = engine.serp_information_gain(
                            ctx.get("keyword") or req.keyword_template, serp
                        )
                        if gain.get("themes_a_couvrir") or gain.get("angle_differenciant"):
                            ctx["serp_gain"] = gain
                            # Injecte thèmes + angle dans le contexte métier lu par optimize_blocks
                            extra = []
                            if gain.get("angle_differenciant"):
                                extra.append(f"Angle différenciant à exploiter : {gain['angle_differenciant']}")
                            if gain.get("themes_a_couvrir"):
                                extra.append("Thèmes que couvrent les pages qui rankent (à traiter) : "
                                             + ", ".join(gain["themes_a_couvrir"][:8]))
                            if gain.get("entites_importantes"):
                                extra.append("Entités/termes sémantiques à intégrer : "
                                             + ", ".join(gain["entites_importantes"][:10]))
                            ctx["contexte_metier"] = (ctx.get("contexte_metier", "") + " " + " ".join(extra)).strip()
                            logger.info("SERP gain : %d thèmes", len(gain.get("themes_a_couvrir", [])))
                except Exception as exc:
                    logger.warning("SERP gain indisponible (%s)", exc)

            # --- Step 3: Optimize blocks ---
            state["step"] = "blocks"
            blocs = engine.optimize_blocks(source_text, ctx, anchors, paa_questions=paa_questions)
            # Les avis clients sont extraits : ils ne passent pas par les variantes
            # (rotation par ville pour l'unicité, cf. assemble_page)
            avis = blocs.pop("BLOC_AVIS", [])
            if not isinstance(avis, list):
                avis = []

            # --- Step 4: Analyze competitors (if URLs provided) ---
            competitor_data = None
            if req.competitor_urls:
                state["step"] = "competitors"
                competitor_data = engine.analyze_competitors(req.competitor_urls, ctx)
                # Enrich context with competitor insights
                ctx["competitor_insights"] = competitor_data

            # --- Step 5: Generate variants ---
            state["step"] = "variants"
            variants = engine.generate_variants(blocs, ctx, req.nb_variants)
            if not variants:
                variants = [blocs]

            # --- Step 6+7: Assemble pages & build Excel ---
            state["step"] = "assemble"
            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = "Import WP"

            headers = [
                "Ville actuelle", "SLUG", "post_title", "H1", "post_description",
                "post_content", "post_thumbnail", "post_date", "post_author",
                "post_category", "post_tag", "post_status", "Population", "Type",
            ] + YOAST_HEADERS
            if req.use_divi:
                headers.extend(["_et_pb_use_builder", "_et_pb_old_content"])
            ws.append(headers)

            from datetime import timedelta

            base_date = datetime.now()
            keyword = ctx.get("keyword") or req.keyword_template or source_text[:30]
            slug_base = slugify(ctx.get("slug_base") or keyword)
            # analyze_source renvoie template_title / template_h1 / template_meta avec __VILLE__
            title_tpl = ctx.get("template_title") or f"{keyword.title()} __VILLE__ | {req.site_name}"
            h1_tpl = ctx.get("template_h1") or f"{keyword.title()} __VILLE__"
            meta_tpl = ctx.get("template_meta") or (
                f"{keyword.capitalize()} à __VILLE__ ({{departement}}). Intervention rapide. Devis gratuit."
            )

            # Le moteur (maillage/assemblage) attend les clés nom/slug/lat/lon
            villes_engine = [
                {
                    "nom": v["name"],
                    "slug": v["slug"],
                    "lat": v["lat"],
                    "lon": v["lng"],
                    "population": v["population"],
                    "zone_label": (
                        v["name"] if v.get("type") != "Département"
                        else f"{v['name']} et ses communes"
                    ),
                }
                for v in villes
            ]

            # Variation de structure par domaine (anti-footprint réseau)
            layout_seed = layout_seed_from(req.site_domain) if req.vary_structure else 0

            # Index des villes par département (annuaire des pages hub)
            cities_by_dept: dict[str, list[dict]] = {}
            for vv in villes:
                if vv.get("type") != "Département":
                    cities_by_dept.setdefault(vv["department"], []).append(vv)

            uniq = UniquenessTracker()
            url_entries: list[dict] = []

            # Agent juge qualité : évalue chaque variante sur une ville témoin
            # (coût = nb_variants appels Haiku, pas un par page).
            quality_report = None
            if req.quality_check and villes:
                state["step"] = "quality"
                sample = villes[0]
                sample_engine = villes_engine[0]
                scores, all_issues, weak = [], [], 0
                for vi, variant in enumerate(variants):
                    sample_content = engine.assemble_page(
                        blocs=variant, ville=sample_engine, ctx=ctx,
                        maillage="", variant_idx=vi, use_divi=False,
                        images=[], colors=req.colors,
                        h1=replace_ville(h1_tpl, sample["name"]),
                        avis=[], layout_seed=layout_seed,
                    )
                    verdict = engine.judge_quality(sample_content, ctx)
                    if verdict.get("score") is not None:
                        scores.append(verdict["score"])
                    if not verdict.get("keep", True):
                        weak += 1
                    all_issues.extend(verdict.get("issues", [])[:3])
                if scores:
                    quality_report = {
                        "avg_score": round(sum(scores) / len(scores)),
                        "min_score": min(scores),
                        "weak_variants": weak,
                        "total_variants": len(variants),
                        "issues": list(dict.fromkeys(all_issues))[:8],
                    }
                    logger.info("Juge qualité : score moyen %s/100, %d variantes faibles",
                                quality_report["avg_score"], weak)

            for i, v in enumerate(villes):
                # Rotate variants
                variant = variants[i % len(variants)]
                is_dept = v.get("type") == "Département"

                # Maillage interne : liens vers les 8 villes les plus proches
                maillage = engine.generate_maillage(
                    ville=v["name"],
                    all_villes=villes_engine,
                    slug_base=slug_base,
                    anchors=anchors,
                    nb=8,
                )

                # Métadonnées d'abord (le H1 est intégré en tête de contenu)
                replacements_pre = {"__VILLE__": v["name"], "{ville}": v["name"]}
                h1_page = h1_tpl
                for ph, val in replacements_pre.items():
                    h1_page = h1_page.replace(ph, val)

                # Bloc de contexte local réel (communes voisines + distances) —
                # données factuelles uniques par ville, cœur de la valeur locale.
                extra_html = ""
                dept_name = DEPT_NAMES.get(v["department"], v["department"])
                dept_slug = f"{slug_base}-{slugify(dept_name)}"
                if req.local_data and not is_dept:
                    extra_html += engine.local_context_block(
                        villes_engine[i], villes_engine, ctx,
                        postal_code=v.get("postal_code", ""),
                        department=v["department"], region=v.get("region", ""),
                        department_name=dept_name,
                    )

                # Pages hub (département) : annuaire de toutes leurs communes.
                # Pages villes : lien remontant vers leur page département.
                if is_dept:
                    dept_cities = cities_by_dept.get(v["department"], [])
                    if dept_cities:
                        links = "".join(
                            f'<li><a href="/{slug_base}-{c["slug"]}/">'
                            f'{ctx.get("keyword", "").capitalize()} {c["name"]}</a></li>'
                            for c in dept_cities
                        )
                        extra_html += (
                            f"<h2>Nos interventions dans les communes de {v['name']}</h2>"
                            f'<ul class="annuaire-communes" style="columns:3;column-gap:24px">{links}</ul>'
                        )
                elif req.include_departments:
                    extra_html += (
                        f"<p>Découvrez aussi notre page départementale : "
                        f'<a href="/{dept_slug}/">{ctx.get("keyword", "").capitalize()} {dept_name}</a></p>'
                    )

                # Schema.org JSON-LD (LocalBusiness+Rating, FAQPage, Breadcrumb)
                jsonld = ""
                if req.schema_org:
                    faq_pairs = parse_faq(replace_ville(str(variant.get("BLOC_FAQ", "")), v["name"]))
                    breadcrumb = [("Accueil", "/")]
                    if req.include_departments and not is_dept:
                        breadcrumb.append((dept_name, f"/{dept_slug}/"))
                    breadcrumb.append((v["name"], f"/{slug_base}-{v['slug']}/"))
                    jsonld = build_jsonld(
                        site_name=req.site_name,
                        site_domain=req.site_domain,
                        keyword=ctx.get("keyword", ""),
                        ville={**villes_engine[i], "postal_code": v.get("postal_code", ""),
                               "type": v.get("type", "Ville")},
                        faq_pairs=faq_pairs,
                        avis=engine.render_avis(avis, start=i, count=6),
                        breadcrumb=breadcrumb,
                        real_reviews=req.real_reviews,
                        business_info=req.business_info,
                    )

                # Assemble content
                content = engine.assemble_page(
                    blocs=variant,
                    ville=villes_engine[i],
                    ctx=ctx,
                    maillage=maillage,
                    variant_idx=i,
                    use_divi=req.use_divi,
                    images=req.images,
                    colors=req.colors,
                    h1=h1_page,
                    video_url=req.video_url,
                    avis=avis,
                    layout_seed=layout_seed,
                    jsonld=jsonld,
                    extra_html=extra_html,
                )
                uniq.add(i % len(variants), content)

                # Build metadata — supporte __VILLE__ (moteur) et {ville} (legacy)
                replacements = {
                    "__VILLE__": v["name"],
                    "{ville}": v["name"],
                    "{departement}": v["department"],
                    "{region}": v.get("region", ""),
                    "{code_postal}": v.get("postal_code", ""),
                }

                def _replace(tpl: str) -> str:
                    for ph, val in replacements.items():
                        tpl = tpl.replace(ph, val)
                    return tpl

                title = _replace(title_tpl)
                h1 = _replace(h1_tpl)
                meta_desc = _replace(meta_tpl)
                slug = f"{slug_base}-{v['slug']}"
                tags = f"{keyword},{v['name'].lower()},{v['department']}"
                # Dates étalées (+1 jour par ville) pour éviter un footprint de publication massive
                today = (base_date + timedelta(days=i)).strftime("%Y-%m-%d")

                row = [
                    v["name"], slug, title, h1, meta_desc,
                    safe_cell(content), req.post_thumbnail, today, req.post_author,
                    req.post_category, tags, req.post_status,
                    v["population"], v.get("type", "Ville"),
                ] + [title, meta_desc, keyword if is_dept else f"{keyword} {v['name']}"]
                if req.use_divi:
                    row.extend(["on", ""])

                base_url = req.site_domain.replace("https://", "").replace("http://", "").rstrip("/")
                url_entries.append({"loc": f"https://{base_url}/{slug}/", "lastmod": today, "dept": v["department"]})

                ws.append(row)
                state["done"] = i + 1

            # --- Step 8: Save to cache ---
            state["step"] = "saving"
            buffer = io.BytesIO()
            wb.save(buffer)
            buffer.seek(0)

            _generated_files[file_key] = {
                "status": "done",
                "total": len(villes),
                "done": len(villes),
                "step": "done",
                "data": buffer.getvalue(),
                "filename": f"adv_import_{req.site_domain}_{len(villes)}_pages.xlsx",
                "ctx": ctx,
                "nb_variants": len(variants),
                "uniqueness": uniq.score(),
                "quality": quality_report,
                "sitemaps": build_sitemaps_zip(req.site_domain, url_entries) if req.generate_sitemaps else None,
            }

        except Exception as exc:
            logger.error("build-file pipeline error: %s", traceback.format_exc())
            _generated_files[file_key] = {
                "status": "error",
                "total": state.get("total", 0),
                "done": state.get("done", 0),
                "step": state.get("step", "unknown"),
                "error": str(exc),
            }

    background_tasks.add_task(run_pipeline)
    return {
        "message": f"Pipeline avancé lancé ({len(villes)} villes)",
        "file_key": file_key,
        "total": len(villes),
    }
