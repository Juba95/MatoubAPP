"""
Crawler SEO (mini Screaming Frog) — 100 % gratuit (httpx + BeautifulSoup).

Crawle un site en restant sur le domaine, extrait pour chaque page les
signaux SEO (status, title, meta description, H1, Hn, canonical, robots,
liens internes/externes, nombre de mots), détecte la ville d'une page à
partir de la base de communes, vérifie la cohérence Title/H1/ville, mesure
la proximité de contenu entre pages, puis analyse le maillage interne
(pages orphelines, inlinks, ancres) avec des suggestions adaptées à une
stratégie « site service + géolocalisé ».
"""
import csv
import os
import re
import unicodedata
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin, urlparse, urldefrag

import httpx
from bs4 import BeautifulSoup

USER_AGENT = (
    "Mozilla/5.0 (compatible; MatoubBot/1.0; +https://matoub.app) "
    "SEO-Crawler"
)

_ASSET_RE = re.compile(r"\.(jpe?g|png|gif|svg|webp|ico|css|js|pdf|zip|mp4|mp3|woff2?|ttf|xml|json)(\?|$)", re.I)
_WORD_RE = re.compile(r"[a-zà-ÿ0-9]+", re.I)


def _norm(text: str) -> str:
    """Minuscule ASCII sans accents."""
    text = unicodedata.normalize("NFKD", text or "")
    text = text.encode("ascii", "ignore").decode("ascii")
    return text.lower().strip()


# ---------------------------------------------------------------------------
# Base de villes (détection de ville dans les pages)
# ---------------------------------------------------------------------------

_CITY_CACHE: dict | None = None


def _villes_csv_path() -> str | None:
    candidates = [
        os.path.join(os.path.dirname(__file__), "..", "..", "..", "data", "villes_france_premium.csv"),
        os.path.join(os.path.dirname(__file__), "..", "..", "data", "villes_france_premium.csv"),
        "/app/data/villes_france_premium.csv",
        os.path.join(os.path.dirname(__file__), "..", "..", "villes_france.csv"),
    ]
    return next((p for p in candidates if os.path.exists(p)), None)


def _load_cities() -> dict:
    """Retourne {slug_norm: NomVille}. Ignore les villes au nom trop court/commun."""
    global _CITY_CACHE
    if _CITY_CACHE is not None:
        return _CITY_CACHE
    cities: dict[str, str] = {}
    path = _villes_csv_path()
    if path:
        try:
            with open(path, encoding="utf-8") as f:
                for row in csv.reader(f):
                    try:
                        display = row[5]           # "Lyon", "Saint-Étienne"
                        slug = _norm(row[2])       # "lyon", "saint-etienne"
                    except IndexError:
                        continue
                    if len(slug) < 4:              # évite "y", "eu"... faux positifs
                        continue
                    cities.setdefault(slug, display)
        except Exception:
            pass
    _CITY_CACHE = cities
    return cities


def detect_city(url: str, h1: str, title: str) -> str:
    """Détecte la ville d'une page (priorité : slug d'URL, puis H1, puis title)."""
    cities = _load_cities()
    if not cities:
        return ""
    # 1. Depuis le slug d'URL (le plus fiable pour les pages géoloc)
    path = _norm(urlparse(url).path)
    path = re.sub(r"\.(html?|php|aspx?)$", "", path)   # retire l'extension éventuelle
    tokens = [t for t in re.split(r"[-/_]", path) if t]
    # bigrammes puis unigrammes (pour "saint-etienne")
    for size in (3, 2, 1):
        for i in range(len(tokens) - size + 1):
            cand = "-".join(tokens[i:i + size])
            if cand in cities:
                return cities[cand]
    # 2. Depuis H1 puis title
    for text in (h1, title):
        norm = _norm(text)
        words = [w for w in re.split(r"[\s,|/–—-]+", norm) if len(w) >= 4]
        for size in (3, 2, 1):
            for i in range(len(words) - size + 1):
                cand = "-".join(words[i:i + size])
                if cand in cities:
                    return cities[cand]
    return ""


def _shingles(text: str, n: int = 5) -> frozenset:
    words = _WORD_RE.findall(text.lower())
    return frozenset(
        hash(" ".join(words[i:i + n])) for i in range(max(0, len(words) - n + 1))
    )


# ---------------------------------------------------------------------------
# Crawler
# ---------------------------------------------------------------------------


class SiteCrawler:
    """Crawle un site et produit un rapport SEO par page + analyse de maillage."""

    def __init__(
        self,
        start_url: str,
        max_pages: int = 300,
        max_workers: int = 8,
        include_sitemap: bool = True,
        sitemap_url: str = "",
        timeout: float = 15.0,
        progress=None,
    ):
        if not start_url.startswith(("http://", "https://")):
            start_url = "https://" + start_url
        self.start_url = start_url.rstrip("/")
        parsed = urlparse(self.start_url)
        self.scheme = parsed.scheme
        self.host = parsed.netloc.lower()
        self.root_host = self.host[4:] if self.host.startswith("www.") else self.host
        self.max_pages = max(1, min(max_pages, 2000))
        self.max_workers = max(1, min(max_workers, 16))
        self.include_sitemap = include_sitemap
        self.sitemap_url = sitemap_url.strip()
        self.timeout = timeout
        self.progress = progress or (lambda done, total: None)

        self.pages: dict[str, dict] = {}      # url -> page data
        self._seen: set[str] = set()
        self._queue: deque[str] = deque()

    # -- helpers URL -------------------------------------------------------

    def _same_site(self, url: str) -> bool:
        h = urlparse(url).netloc.lower()
        h = h[4:] if h.startswith("www.") else h
        return h == self.root_host

    def _clean(self, url: str) -> str:
        url, _ = urldefrag(url)          # retire #ancre
        return url.rstrip("/") or url

    # -- sitemap -----------------------------------------------------------

    def _discover_sitemap_urls(self, client: httpx.Client) -> list[str]:
        urls: list[str] = []
        candidates = [self.sitemap_url] if self.sitemap_url else [
            f"{self.scheme}://{self.host}/sitemap.xml",
            f"{self.scheme}://{self.host}/sitemap_index.xml",
        ]
        # robots.txt peut pointer un sitemap
        try:
            r = client.get(f"{self.scheme}://{self.host}/robots.txt")
            if r.status_code == 200:
                for m in re.findall(r"(?i)sitemap:\s*(\S+)", r.text):
                    candidates.append(m.strip())
        except Exception:
            pass

        to_fetch = list(dict.fromkeys(c for c in candidates if c))
        seen_sm: set[str] = set()
        while to_fetch and len(urls) < self.max_pages * 2:
            sm = to_fetch.pop(0)
            if sm in seen_sm:
                continue
            seen_sm.add(sm)
            try:
                r = client.get(sm)
                if r.status_code != 200:
                    continue
                locs = re.findall(r"<loc>\s*(.*?)\s*</loc>", r.text, re.I | re.S)
                if "<sitemapindex" in r.text.lower():
                    to_fetch.extend(loc.strip() for loc in locs)   # index → sous-sitemaps
                else:
                    urls.extend(loc.strip() for loc in locs if self._same_site(loc.strip()))
            except Exception:
                continue
        return list(dict.fromkeys(urls))

    # -- fetch + parse -----------------------------------------------------

    def _fetch(self, client: httpx.Client, url: str) -> dict:
        data = {
            "url": url, "status": 0, "title": "", "title_len": 0,
            "meta_description": "", "desc_len": 0, "h1": "", "h1_count": 0,
            "h2_count": 0, "h3_count": 0, "hn": [], "word_count": 0,
            "canonical": "", "meta_robots": "", "indexable": True,
            "internal_links": [], "external_links": 0, "anchors_out": [],
            "content_type": "", "error": "",
        }
        try:
            r = client.get(url)
        except Exception as exc:
            data["error"] = str(exc)[:200]
            return data
        data["status"] = r.status_code
        data["content_type"] = r.headers.get("content-type", "").split(";")[0]
        if r.status_code >= 400 or "text/html" not in data["content_type"]:
            data["indexable"] = False
            return data

        soup = BeautifulSoup(r.text, "html.parser")

        title_tag = soup.find("title")
        data["title"] = title_tag.get_text(strip=True) if title_tag else ""
        data["title_len"] = len(data["title"])

        md = soup.find("meta", attrs={"name": re.compile(r"^description$", re.I)})
        if md and md.get("content"):
            data["meta_description"] = md["content"].strip()
            data["desc_len"] = len(data["meta_description"])

        h1s = soup.find_all("h1")
        data["h1"] = h1s[0].get_text(strip=True) if h1s else ""
        data["h1_count"] = len(h1s)
        data["h2_count"] = len(soup.find_all("h2"))
        data["h3_count"] = len(soup.find_all("h3"))
        data["hn"] = [
            {"level": int(h.name[1]), "text": h.get_text(strip=True)[:120]}
            for h in soup.find_all(["h1", "h2", "h3", "h4"])
            if h.get_text(strip=True)
        ][:60]

        can = soup.find("link", attrs={"rel": re.compile(r"canonical", re.I)})
        if can and can.get("href"):
            data["canonical"] = urljoin(url, can["href"].strip())

        mr = soup.find("meta", attrs={"name": re.compile(r"^robots$", re.I)})
        if mr and mr.get("content"):
            data["meta_robots"] = mr["content"].strip().lower()
            if "noindex" in data["meta_robots"]:
                data["indexable"] = False

        # Contenu texte (retire nav/footer/script/style pour le word count)
        body = BeautifulSoup(r.text, "html.parser")
        for tag in body(["script", "style", "nav", "footer", "header", "aside", "noscript"]):
            tag.decompose()
        text = body.get_text(" ", strip=True)
        data["word_count"] = len(_WORD_RE.findall(text))
        data["_text"] = text[:20000]     # pour la proximité (non exposé)

        internal, external = [], 0
        anchors = []
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if href.startswith(("mailto:", "tel:", "javascript:", "#")):
                continue
            full = self._clean(urljoin(url, href))
            if not full.startswith(("http://", "https://")):
                continue
            if _ASSET_RE.search(full):
                continue
            if self._same_site(full):
                internal.append(full)
                anchors.append({"to": full, "anchor": a.get_text(strip=True)[:80]})
            else:
                external += 1
        data["internal_links"] = list(dict.fromkeys(internal))
        data["external_links"] = external
        data["anchors_out"] = anchors

        # Détection ville + cohérences
        city = detect_city(url, data["h1"], data["title"])
        data["city"] = city
        data["title_h1_match"] = _title_h1_similar(data["title"], data["h1"])
        data["city_in_title"] = bool(city) and _norm(city) in _norm(data["title"])
        data["city_in_h1"] = bool(city) and _norm(city) in _norm(data["h1"])
        return data

    # -- boucle de crawl ---------------------------------------------------

    def run(self) -> dict:
        headers = {"User-Agent": USER_AGENT, "Accept-Language": "fr-FR,fr;q=0.9"}
        with httpx.Client(follow_redirects=True, timeout=self.timeout, headers=headers) as client:
            # Amorçage
            seeds = [self.start_url]
            if self.include_sitemap:
                seeds += self._discover_sitemap_urls(client)
            for s in seeds:
                cs = self._clean(s)
                if cs not in self._seen and self._same_site(cs):
                    self._seen.add(cs)
                    self._queue.append(cs)

            # BFS parallélisé par vagues
            with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
                while self._queue and len(self.pages) < self.max_pages:
                    batch = []
                    while self._queue and len(batch) < self.max_workers and \
                            len(self.pages) + len(batch) < self.max_pages:
                        batch.append(self._queue.popleft())
                    futures = {pool.submit(self._fetch, client, u): u for u in batch}
                    for fut in as_completed(futures):
                        page = fut.result()
                        self.pages[page["url"]] = page
                        # Enfiler les nouveaux liens internes
                        for link in page.get("internal_links", []):
                            if link not in self._seen and len(self._seen) < self.max_pages * 3:
                                self._seen.add(link)
                                self._queue.append(link)
                    self.progress(len(self.pages), min(self.max_pages, len(self._seen)))

        self._compute_proximity()
        return self._report()

    # -- proximité de contenu ---------------------------------------------

    def _compute_proximity(self):
        html_pages = [
            p for p in self.pages.values()
            if p.get("status", 0) < 400 and p.get("_text") and p.get("word_count", 0) > 50
        ]
        sample = html_pages[:300]   # borne l'O(n²)
        shingles = {p["url"]: _shingles(p.pop("_text")) for p in sample}
        # retire _text des pages non échantillonnées
        for p in self.pages.values():
            p.pop("_text", None)
        urls = list(shingles.keys())
        for p in self.pages.values():
            p["max_similarity"] = 0
            p["closest_page"] = ""
        for i, u1 in enumerate(urls):
            s1 = shingles[u1]
            if not s1:
                continue
            best, best_url = 0.0, ""
            for j, u2 in enumerate(urls):
                if i == j:
                    continue
                s2 = shingles[u2]
                if not s2:
                    continue
                inter = len(s1 & s2)
                if not inter:
                    continue
                jac = inter / len(s1 | s2)
                if jac > best:
                    best, best_url = jac, u2
            self.pages[u1]["max_similarity"] = round(best * 100)
            self.pages[u1]["closest_page"] = best_url

    # -- rapport final -----------------------------------------------------

    def _report(self) -> dict:
        from app.services.internal_linking import analyze_internal_linking

        pages = list(self.pages.values())
        for p in pages:
            p["internal_out"] = len(p.get("internal_links", []))
            p["issues"] = _page_issues(p)
        maillage = analyze_internal_linking(pages, self.start_url)
        # Injecte les inlinks calculés par le maillage dans chaque page
        inlinks = maillage.get("inlinks_by_url", {})
        for p in pages:
            p["inlinks"] = inlinks.get(p["url"], 0)

        html_pages = [p for p in pages if p.get("content_type") == "text/html"]
        summary = {
            "start_url": self.start_url,
            "total_pages": len(pages),
            "html_pages": len(html_pages),
            "errors_4xx_5xx": sum(1 for p in pages if p.get("status", 0) >= 400),
            "noindex": sum(1 for p in html_pages if not p.get("indexable", True)),
            "missing_title": sum(1 for p in html_pages if not p.get("title")),
            "missing_desc": sum(1 for p in html_pages if not p.get("meta_description")),
            "missing_h1": sum(1 for p in html_pages if not p.get("h1")),
            "multiple_h1": sum(1 for p in html_pages if p.get("h1_count", 0) > 1),
            "title_h1_mismatch": sum(1 for p in html_pages if p.get("h1") and not p.get("title_h1_match")),
            "duplicate_content": sum(1 for p in html_pages if p.get("max_similarity", 0) >= 90),
            "city_pages": sum(1 for p in html_pages if p.get("city")),
            "city_missing_in_title": sum(1 for p in html_pages if p.get("city") and not p.get("city_in_title")),
            "orphan_pages": len(maillage.get("orphans", [])),
            "pages_with_issues": sum(1 for p in pages if p.get("issues")),
        }
        return {"summary": summary, "pages": pages, "maillage": maillage}


def _title_h1_similar(title: str, h1: str) -> bool:
    """Le title et le H1 partagent-ils l'essentiel de leurs mots ?"""
    if not title or not h1:
        return False
    tw = set(w for w in _WORD_RE.findall(_norm(title)) if len(w) > 2)
    hw = set(w for w in _WORD_RE.findall(_norm(h1)) if len(w) > 2)
    if not tw or not hw:
        return False
    return len(tw & hw) / len(hw) >= 0.5


def _page_issues(p: dict) -> list[str]:
    """Liste des problèmes SEO d'une page (labels courts)."""
    issues = []
    st = p.get("status", 0)
    if st >= 400:
        issues.append(f"HTTP {st}")
        return issues
    if p.get("content_type") != "text/html":
        return issues
    if not p.get("title"):
        issues.append("Title manquant")
    elif p["title_len"] > 65:
        issues.append("Title trop long")
    elif p["title_len"] < 20:
        issues.append("Title trop court")
    if not p.get("meta_description"):
        issues.append("Meta description manquante")
    elif p["desc_len"] > 160:
        issues.append("Meta desc trop longue")
    if not p.get("h1"):
        issues.append("H1 manquant")
    if p.get("h1_count", 0) > 1:
        issues.append("H1 multiples")
    if p.get("h1") and not p.get("title_h1_match"):
        issues.append("Title ≠ H1")
    if not p.get("indexable", True):
        issues.append("Noindex")
    if p.get("word_count", 0) < 250 and p.get("content_type") == "text/html":
        issues.append("Contenu léger (<250 mots)")
    if p.get("max_similarity", 0) >= 90:
        issues.append(f"Quasi-doublon ({p['max_similarity']}%)")
    if p.get("city") and not p.get("city_in_title"):
        issues.append("Ville absente du Title")
    if p.get("city") and not p.get("city_in_h1"):
        issues.append("Ville absente du H1")
    return issues


# ---------------------------------------------------------------------------
# Export Excel
# ---------------------------------------------------------------------------


def build_crawl_excel(report: dict) -> bytes:
    import io
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment

    wb = openpyxl.Workbook()

    # Feuille 1 : pages (vue Screaming Frog)
    ws = wb.active
    ws.title = "Pages"
    headers = [
        "URL", "Status", "Type", "Indexable", "Title", "Long. Title",
        "Meta description", "Long. desc", "H1", "Nb H1", "Nb H2", "Nb H3",
        "Mots", "Canonical", "Meta robots", "Ville détectée",
        "Ville dans Title", "Title=H1", "Liens internes (out)", "Inlinks",
        "Similarité max %", "Page la + proche", "Problèmes",
    ]
    _write_header(ws, headers)
    for p in report["pages"]:
        ws.append([
            p["url"], p.get("status"), p.get("content_type"),
            "Oui" if p.get("indexable", True) else "Non",
            p.get("title", ""), p.get("title_len", 0),
            p.get("meta_description", ""), p.get("desc_len", 0),
            p.get("h1", ""), p.get("h1_count", 0), p.get("h2_count", 0), p.get("h3_count", 0),
            p.get("word_count", 0), p.get("canonical", ""), p.get("meta_robots", ""),
            p.get("city", ""), "Oui" if p.get("city_in_title") else ("Non" if p.get("city") else ""),
            "Oui" if p.get("title_h1_match") else "Non",
            p.get("internal_out", 0), p.get("inlinks", 0),
            p.get("max_similarity", 0), p.get("closest_page", ""),
            " | ".join(p.get("issues", [])),
        ])

    # Feuille 2 : maillage — pages orphelines + top inlinks
    m = report.get("maillage", {})
    ws2 = wb.create_sheet("Maillage")
    _write_header(ws2, ["Type", "URL", "Inlinks", "Détail"])
    for o in m.get("orphans", []):
        ws2.append(["Orpheline", o, 0, "Aucun lien interne entrant"])
    for t in m.get("top_linked", []):
        ws2.append(["Top inlinks", t["url"], t["inlinks"], ""])

    # Feuille 3 : suggestions de maillage
    ws3 = wb.create_sheet("Suggestions")
    _write_header(ws3, ["Priorité", "De (page)", "Vers (page)", "Ancre suggérée", "Raison"])
    for s in m.get("suggestions", []):
        ws3.append([s.get("priority", ""), s.get("from", ""), s.get("to", ""),
                    s.get("anchor", ""), s.get("reason", "")])

    # Largeurs
    for sheet in wb.worksheets:
        for col in sheet.columns:
            letter = col[0].column_letter
            sheet.column_dimensions[letter].width = 22

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _write_header(ws, headers):
    from openpyxl.styles import Font, PatternFill, Alignment
    font = Font(bold=True, color="FFFFFF", size=11)
    fill = PatternFill(start_color="2E86C1", end_color="2E86C1", fill_type="solid")
    for i, h in enumerate(headers, 1):
        c = ws.cell(row=1, column=i, value=h)
        c.font = font
        c.fill = fill
        c.alignment = Alignment(horizontal="center")
    ws.freeze_panes = "A2"
