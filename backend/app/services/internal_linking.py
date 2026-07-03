"""
Analyse du maillage interne à partir des pages crawlées.

Produit : inlinks par URL, pages orphelines, pages les plus liées, analyse
des ancres (génériques / sur-optimisées), et des suggestions d'ajustement
adaptées à une stratégie « site service + géolocalisé » (clusters service ×
ville, renforcement des orphelines, variation des ancres).
"""
import re
import unicodedata
from collections import defaultdict
from urllib.parse import urlparse

GENERIC_ANCHORS = {
    "cliquez ici", "cliquer ici", "ici", "en savoir plus", "lire la suite",
    "lire plus", "plus", "voir plus", "详细", "read more", "en savoir +",
    "découvrir", "voir", "ce lien", "lien", "click here",
}


def _norm(t: str) -> str:
    t = unicodedata.normalize("NFKD", t or "").encode("ascii", "ignore").decode("ascii")
    return t.lower().strip()


def _slug(url: str) -> str:
    slug = _norm(urlparse(url).path).strip("/").split("/")[-1]
    return re.sub(r"\.(html?|php|aspx?)$", "", slug)   # retire l'extension éventuelle


def _service_base(page: dict) -> str:
    """Base « service » d'une page géoloc = slug sans la ville détectée.

    ex: /epaviste-lyon/ (ville=Lyon) -> "epaviste".
    """
    slug = _slug(page["url"])
    city = _norm(page.get("city", "")).replace(" ", "-")
    if city and city in slug:
        base = slug.replace(city, "").strip("-")
        return re.sub(r"-+", "-", base)
    return ""


def analyze_internal_linking(pages: list[dict], start_url: str) -> dict:
    by_url = {p["url"]: p for p in pages}
    start = start_url.rstrip("/")

    # Graphe des inlinks (nombre de pages DISTINCTES pointant vers une URL)
    inlink_sources: dict[str, set] = defaultdict(set)
    anchors_to: dict[str, list] = defaultdict(list)
    for p in pages:
        for a in p.get("anchors_out", []):
            tgt = a["to"]
            if tgt in by_url and tgt != p["url"]:
                inlink_sources[tgt].add(p["url"])
                if a.get("anchor"):
                    anchors_to[tgt].append(a["anchor"])
    inlinks_by_url = {u: len(s) for u, s in inlink_sources.items()}

    def is_indexable_html(p):
        return p.get("content_type") == "text/html" and p.get("status", 0) < 400 \
            and p.get("indexable", True)

    html_pages = [p for p in pages if is_indexable_html(p)]

    # Pages orphelines : aucune page interne ne pointe vers elles (hors accueil)
    orphans = [
        p["url"] for p in html_pages
        if inlinks_by_url.get(p["url"], 0) == 0 and p["url"].rstrip("/") != start
    ]

    # Top pages par inlinks
    top_linked = sorted(
        ({"url": p["url"], "inlinks": inlinks_by_url.get(p["url"], 0),
          "city": p.get("city", "")} for p in html_pages),
        key=lambda x: x["inlinks"], reverse=True,
    )[:20]

    # Pages sous-maillées : bon contenu mais peu d'inlinks
    under_linked = sorted(
        ({"url": p["url"], "inlinks": inlinks_by_url.get(p["url"], 0),
          "words": p.get("word_count", 0), "city": p.get("city", "")}
         for p in html_pages
         if inlinks_by_url.get(p["url"], 0) <= 2 and p.get("word_count", 0) >= 300
         and p["url"].rstrip("/") != start),
        key=lambda x: (x["inlinks"], -x["words"]),
    )[:30]

    # Analyse des ancres
    anchor_issues = _anchor_analysis(pages, by_url)

    # Clusters service × ville (stratégie géoloc)
    clusters: dict[str, list[dict]] = defaultdict(list)
    for p in html_pages:
        if p.get("city"):
            base = _service_base(p)
            if base:
                clusters[base].append(p)

    suggestions = _build_suggestions(
        html_pages, by_url, inlinks_by_url, inlink_sources,
        orphans, clusters, start, anchor_issues,
    )

    return {
        "inlinks_by_url": inlinks_by_url,
        "orphans": orphans,
        "top_linked": top_linked,
        "under_linked": under_linked,
        "anchor_issues": anchor_issues,
        "clusters": {
            base: {"pages": len(ps), "cities": sorted({p["city"] for p in ps})}
            for base, ps in clusters.items()
        },
        "suggestions": suggestions,
    }


def _anchor_analysis(pages: list[dict], by_url: dict) -> dict:
    generic = []          # liens avec ancre générique
    over_optimized = []   # cibles recevant trop d'ancres exactes identiques
    target_anchor_counts: dict[str, dict] = defaultdict(lambda: defaultdict(int))
    for p in pages:
        for a in p.get("anchors_out", []):
            anc = (a.get("anchor") or "").strip()
            tgt = a["to"]
            if tgt not in by_url:
                continue
            if _norm(anc) in GENERIC_ANCHORS or not anc:
                generic.append({"from": p["url"], "to": tgt, "anchor": anc or "(vide)"})
            target_anchor_counts[tgt][_norm(anc)] += 1
    for tgt, counts in target_anchor_counts.items():
        total = sum(counts.values())
        if total >= 5:
            top_anchor, top_count = max(counts.items(), key=lambda x: x[1])
            if top_anchor and top_count / total >= 0.8:
                over_optimized.append({
                    "to": tgt, "anchor": top_anchor,
                    "ratio": round(top_count / total * 100), "total": total,
                })
    return {"generic": generic[:40], "over_optimized": over_optimized[:20]}


def _build_suggestions(pages, by_url, inlinks, inlink_sources, orphans,
                       clusters, start, anchor_issues) -> list[dict]:
    suggestions: list[dict] = []
    linked_pairs = {(p["url"], a["to"]) for p in pages for a in p.get("anchors_out", [])}

    def already_linked(a, b):
        return (a, b) in linked_pairs

    # 1. Orphelines → lien depuis l'accueil (priorité haute)
    home = start
    for url in orphans[:40]:
        p = by_url.get(url, {})
        city = p.get("city", "")
        anchor = _suggest_anchor(p)
        suggestions.append({
            "priority": "Haute", "from": home, "to": url, "anchor": anchor,
            "reason": "Page orpheline (aucun lien interne entrant) — la relier au moins depuis l'accueil ou un hub.",
        })

    # 2. Maillage géoloc : dans chaque cluster service, relier les villes entre elles
    for base, cpages in clusters.items():
        if len(cpages) < 2:
            continue
        # trie par inlinks croissants : on renforce les plus faibles
        cpages_sorted = sorted(cpages, key=lambda p: inlinks.get(p["url"], 0))
        for target in cpages_sorted[:15]:
            if inlinks.get(target["url"], 0) >= 3:
                continue
            # trouver 2 pages du même cluster qui ne pointent pas encore vers elle
            donors = [
                s for s in cpages
                if s["url"] != target["url"] and not already_linked(s["url"], target["url"])
            ][:2]
            for donor in donors:
                suggestions.append({
                    "priority": "Moyenne", "from": donor["url"], "to": target["url"],
                    "anchor": _suggest_anchor(target),
                    "reason": f"Maillage géoloc « {base} » : relier les villes du même service "
                              f"(renforce {target.get('city') or 'la page'}, peu d'inlinks).",
                })

    # 3. Pages sous-maillées (bon contenu, peu d'inlinks)
    for p in pages:
        u = p["url"]
        if u.rstrip("/") == start or inlinks.get(u, 0) > 2 or p.get("word_count", 0) < 400:
            continue
        if u in orphans:
            continue  # déjà couvert
        # un donneur pertinent : une page à fort inlinks (autorité) qui ne pointe pas déjà
        authorities = sorted(pages, key=lambda x: inlinks.get(x["url"], 0), reverse=True)
        donor = next((a for a in authorities
                      if a["url"] != u and not already_linked(a["url"], u)
                      and a.get("content_type") == "text/html"), None)
        if donor:
            suggestions.append({
                "priority": "Basse", "from": donor["url"], "to": u,
                "anchor": _suggest_anchor(p),
                "reason": "Page à bon contenu mais peu d'inlinks — ajouter un lien depuis une page à forte autorité.",
            })
        if len([s for s in suggestions if s["priority"] == "Basse"]) >= 20:
            break

    # 4. Ancres sur-optimisées → varier
    for oo in anchor_issues.get("over_optimized", [])[:10]:
        suggestions.append({
            "priority": "Moyenne", "from": "(plusieurs pages)", "to": oo["to"],
            "anchor": "Varier les ancres",
            "reason": f"{oo['ratio']}% des liens vers cette page utilisent la même ancre exacte "
                      f"« {oo['anchor']} » — diversifier pour éviter la sur-optimisation.",
        })

    # 5. Ancres génériques → remplacer
    for g in anchor_issues.get("generic", [])[:10]:
        tgt = by_url.get(g["to"], {})
        suggestions.append({
            "priority": "Basse", "from": g["from"], "to": g["to"],
            "anchor": _suggest_anchor(tgt),
            "reason": f"Ancre générique « {g['anchor']} » — la remplacer par une ancre descriptive avec mot-clé.",
        })

    return suggestions[:120]


def _suggest_anchor(page: dict) -> str:
    """Propose une ancre descriptive à partir du H1/title/ville de la cible."""
    h1 = (page.get("h1") or "").strip()
    if h1 and len(h1) <= 70:
        return h1
    title = (page.get("title") or "").split("|")[0].split("-")[0].strip()
    if title:
        return title[:70]
    return (page.get("city") or "cette page").strip()
