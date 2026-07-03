"""
Analyse avancée du maillage interne à partir des pages crawlées.

Au-delà du simple comptage de liens, ce module modélise le FLUX D'AUTORITÉ :
PageRank interne, profondeur de clic depuis l'accueil, typologie des nœuds
(hubs/puits), cannibalisation (spécifique géoloc), cohésion des silos, et des
suggestions de liens SCORÉES par impact réel. Tout en Python pur, gratuit,
sur les données déjà crawlées.
"""
import re
import unicodedata
from collections import defaultdict, deque
from urllib.parse import urlparse

GENERIC_ANCHORS = {
    "cliquez ici", "cliquer ici", "ici", "en savoir plus", "lire la suite",
    "lire plus", "plus", "voir plus", "read more", "en savoir +",
    "découvrir", "voir", "ce lien", "lien", "click here",
}


def _norm(t: str) -> str:
    t = unicodedata.normalize("NFKD", t or "").encode("ascii", "ignore").decode("ascii")
    return t.lower().strip()


def _slug(url: str) -> str:
    slug = _norm(urlparse(url).path).strip("/").split("/")[-1]
    return re.sub(r"\.(html?|php|aspx?)$", "", slug)


def _service_base(page: dict) -> str:
    """Base « service » d'une page géoloc = slug sans la ville détectée."""
    if not page.get("url"):
        return ""
    slug = _slug(page["url"])
    city = _norm(page.get("city", "")).replace(" ", "-")
    if city and city in slug:
        base = slug.replace(city, "").strip("-")
        return re.sub(r"-+", "-", base)
    return ""


# ---------------------------------------------------------------------------
# Graphe & métriques de flux
# ---------------------------------------------------------------------------


def _build_graph(pages: list[dict], by_url: dict):
    """Construit le graphe dirigé des liens internes (arêtes dédupliquées,
    nofollow exclus du flux)."""
    out_links: dict[str, set] = defaultdict(set)
    inlink_sources: dict[str, set] = defaultdict(set)
    for p in pages:
        u = p["url"]
        for a in p.get("anchors_out", []):
            tgt = a["to"]
            if tgt not in by_url or tgt == u:
                continue
            rel = a.get("rel") or []
            if isinstance(rel, str):
                rel = rel.split()
            if any("nofollow" in str(x).lower() for x in rel):
                continue
            out_links[u].add(tgt)
            inlink_sources[tgt].add(u)
    return out_links, inlink_sources


def _pagerank(urls: list[str], out_links: dict, d: float = 0.85, iters: int = 50) -> dict:
    """PageRank interne (power iteration + gestion des dangling nodes)."""
    n = len(urls)
    if n == 0:
        return {}
    pr = {u: 1.0 / n for u in urls}
    outdeg = {u: len(out_links.get(u, ())) for u in urls}
    # index inverse : cibles → sources
    incoming: dict[str, list] = defaultdict(list)
    for u in urls:
        for v in out_links.get(u, ()):
            if v in pr:
                incoming[v].append(u)
    for _ in range(iters):
        dangling = sum(pr[u] for u in urls if outdeg[u] == 0)
        new = {}
        base = (1 - d) / n + d * dangling / n
        for u in urls:
            s = sum(pr[q] / outdeg[q] for q in incoming.get(u, ()) if outdeg[q])
            new[u] = base + d * s
        diff = sum(abs(new[u] - pr[u]) for u in urls)
        pr = new
        if diff < 1e-7:
            break
    mx = max(pr.values()) or 1.0
    return {u: round(v / mx * 100, 1) for u, v in pr.items()}   # normalisé 0-100


def _click_depth(urls_set: set, out_links: dict, start: str) -> dict:
    """Profondeur de clic minimale depuis l'accueil (BFS). ∞ = inatteignable."""
    # Trouver la racine réelle dans le set (start peut avoir un / final)
    root = start.rstrip("/")
    root = next((u for u in urls_set if u.rstrip("/") == root), None)
    if root is None:
        # fallback : page la plus courte (souvent l'accueil)
        root = min(urls_set, key=lambda u: len(u)) if urls_set else None
    depth = {u: None for u in urls_set}
    if root is None:
        return depth
    depth[root] = 0
    q = deque([root])
    while q:
        u = q.popleft()
        for v in out_links.get(u, ()):
            if v in depth and depth[v] is None:
                depth[v] = depth[u] + 1
                q.append(v)
    return depth


# ---------------------------------------------------------------------------
# Cannibalisation & silos (spécifique géoloc)
# ---------------------------------------------------------------------------


def _detect_cannibalization(html_pages: list[dict]) -> list[dict]:
    """Repère les pages qui se disputent le même mot-clé/intention."""
    groups = []
    # 1. Même couple (service, ville) → cannibalisation quasi certaine
    by_key: dict[tuple, list] = defaultdict(list)
    for p in html_pages:
        base = _service_base(p)
        if base and p.get("city"):
            by_key[(base, _norm(p["city"]))].append(p["url"])
    for (base, city), urls in by_key.items():
        if len(urls) >= 2:
            groups.append({"key": f"{base} / {city}", "urls": urls,
                           "type": "Même service + ville"})
    # 2. Titles quasi identiques (signature de tokens hors ville/stopwords)
    from app.services.crawler import _LANG_STOPWORDS
    stop = set().union(*_LANG_STOPWORDS.values())
    sigs = []
    for p in html_pages:
        toks = {w for w in re.findall(r"[a-zà-ÿ0-9]+", _norm(p.get("title", "")))
                if len(w) > 2 and w not in stop and w != _norm(p.get("city", ""))}
        if toks:
            sigs.append((p["url"], toks))
    seen_pairs = set()
    for i in range(len(sigs)):
        u1, s1 = sigs[i]
        dup = [u1]
        for j in range(i + 1, len(sigs)):
            u2, s2 = sigs[j]
            if (u1, u2) in seen_pairs:
                continue
            inter = len(s1 & s2)
            if inter and inter / len(s1 | s2) >= 0.85:
                dup.append(u2)
                seen_pairs.add((u1, u2))
        if len(dup) >= 2 and not any(u1 in g["urls"] for g in groups):
            groups.append({"key": " ".join(sorted(s1))[:50], "urls": dup,
                           "type": "Titres quasi identiques"})
    return groups[:60]


def _silo_analysis(clusters: dict, out_links: dict, by_url: dict, start: str) -> list[dict]:
    """Cohésion des silos service×ville : densité + hub de tête."""
    silos = []
    for base, cpages in clusters.items():
        if len(cpages) < 3:
            continue
        urls = {p["url"] for p in cpages}
        n = len(urls)
        intra = sum(1 for u in urls for v in out_links.get(u, ()) if v in urls)
        density = round(intra / (n * (n - 1)), 3) if n > 1 else 0
        # Hub de tête : une page (hors cluster) qui lie ≥60% des villes du cluster
        hub, hub_cov = "", 0.0
        for src, tgts in out_links.items():
            cov = len(urls & tgts) / n
            if cov > hub_cov:
                hub_cov, hub = cov, src
        silos.append({
            "cluster": base, "pages": n, "density": density,
            "hub": hub if hub_cov >= 0.5 else "",
            "hub_coverage": round(hub_cov * 100),
            "cohesion": "forte" if density >= 0.15 else ("moyenne" if density >= 0.05 else "faible"),
        })
    return sorted(silos, key=lambda s: -s["pages"])


# ---------------------------------------------------------------------------
# Analyse principale
# ---------------------------------------------------------------------------


def analyze_internal_linking(pages: list[dict], start_url: str) -> dict:
    by_url = {p["url"]: p for p in pages}
    start = start_url.rstrip("/")
    urls = list(by_url.keys())

    out_links, inlink_sources = _build_graph(pages, by_url)
    inlinks_by_url = {u: len(s) for u, s in inlink_sources.items()}
    for u in urls:
        inlinks_by_url.setdefault(u, 0)

    pagerank = _pagerank(urls, out_links)
    click_depth = _click_depth(set(urls), out_links, start)

    def is_indexable_html(p):
        return p.get("content_type") == "text/html" and p.get("status", 0) < 400 \
            and p.get("indexable", True)

    html_pages = [p for p in pages if is_indexable_html(p)]

    # Injecte les métriques dans les pages (pour l'Excel et le front)
    for p in pages:
        u = p["url"]
        p["pagerank"] = pagerank.get(u, 0)
        d = click_depth.get(u)
        p["click_depth"] = d if d is not None else -1   # -1 = inatteignable

    # Orphelines (0 inlink) + inatteignables (depth None) distinctes
    orphans = [p["url"] for p in html_pages
               if inlinks_by_url.get(p["url"], 0) == 0 and p["url"].rstrip("/") != start]
    unreachable = [p["url"] for p in html_pages
                   if click_depth.get(p["url"]) is None and p["url"].rstrip("/") != start]
    deep_pages = [{"url": p["url"], "depth": click_depth.get(p["url"])}
                  for p in html_pages if (click_depth.get(p["url"]) or 0) >= 4]

    # Typologie des nœuds
    node_types: dict[str, str] = {}
    for p in html_pages:
        u = p["url"]
        io, out = inlinks_by_url.get(u, 0), p.get("internal_out", 0)
        if u.rstrip("/") == start:
            node_types[u] = "accueil"
        elif out == 0 and io >= 2:
            node_types[u] = "puits"          # reçoit du jus, n'en redistribue pas
        elif out >= 15 and io >= 1:
            node_types[u] = "hub"
        elif io == 0:
            node_types[u] = "source"
        else:
            node_types[u] = "normal"
    sinks = sorted([{"url": u, "pagerank": pagerank.get(u, 0)}
                    for u, t in node_types.items() if t == "puits"],
                   key=lambda x: -x["pagerank"])[:15]

    top_linked = sorted(
        ({"url": p["url"], "inlinks": inlinks_by_url.get(p["url"], 0),
          "pagerank": pagerank.get(p["url"], 0), "city": p.get("city", "")}
         for p in html_pages),
        key=lambda x: (-x["pagerank"], -x["inlinks"]))[:20]

    anchor_issues = _anchor_analysis(pages, by_url)

    clusters: dict[str, list[dict]] = defaultdict(list)
    for p in html_pages:
        if p.get("city"):
            base = _service_base(p)
            if base:
                clusters[base].append(p)

    cannibalization = _detect_cannibalization(html_pages)
    silos = _silo_analysis(clusters, out_links, by_url, start)

    suggestions = _build_suggestions(
        html_pages, by_url, inlinks_by_url, out_links, pagerank, click_depth,
        node_types, orphans, unreachable, clusters, start, anchor_issues,
    )

    health = _health_score(html_pages, inlinks_by_url, click_depth, pagerank,
                           orphans, anchor_issues, silos)

    return {
        "inlinks_by_url": inlinks_by_url,
        "pagerank": pagerank,
        "orphans": orphans,
        "unreachable": unreachable,
        "deep_pages": deep_pages[:40],
        "top_linked": top_linked,
        "sinks": sinks,
        "cannibalization": cannibalization,
        "silos": silos,
        "anchor_issues": anchor_issues,
        "health_score": health,
        "clusters": {
            base: {"pages": len(ps), "cities": sorted({p["city"] for p in ps})}
            for base, ps in clusters.items()
        },
        "suggestions": suggestions,
    }


def _anchor_analysis(pages: list[dict], by_url: dict) -> dict:
    generic, over_optimized = [], []
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
                over_optimized.append({"to": tgt, "anchor": top_anchor,
                                       "ratio": round(top_count / total * 100), "total": total})
    return {"generic": generic[:40], "over_optimized": over_optimized[:20]}


def _build_suggestions(pages, by_url, inlinks, out_links, pagerank, click_depth,
                       node_types, orphans, unreachable, clusters, start, anchor_issues):
    """Suggestions de liens SCORÉES par impact estimé."""
    linked_pairs = {(u, v) for u in out_links for v in out_links[u]}
    max_words = max((p.get("word_count", 0) for p in pages), default=1) or 1
    # Résout l'accueil vers l'URL réellement crawlée (avec ou sans / final)
    home = next((u for u in by_url if u.rstrip("/") == start), start)
    donor_budget: dict[str, int] = defaultdict(int)   # cap de liens ajoutés par donneur

    def already(a, b):
        return (a, b) in linked_pairs

    def potential(b):
        p = by_url.get(b, {})
        wn = p.get("word_count", 0) / max_words
        return wn * (1 - pagerank.get(b, 0) / 100)

    def donor_power(a):
        return pagerank.get(a, 0) / (len(out_links.get(a, ())) + 1)

    def relevance(a, b):
        pa, pb = by_url.get(a, {}), by_url.get(b, {})
        r = 0.3
        if _service_base(pa) and _service_base(pa) == _service_base(pb):
            r += 0.5
        if pa.get("city") and pa.get("city") == pb.get("city"):
            r += 0.2
        if pa.get("language") and pb.get("language") and pa["language"] != pb["language"]:
            r = 0.0    # jamais entre langues différentes
        return r

    def urgency(b):
        d = click_depth.get(b)
        u = 1 + ((d or 0) / 4)
        if b in orphans or b in unreachable:
            u += 2
        return u

    def best_donor(b):
        """Donneur maximisant l'impact : fort PageRank, faible profondeur,
        thématiquement proche, pas déjà lié, budget dispo."""
        cands = []
        for a in pages:
            au = a["url"]
            if au == b or already(au, b) or donor_budget[au] >= 8:
                continue
            if a.get("content_type") != "text/html" or a.get("status", 0) >= 400:
                continue
            rel = relevance(au, b)
            if rel <= 0:
                continue
            score = donor_power(au) * rel / (1 + (click_depth.get(au) or 0) / 3)
            cands.append((score, au))
        if not cands:
            return ""
        return max(cands, key=lambda x: x[0])[1]

    raw = []
    # Cibles prioritaires : orphelines, inatteignables, profondes, sous-maillées à potentiel
    targets = set(orphans) | set(unreachable)
    for p in pages:
        u = p["url"]
        if u.rstrip("/") == start:
            continue
        if (click_depth.get(u) or 0) >= 4 or (inlinks.get(u, 0) <= 2 and p.get("word_count", 0) >= 300):
            targets.add(u)

    for b in targets:
        pb = by_url.get(b, {})
        donor = best_donor(b) or (home if not already(home, b) else "")
        if not donor:
            continue
        impact = potential(b) * donor_power(donor) * max(relevance(donor, b), 0.1) * urgency(b)
        donor_budget[donor] += 1
        reason_bits = []
        if b in orphans:
            reason_bits.append("page orpheline (0 lien entrant)")
        elif b in unreachable:
            reason_bits.append("page inatteignable depuis l'accueil")
        elif (click_depth.get(b) or 0) >= 4:
            reason_bits.append(f"page profonde ({click_depth.get(b)} clics)")
        else:
            reason_bits.append(f"fort potentiel ({pb.get('word_count', 0)} mots, PR {pagerank.get(b, 0)})")
        if _service_base(by_url.get(donor, {})) and _service_base(by_url.get(donor, {})) == _service_base(pb):
            reason_bits.append("même cluster service")
        raw.append({
            "from": donor, "to": b, "anchor": _suggest_anchor(pb),
            "impact": round(impact * 100, 1),
            "reason": " ; ".join(reason_bits) + ".",
        })

    raw.sort(key=lambda x: -x["impact"])
    # Priorité affichée dérivée de l'impact (terciles)
    if raw:
        hi = raw[max(0, len(raw) // 5 - 1)]["impact"] if len(raw) > 5 else raw[0]["impact"]
        mid = raw[max(0, len(raw) // 2 - 1)]["impact"]
        for s in raw:
            s["priority"] = "Haute" if s["impact"] >= hi else ("Moyenne" if s["impact"] >= mid else "Basse")

    # Ancres sur-optimisées → varier
    for oo in anchor_issues.get("over_optimized", [])[:8]:
        raw.append({"priority": "Moyenne", "impact": 0, "from": "(plusieurs pages)",
                    "to": oo["to"], "anchor": "Varier les ancres",
                    "reason": f"{oo['ratio']}% des liens vers cette page utilisent la même ancre "
                              f"« {oo['anchor']} » — diversifier (sur-optimisation)."})
    return raw[:150]


def _suggest_anchor(page: dict) -> str:
    h1 = (page.get("h1") or "").strip()
    if h1 and len(h1) <= 70:
        return h1
    title = (page.get("title") or "").split("|")[0].split("-")[0].strip()
    if title:
        return title[:70]
    return (page.get("city") or "cette page").strip()


def _health_score(html_pages, inlinks, click_depth, pagerank, orphans, anchor_issues, silos) -> dict:
    """Score de santé du maillage 0-100 (agrège plusieurs signaux)."""
    n = len(html_pages) or 1
    shallow = sum(1 for p in html_pages if 0 <= (click_depth.get(p["url"]) or 99) <= 3)
    non_orphan = n - len([o for o in orphans])
    total_anchors = sum(len(p.get("anchors_out", [])) for p in html_pages) or 1
    generic = len(anchor_issues.get("generic", []))
    good_silos = sum(1 for s in silos if s["cohesion"] != "faible")
    parts = {
        "profondeur": round(shallow / n * 100),          # % pages à ≤3 clics
        "non_orphelines": round(non_orphan / n * 100),
        "ancres_non_generiques": round(max(0, 1 - generic / total_anchors) * 100),
        "silos_cohesifs": round(good_silos / len(silos) * 100) if silos else 100,
    }
    score = round(sum(parts.values()) / len(parts))
    return {"score": score, "parts": parts,
            "verdict": "excellent" if score >= 80 else ("correct" if score >= 60 else "à améliorer")}
