"""
Analyse GEO (Generative Engine Optimization) — visibilite IA d'un site.
Combine l'analyse technique locale avec les donnees DataForSEO AI Optimization API
pour fournir des metriques de visibilite IA de niveau Semrush.
"""
import httpx
import json
import re
import base64
from collections import Counter
from bs4 import BeautifulSoup
from app.config import get_settings
from app.services.dataforseo import DataForSEOClient


AI_CRAWLERS = {
    "GPTBot": "OpenAI / ChatGPT",
    "ChatGPT-User": "ChatGPT browse",
    "OAI-SearchBot": "OpenAI Search",
    "ClaudeBot": "Anthropic / Claude",
    "anthropic-ai": "Anthropic",
    "PerplexityBot": "Perplexity AI",
    "Bytespider": "TikTok / ByteDance",
    "CCBot": "Common Crawl",
    "Google-Extended": "Google Gemini / Bard",
    "Googlebot": "Google Search + AIO",
    "Bingbot": "Bing / Copilot",
    "FacebookBot": "Meta AI",
    "Applebot-Extended": "Apple Intelligence",
    "cohere-ai": "Cohere",
}


class GEOAnalyzer:
    def __init__(self):
        self.settings = get_settings()
        self.client = httpx.Client(
            timeout=30,
            follow_redirects=True,
            headers={"User-Agent": "MatoubeAPP-GEO/1.0"},
        )
        # DataForSEO HTTP client (separate, with auth)
        self._dfs_client = None
        self._dfs_base_url = "https://api.dataforseo.com/v3"

    def _get_dfs_client(self) -> httpx.Client | None:
        """Create an authenticated httpx client for DataForSEO API calls."""
        if not self.settings.dataforseo_login or not self.settings.dataforseo_password:
            return None
        if self._dfs_client is None or self._dfs_client.is_closed:
            creds = base64.b64encode(
                f"{self.settings.dataforseo_login}:{self.settings.dataforseo_password}".encode()
            ).decode()
            proxy = self.settings.proxy_url or None
            self._dfs_client = httpx.Client(
                headers={
                    "Authorization": f"Basic {creds}",
                    "Content-Type": "application/json",
                },
                proxy=proxy,
                timeout=60,
            )
        return self._dfs_client

    def _dfs_post(self, endpoint: str, payload: list[dict]) -> dict | None:
        """POST to a DataForSEO endpoint. Returns parsed JSON or None on error."""
        client = self._get_dfs_client()
        if client is None:
            return None
        try:
            resp = client.post(f"{self._dfs_base_url}{endpoint}", json=payload)
            resp.raise_for_status()
            return resp.json()
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Domain-level AI Visibility Analysis
    # ------------------------------------------------------------------

    def analyze_domain(self, domain: str) -> dict:
        """Full domain-level AI visibility analysis combining DataForSEO
        AI Optimization data with technical analysis of the homepage."""
        domain = domain.strip().lower()
        if domain.startswith("http"):
            domain = domain.split("//")[-1].split("/")[0]
        if domain.startswith("www."):
            domain = domain[4:]

        result = {
            "domain": domain,
            "summary": {
                "total_mentions": 0,
                "total_citations": 0,
                "total_pages_mentioned": 0,
                "ai_visibility_score": 0,
            },
            "mentions_by_platform": [],
            "top_pages": [],
            "top_questions": [],
            "brand_entities": [],
            "competing_domains": [],
            "crawlers": {},
            "technical": {},
            "content": {},
            "schema": {},
            "platform_readiness": {},
            "recommendations": [],
            "semrush": {},
        }

        # --- DataForSEO AI Optimization calls ---
        dfs_target = [{
            "domain": domain,
            "search_filter": "include",
            "search_scope": "any",
            "include_subdomains": True,
        }]

        # A. LLM Mentions Search (Google AIO + ChatGPT)
        google_mentions = self._fetch_llm_mentions_search(dfs_target, "google")
        chatgpt_mentions = self._fetch_llm_mentions_search(dfs_target, "chat_gpt")
        all_mention_items = google_mentions + chatgpt_mentions

        # B. LLM Mentions Aggregated Metrics
        agg_metrics = self._fetch_llm_aggregated_metrics(dfs_target)

        # C. LLM Mentions Top Pages
        top_pages_data = self._fetch_llm_top_pages(dfs_target)

        # --- Build top_questions ---
        for item in all_mention_items:
            answer_raw = item.get("answer", "") or ""
            sources = item.get("sources", []) or []
            brand_ents = item.get("brand_entities", []) or []
            is_cited = any(
                domain in (s.get("domain", "") or "") for s in sources
            )
            result["top_questions"].append({
                "question": item.get("question", ""),
                "answer_preview": answer_raw[:200],
                "platform": item.get("platform", ""),
                "ai_volume": item.get("ai_search_volume", 0) or 0,
                "is_cited": is_cited,
                "brands_count": len(brand_ents),
                "sources_count": len(sources),
            })

        # --- Build brand_entities (aggregate across all mentions) ---
        entity_counter: dict[str, dict] = {}
        for item in all_mention_items:
            for ent in item.get("brand_entities", []) or []:
                title = ent.get("title", "")
                if not title:
                    continue
                if title not in entity_counter:
                    entity_counter[title] = {
                        "title": title,
                        "category": ent.get("category", ""),
                        "count": 0,
                    }
                entity_counter[title]["count"] += 1
        result["brand_entities"] = sorted(
            entity_counter.values(), key=lambda x: x["count"], reverse=True
        )

        # --- Build competing_domains (domains cited alongside ours) ---
        domain_counter: Counter = Counter()
        total_citations = 0
        for item in all_mention_items:
            for src in item.get("sources", []) or []:
                src_domain = src.get("domain", "") or ""
                if src_domain and domain in src_domain:
                    total_citations += 1
                elif src_domain:
                    domain_counter[src_domain] += 1
        result["competing_domains"] = [
            {"domain": d, "mentions": c}
            for d, c in domain_counter.most_common(30)
        ]

        # --- Aggregated metrics ---
        if agg_metrics:
            result["summary"]["total_mentions"] = agg_metrics.get("total_count", 0) or 0
            # mentions_by_platform from grouping
            platform_groups = agg_metrics.get("platform", []) or []
            total_m = result["summary"]["total_mentions"] or 1
            for pg in platform_groups:
                name = pg.get("name", pg.get("platform", ""))
                count = pg.get("count", 0) or 0
                result["mentions_by_platform"].append({
                    "platform": name,
                    "mentions": count,
                    "pct": round(count / total_m * 100, 1) if total_m else 0,
                })
        else:
            # Fallback: build from search results
            plat_counter: Counter = Counter()
            for item in all_mention_items:
                plat_counter[item.get("platform", "unknown")] += 1
            total_m = sum(plat_counter.values()) or 1
            result["summary"]["total_mentions"] = total_m
            for plat, cnt in plat_counter.most_common():
                result["mentions_by_platform"].append({
                    "platform": plat,
                    "mentions": cnt,
                    "pct": round(cnt / total_m * 100, 1),
                })

        # --- Top pages ---
        if top_pages_data:
            result["top_pages"] = top_pages_data
            result["summary"]["total_pages_mentioned"] = len(top_pages_data)
        else:
            # Fallback: count from sources in search results
            page_counter: Counter = Counter()
            page_cite_counter: Counter = Counter()
            for item in all_mention_items:
                for src in item.get("sources", []) or []:
                    src_domain = src.get("domain", "") or ""
                    if domain in src_domain:
                        url = src.get("url", src_domain)
                        page_counter[url] += 1
                        page_cite_counter[url] += 1
            result["top_pages"] = [
                {"url": u, "mentions": c, "citations": page_cite_counter.get(u, 0)}
                for u, c in page_counter.most_common(20)
            ]
            result["summary"]["total_pages_mentioned"] = len(result["top_pages"])

        result["summary"]["total_citations"] = total_citations

        # --- Technical analysis of homepage ---
        homepage_url = f"https://{domain}"
        try:
            resp = self.client.get(homepage_url)
            html = resp.text
            soup = BeautifulSoup(html, "lxml")
            result["technical"]["status_code"] = resp.status_code
            result["technical"]["https"] = True
            result["technical"]["response_time_ms"] = int(
                resp.elapsed.total_seconds() * 1000
            )
            result["technical"].update(self._analyze_technical(resp, soup))
            result["content"] = self._analyze_content(soup)
            result["schema"] = self._analyze_schema(soup)
        except Exception as e:
            result["technical"]["error"] = str(e)

        # Crawlers
        result["crawlers"] = self._analyze_crawlers(domain)

        # llms.txt
        result["technical"]["llms_txt"] = self._check_llms_txt(domain)

        # Platform readiness
        result["platform_readiness"] = self._score_platforms({
            "technical": result["technical"],
            "content": result["content"],
            "schema": result["schema"],
            "ai_crawlers": result["crawlers"],
        })

        # AI Visibility Score (composite 0-100)
        result["summary"]["ai_visibility_score"] = self._compute_ai_visibility_score(
            result
        )

        # Recommendations
        result["recommendations"] = self._generate_recommendations({
            "scores": {
                "geo_score": result["summary"]["ai_visibility_score"],
            },
            "content": result["content"],
            "schema": result["schema"],
            "technical": result["technical"],
            "ai_crawlers": result["crawlers"],
        })

        # --- Semrush data ---
        result["semrush"] = self._fetch_semrush_data(domain)

        return result

    # ------------------------------------------------------------------
    # DataForSEO API Fetchers
    # ------------------------------------------------------------------

    def _fetch_llm_mentions_search(
        self, target: list[dict], platform: str
    ) -> list[dict]:
        """Call LLM Mentions Search endpoint for a given platform.
        Returns a list of mention items."""
        data = self._dfs_post(
            "/ai_optimization/llm_mentions/search/live",
            [{
                "target": target,
                "platform": platform,
                "location_code": 2250,
                "language_code": "fr",
                "limit": 50,
            }],
        )
        if not data:
            return []
        try:
            tasks = data.get("tasks", [])
            if not tasks:
                return []
            result_list = tasks[0].get("result", [])
            if not result_list:
                return []
            return result_list[0].get("items", []) or []
        except Exception:
            return []

    def _fetch_llm_aggregated_metrics(self, target: list[dict]) -> dict | None:
        """Call LLM Mentions Aggregated Metrics endpoint.
        Returns the first result item or None."""
        data = self._dfs_post(
            "/ai_optimization/llm_mentions/aggregated_metrics/live",
            [{
                "target": target,
                "location_code": 2250,
                "language_code": "fr",
                "internal_list_limit": 20,
            }],
        )
        if not data:
            return None
        try:
            tasks = data.get("tasks", [])
            if not tasks:
                return None
            result_list = tasks[0].get("result", [])
            if not result_list:
                return None
            return result_list[0]
        except Exception:
            return None

    def _fetch_llm_top_pages(self, target: list[dict]) -> list[dict]:
        """Call LLM Mentions Top Pages endpoint.
        Returns a list of page dicts with url, mentions, citations."""
        data = self._dfs_post(
            "/ai_optimization/llm_mentions/top_pages/live",
            [{
                "target": target,
                "location_code": 2250,
                "language_code": "fr",
                "limit": 20,
            }],
        )
        if not data:
            return []
        try:
            tasks = data.get("tasks", [])
            if not tasks:
                return []
            result_list = tasks[0].get("result", [])
            if not result_list:
                return []
            items = result_list[0].get("items", []) or []
            pages = []
            for item in items:
                pages.append({
                    "url": item.get("page", item.get("url", "")),
                    "mentions": item.get("count", item.get("mentions", 0)) or 0,
                    "citations": item.get("citations", item.get("citation_count", 0)) or 0,
                })
            return pages
        except Exception:
            return []

    # ------------------------------------------------------------------
    # AI Visibility Score
    # ------------------------------------------------------------------

    def _compute_ai_visibility_score(self, result: dict) -> int:
        """Compute a composite 0-100 AI visibility score."""
        score = 0.0

        # Mentions volume (40 points max)
        total_mentions = result["summary"]["total_mentions"]
        if total_mentions >= 500:
            score += 40
        elif total_mentions >= 100:
            score += 30
        elif total_mentions >= 50:
            score += 20
        elif total_mentions >= 10:
            score += 10
        elif total_mentions >= 1:
            score += 5

        # Citations (20 points max)
        total_citations = result["summary"]["total_citations"]
        if total_citations >= 50:
            score += 20
        elif total_citations >= 20:
            score += 15
        elif total_citations >= 5:
            score += 10
        elif total_citations >= 1:
            score += 5

        # Platform diversity (10 points max)
        platforms_present = len(result["mentions_by_platform"])
        score += min(platforms_present * 2.5, 10)

        # Pages mentioned (10 points max)
        pages = result["summary"]["total_pages_mentioned"]
        if pages >= 10:
            score += 10
        elif pages >= 5:
            score += 7
        elif pages >= 1:
            score += 3

        # Technical readiness (20 points max)
        pr = result.get("platform_readiness", {})
        if pr:
            plat_scores = [
                v["score"] if isinstance(v, dict) else v
                for v in pr.values()
                if isinstance(v, (int, float, dict))
            ]
            if plat_scores:
                avg_platform = sum(plat_scores) / len(plat_scores)
                score += avg_platform * 0.2

        return min(int(score), 100)

    # ------------------------------------------------------------------
    # URL-level Analysis (original method)
    # ------------------------------------------------------------------

    def analyze(self, url: str) -> dict:
        """Analyse GEO complete d'une URL."""
        result = {
            "url": url,
            "domain": self._extract_domain(url),
            "scores": {},
            "technical": {},
            "ai_crawlers": {},
            "content": {},
            "schema": {},
            "platform_readiness": {},
            "llm_mentions": {},
            "recommendations": [],
        }

        # 1. Fetch la page
        try:
            resp = self.client.get(url)
            html = resp.text
            soup = BeautifulSoup(html, "lxml")
            result["technical"]["status_code"] = resp.status_code
            result["technical"]["https"] = url.startswith("https")
            result["technical"]["response_time_ms"] = int(
                resp.elapsed.total_seconds() * 1000
            )
        except Exception as e:
            result["technical"]["error"] = str(e)
            return result

        # 2. Analyse technique
        result["technical"].update(self._analyze_technical(resp, soup))

        # 3. Analyse contenu
        result["content"] = self._analyze_content(soup)

        # 4. Schema / Structured Data
        result["schema"] = self._analyze_schema(soup)

        # 5. AI Crawlers (robots.txt)
        result["ai_crawlers"] = self._analyze_crawlers(result["domain"])

        # 6. llms.txt
        result["technical"]["llms_txt"] = self._check_llms_txt(result["domain"])

        # 7. Platform readiness
        result["platform_readiness"] = self._score_platforms(result)

        # 8. DataForSEO LLM Mentions
        if self.settings.dataforseo_login:
            result["llm_mentions"] = self._get_llm_mentions(result["domain"])

        # 9. Scores
        result["scores"] = self._compute_scores(result)

        # 10. Recommandations
        result["recommendations"] = self._generate_recommendations(result)

        return result

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _extract_domain(self, url: str) -> str:
        d = url.split("//")[-1].split("/")[0]
        if d.startswith("www."):
            d = d[4:]
        return d

    def _analyze_technical(self, resp, soup) -> dict:
        headers = dict(resp.headers)
        meta_tags = {}
        for meta in soup.find_all("meta"):
            name = meta.get("name", meta.get("property", ""))
            content = meta.get("content", "")
            if name and content:
                meta_tags[name.lower()] = content

        # Security headers
        security = {
            "hsts": "strict-transport-security" in headers,
            "csp": "content-security-policy" in headers,
            "x_frame_options": "x-frame-options" in headers,
        }

        # SSR detection
        html_str = str(resp.text[:5000])
        ssr_signals = {
            "next_js": "__NEXT_DATA__" in html_str or "__next" in html_str,
            "nuxt": "__NUXT__" in html_str or "nuxt" in html_str.lower(),
            "gatsby": "___gatsby" in html_str,
            "wp": "wp-content" in html_str or "wordpress" in html_str.lower(),
        }
        is_ssr = any(ssr_signals.values()) or len(soup.find_all("p")) > 3

        title = soup.title.string.strip() if soup.title and soup.title.string else ""
        meta_desc = meta_tags.get("description", "")

        return {
            "title": title,
            "title_length": len(title),
            "meta_description": meta_desc,
            "meta_desc_length": len(meta_desc),
            "canonical": (soup.find("link", rel="canonical") or {}).get("href"),
            "language": (
                soup.find("html").get("lang", "") if soup.find("html") else ""
            ),
            "security_headers": security,
            "is_ssr": is_ssr,
            "framework": next(
                (k for k, v in ssr_signals.items() if v), "unknown"
            ),
            "og_tags": {k: v for k, v in meta_tags.items() if k.startswith("og:")},
        }

    def _analyze_content(self, soup) -> dict:
        # Clone soup to avoid modifying the original
        soup = BeautifulSoup(str(soup), "lxml")
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()

        text = soup.get_text(separator=" ", strip=True)
        words = text.split()
        word_count = len(words)

        # Structure
        headings = {}
        for level in range(1, 7):
            tags = soup.find_all(f"h{level}")
            if tags:
                headings[f"h{level}"] = [t.get_text(strip=True) for t in tags]

        # Paragraphes
        paragraphs = soup.find_all("p")
        para_count = len(paragraphs)
        avg_para_length = (
            sum(len(p.get_text().split()) for p in paragraphs) / max(para_count, 1)
        )

        # Listes
        lists = len(soup.find_all(["ul", "ol"]))

        # Images
        images = soup.find_all("img")
        images_with_alt = sum(1 for img in images if img.get("alt", "").strip())

        # Liens
        links = soup.find_all("a", href=True)
        internal = sum(1 for a in links if not a["href"].startswith("http"))
        external = len(links) - internal

        # Citability -- blocs qui pourraient etre cites par un LLM
        citable_blocks = 0
        for p in paragraphs:
            t = p.get_text(strip=True)
            w = len(t.split())
            if 20 <= w <= 60 and any(c in t for c in [".", ":", "?"]):
                citable_blocks += 1

        return {
            "word_count": word_count,
            "headings": headings,
            "h_count": sum(len(v) for v in headings.values()),
            "paragraphs": para_count,
            "avg_paragraph_words": round(avg_para_length),
            "lists": lists,
            "images": len(images),
            "images_with_alt": images_with_alt,
            "internal_links": internal,
            "external_links": external,
            "citable_blocks": citable_blocks,
            "citability_ratio": round(
                citable_blocks / max(para_count, 1) * 100
            ),
        }

    def _analyze_schema(self, soup) -> dict:
        schemas = []
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string)
                if isinstance(data, list):
                    schemas.extend(data)
                else:
                    schemas.append(data)
            except (json.JSONDecodeError, TypeError):
                continue

        types = []
        has_org = False
        has_article = False
        has_faq = False
        has_breadcrumb = False
        has_person = False
        has_same_as = False
        has_speakable = False

        for s in schemas:
            t = s.get("@type", "")
            # Normaliser en liste pour gérer @type: "Org" ET @type: ["Org", "Person"]
            type_list = t if isinstance(t, list) else [t]
            types.extend(type_list)
            for single_type in type_list:
                if single_type in ("Organization", "LocalBusiness"):
                    has_org = True
                    if s.get("sameAs"):
                        has_same_as = True
                if single_type in ("Article", "NewsArticle", "BlogPosting"):
                    has_article = True
                    if s.get("speakable"):
                        has_speakable = True
                if single_type == "FAQPage":
                    has_faq = True
                if single_type == "BreadcrumbList":
                    has_breadcrumb = True
                if single_type == "Person":
                    has_person = True

        score = 0
        if schemas:
            score += 20
        if has_org:
            score += 20
        if has_same_as:
            score += 15
        if has_article:
            score += 15
        if has_breadcrumb:
            score += 10
        if has_faq:
            score += 5
        if has_person:
            score += 10
        if has_speakable:
            score += 5

        return {
            "schemas_found": len(schemas),
            "types": list(set(types)),
            "has_organization": has_org,
            "has_same_as": has_same_as,
            "has_article": has_article,
            "has_faq": has_faq,
            "has_breadcrumb": has_breadcrumb,
            "has_person": has_person,
            "has_speakable": has_speakable,
            "schema_score": min(score, 100),
        }

    def _analyze_crawlers(self, domain: str) -> dict:
        result = {}
        try:
            resp = self.client.get(f"https://{domain}/robots.txt")
            if resp.status_code == 200:
                robots = resp.text.lower()
                for crawler, label in AI_CRAWLERS.items():
                    if crawler.lower() in robots:
                        blocked = False
                        lines = robots.split("\n")
                        in_agent = False
                        for line in lines:
                            line = line.strip()
                            if (
                                line.startswith("user-agent:")
                                and crawler.lower() in line
                            ):
                                in_agent = True
                            elif line.startswith("user-agent:") and in_agent:
                                in_agent = False
                            elif in_agent and line.startswith("disallow: /"):
                                blocked = True
                        result[crawler] = {
                            "label": label,
                            "status": "blocked" if blocked else "allowed",
                            "in_robots": True,
                        }
                    else:
                        result[crawler] = {
                            "label": label,
                            "status": "allowed",
                            "in_robots": False,
                        }
            else:
                for crawler, label in AI_CRAWLERS.items():
                    result[crawler] = {
                        "label": label,
                        "status": "allowed",
                        "in_robots": False,
                    }
        except Exception:
            for crawler, label in AI_CRAWLERS.items():
                result[crawler] = {
                    "label": label,
                    "status": "unknown",
                    "in_robots": False,
                }
        return result

    def _check_llms_txt(self, domain: str) -> dict:
        try:
            resp = self.client.get(f"https://{domain}/llms.txt")
            if resp.status_code == 200:
                content = resp.text[:2000]
                return {
                    "exists": True,
                    "length": len(resp.text),
                    "preview": content,
                }
            return {"exists": False}
        except Exception:
            return {"exists": False}

    def _score_platforms(self, result: dict) -> dict:
        tech = result.get("technical", {})
        content = result.get("content", {})
        schema = result.get("schema", {})
        crawlers = result.get("ai_crawlers", {})
        wc = content.get("word_count", 0)
        hc = content.get("h_count", 0)
        cb = content.get("citable_blocks", 0)
        cr = content.get("citability_ratio", 0)
        lists = content.get("lists", 0)
        ext_links = content.get("external_links", 0)
        int_links = content.get("internal_links", 0)
        imgs_alt = content.get("images_with_alt", 0)

        def crawler_ok(name):
            return crawlers.get(name, {}).get("status") == "allowed"

        def build_detail(checks: list[tuple[str, bool, int]]) -> dict:
            """Build score + details from a list of (label, passed, points)."""
            score = 0
            details = []
            for label, passed, pts in checks:
                if passed:
                    score += pts
                details.append({"label": label, "passed": passed, "points": pts})
            return {"score": min(score, 100), "details": details}

        # ── Google AI Overviews ──
        # Poids fort : structured data, contenu long structuré, listes, FAQ
        google = build_detail([
            ("Schema Organization", schema.get("has_organization", False), 15),
            ("Schema Article/BlogPosting", schema.get("has_article", False), 10),
            ("Schema FAQ", schema.get("has_faq", False), 10),
            ("BreadcrumbList", schema.get("has_breadcrumb", False), 5),
            ("Contenu > 800 mots", wc >= 800, 12),
            ("Structure H2+ (3+)", hc >= 3, 10),
            ("Listes structurees (ul/ol)", lists >= 1, 8),
            ("Citabilite > 20%", cr >= 20, 10),
            ("HTTPS actif", tech.get("https", False), 5),
            ("Googlebot autorise", crawler_ok("Googlebot"), 10),
            ("Canonical defini", bool(tech.get("canonical")), 5),
        ])

        # ── ChatGPT ──
        # Poids fort : GPTBot autorisé, blocs citables, sources externes, meta desc
        chatgpt = build_detail([
            ("GPTBot autorise", crawler_ok("GPTBot"), 20),
            ("ChatGPT-User autorise", crawler_ok("ChatGPT-User"), 5),
            ("Blocs citables (5+)", cb >= 5, 15),
            ("Blocs citables (10+)", cb >= 10, 10),
            ("Meta description remplie", bool(tech.get("meta_description")), 10),
            ("Liens externes (2+)", ext_links >= 2, 8),
            ("sameAs (profils sociaux)", schema.get("has_same_as", False), 10),
            ("Contenu > 500 mots", wc >= 500, 7),
            ("Schema Person (auteur)", schema.get("has_person", False), 8),
            ("speakable", schema.get("has_speakable", False), 7),
        ])

        # ── Perplexity ──
        # Poids fort : PerplexityBot, sources/citations, listes, contenu long
        perplexity = build_detail([
            ("PerplexityBot autorise", crawler_ok("PerplexityBot"), 20),
            ("Blocs citables (3+)", cb >= 3, 12),
            ("Blocs citables (8+)", cb >= 8, 8),
            ("Contenu > 1000 mots", wc >= 1000, 12),
            ("Listes structurees (2+)", lists >= 2, 10),
            ("Liens externes (sources)", ext_links >= 1, 10),
            ("Liens externes (3+)", ext_links >= 3, 5),
            ("Schema present", schema.get("schemas_found", 0) >= 1, 8),
            ("Contenu SSR/indexable", tech.get("is_ssr", False), 10),
            ("HTTPS actif", tech.get("https", False), 5),
        ])

        # ── Gemini ──
        # Poids fort : Google-Extended, schema Organization+sameAs, images, E-E-A-T
        gemini = build_detail([
            ("Google-Extended autorise", crawler_ok("Google-Extended"), 18),
            ("Schema Organization", schema.get("has_organization", False), 15),
            ("sameAs (entite verifiee)", schema.get("has_same_as", False), 12),
            ("Schema Person (E-E-A-T)", schema.get("has_person", False), 10),
            ("Images avec alt (2+)", imgs_alt >= 2, 10),
            ("Structure H2+ (4+)", hc >= 4, 8),
            ("Contenu > 800 mots", wc >= 800, 7),
            ("HTTPS actif", tech.get("https", False), 5),
            ("Liens internes (3+)", int_links >= 3, 8),
            ("llms.txt present", tech.get("llms_txt", {}).get("exists", False), 7),
        ])

        # ── Bing Copilot ──
        # Poids fort : Bingbot, meta tags, IndexNow, schema, securite
        bing = build_detail([
            ("Bingbot autorise", crawler_ok("Bingbot"), 18),
            ("Meta description remplie", bool(tech.get("meta_description")), 15),
            ("Title bien dimensionne", 10 <= tech.get("title_length", 0) <= 65, 10),
            ("Schema Organization", schema.get("has_organization", False), 12),
            ("Contenu > 500 mots", wc >= 500, 8),
            ("Structure H2+ (3+)", hc >= 3, 8),
            ("HTTPS actif", tech.get("https", False), 8),
            ("HSTS header", tech.get("security_headers", {}).get("hsts", False), 6),
            ("Open Graph tags", bool(tech.get("og_tags")), 8),
            ("Canonical defini", bool(tech.get("canonical")), 7),
        ])

        return {
            "google_aio": google,
            "chatgpt": chatgpt,
            "perplexity": perplexity,
            "gemini": gemini,
            "bing_copilot": bing,
        }

    # ------------------------------------------------------------------
    # Semrush API
    # ------------------------------------------------------------------

    def _semrush_get(self, report_type: str, params: dict) -> list[dict]:
        """Call Semrush API and parse the semicolon-delimited response."""
        api_key = self.settings.semrush_api_key
        if not api_key:
            return []
        base = "https://api.semrush.com/"
        params = {"type": report_type, "key": api_key, **params}
        try:
            resp = self.client.get(base, params=params)
            if resp.status_code != 200 or "ERROR" in resp.text[:50]:
                return []
            lines = resp.text.strip().split("\n")
            if len(lines) < 2:
                return []
            headers = lines[0].split(";")
            rows = []
            for line in lines[1:]:
                vals = line.split(";")
                if len(vals) == len(headers):
                    rows.append(dict(zip(headers, vals)))
            return rows
        except Exception:
            return []

    def _fetch_semrush_data(self, domain: str) -> dict:
        """Fetch comprehensive Semrush data for a domain.
        Semrush API returns full column names (e.g. 'Organic Keywords')
        not codes (e.g. 'Or'), so we map by actual header names."""
        if not self.settings.semrush_api_key:
            return {}

        data: dict = {
            "domain_overview": {},
            "organic_keywords": [],
            "organic_competitors": [],
            "backlinks_overview": {},
        }

        # 1. Domain Overview
        # Returns: Domain;Rank;Organic Keywords;Organic Traffic (+ others if plan allows)
        overview = self._semrush_get("domain_ranks", {
            "export_columns": "Dn,Rk,Or,Ot,Os,Fk,Fp",
            "domain": domain,
            "database": "fr",
        })
        if overview:
            row = overview[0]
            data["domain_overview"] = {
                "authority_score": self._safe_int(
                    row.get("Rank", row.get("Rk", "0"))
                ),
                "organic_keywords": self._safe_int(
                    row.get("Organic Keywords", row.get("Or", "0"))
                ),
                "organic_traffic": self._safe_int(
                    row.get("Organic Traffic", row.get("Ot", "0"))
                ),
                "organic_cost": self._safe_float(
                    row.get("Organic Cost", row.get("Os", "0"))
                ),
                "paid_keywords": self._safe_int(
                    row.get("Adwords Keywords", row.get("Fk", "0"))
                ),
                "paid_traffic": self._safe_int(
                    row.get("Adwords Traffic", row.get("Fp", "0"))
                ),
            }

        # 2. Top Organic Keywords
        # Returns: Keyword;Position;Search Volume;CPC;Url;Traffic (%);Traffic Cost (%);Competition;Number of Results;Trends
        kw_rows = self._semrush_get("domain_organic", {
            "export_columns": "Ph,Po,Nq,Cp,Ur,Tr,Tc,Co,Nr,Td",
            "domain": domain,
            "database": "fr",
            "display_limit": "30",
            "display_sort": "tr_desc",
        })
        for row in kw_rows:
            data["organic_keywords"].append({
                "keyword": row.get("Keyword", row.get("Ph", "")),
                "position": self._safe_int(
                    row.get("Position", row.get("Po", "0"))
                ),
                "volume": self._safe_int(
                    row.get("Search Volume", row.get("Nq", "0"))
                ),
                "cpc": self._safe_float(
                    row.get("CPC", row.get("Cp", "0"))
                ),
                "url": row.get("Url", row.get("Ur", "")),
                "traffic_pct": self._safe_float(
                    row.get("Traffic (%)", row.get("Tr", "0"))
                ),
                "traffic_cost": self._safe_float(
                    row.get("Traffic Cost (%)", row.get("Tc", "0"))
                ),
                "competition": self._safe_float(
                    row.get("Competition", row.get("Co", "0"))
                ),
                "results": self._safe_int(
                    row.get("Number of Results", row.get("Nr", "0"))
                ),
                "trend": row.get("Trends", row.get("Td", "")),
            })

        # 3. Organic Competitors
        # Returns: Domain;Competitor Relevance;Common Keywords;Organic Keywords;Organic Traffic
        comp_rows = self._semrush_get("domain_organic_organic", {
            "export_columns": "Dn,Cr,Np,Or,Ot,Os",
            "domain": domain,
            "database": "fr",
            "display_limit": "15",
            "display_sort": "np_desc",
        })
        for row in comp_rows:
            data["organic_competitors"].append({
                "domain": row.get("Domain", row.get("Dn", "")),
                "common_keywords": self._safe_int(
                    row.get("Common Keywords", row.get("Np", "0"))
                ),
                "competition_level": self._safe_float(
                    row.get("Competitor Relevance", row.get("Cr", "0"))
                ),
                "organic_keywords": self._safe_int(
                    row.get("Organic Keywords", row.get("Or", "0"))
                ),
                "organic_traffic": self._safe_int(
                    row.get("Organic Traffic", row.get("Ot", "0"))
                ),
                "organic_cost": self._safe_float(
                    row.get("Organic Cost", row.get("Os", "0"))
                ),
            })

        # 4. Backlinks Overview (may not be available on all plans)
        bl_rows = self._semrush_get("backlinks_overview", {
            "export_columns": "total,domains_num,urls_num,ips_num,follows_num,nofollows_num,texts_num,images_num",
            "target": domain,
            "target_type": "root_domain",
        })
        if bl_rows:
            row = bl_rows[0]
            data["backlinks_overview"] = {
                "total_backlinks": self._safe_int(row.get("total", "0")),
                "referring_domains": self._safe_int(row.get("domains_num", "0")),
                "referring_urls": self._safe_int(row.get("urls_num", "0")),
                "referring_ips": self._safe_int(row.get("ips_num", "0")),
                "follow": self._safe_int(row.get("follows_num", "0")),
                "nofollow": self._safe_int(row.get("nofollows_num", "0")),
                "text_links": self._safe_int(row.get("texts_num", "0")),
                "image_links": self._safe_int(row.get("images_num", "0")),
            }

        return data

    @staticmethod
    def _safe_int(val: str) -> int:
        try:
            return int(float(val))
        except (ValueError, TypeError):
            return 0

    @staticmethod
    def _safe_float(val: str) -> float:
        try:
            return round(float(val), 2)
        except (ValueError, TypeError):
            return 0.0

    def _get_llm_mentions(self, domain: str) -> dict:
        """Recupere les mentions LLM via DataForSEO AI Optimization API.
        Used by the URL-level analyze() method."""
        try:
            dfs = DataForSEOClient()
            with dfs._client() as client:
                resp = client.post(
                    f"{dfs.BASE_URL}/ai_optimization/llm_mentions/search/live",
                    json=[{
                        "target": [{
                            "domain": domain,
                            "search_filter": "include",
                            "search_scope": "any",
                        }],
                        "platform": "google",
                        "location_code": 2250,
                        "language_code": "fr",
                        "limit": 20,
                    }],
                )
                data = resp.json()
                tasks = data.get("tasks", [])
                mentions = []
                total = 0
                if tasks:
                    result_data = tasks[0].get("result", [])
                    if result_data:
                        total = result_data[0].get("total_count", 0)
                        for item in result_data[0].get("items", []):
                            mentions.append({
                                "question": item.get("question", ""),
                                "platform": item.get("platform", ""),
                                "ai_volume": item.get("ai_search_volume", 0),
                                "sources_count": len(item.get("sources", [])),
                            })

                return {"total_mentions": total, "mentions": mentions[:10]}
        except Exception as e:
            return {"total_mentions": 0, "mentions": [], "error": str(e)}

    def _compute_scores(self, result: dict) -> dict:
        content = result.get("content", {})
        schema = result.get("schema", {})
        tech = result.get("technical", {})
        platforms = result.get("platform_readiness", {})
        crawlers = result.get("ai_crawlers", {})

        # AI Citability (25%)
        citability = min(content.get("citability_ratio", 0) * 2.5, 100)

        # Content Quality (20%)
        content_score = 0
        wc = content.get("word_count", 0)
        if wc >= 1500:
            content_score += 30
        elif wc >= 800:
            content_score += 20
        elif wc >= 300:
            content_score += 10
        if content.get("h_count", 0) >= 3:
            content_score += 20
        if content.get("lists", 0) >= 1:
            content_score += 15
        if content.get("images_with_alt", 0) >= 1:
            content_score += 15
        if content.get("external_links", 0) >= 1:
            content_score += 10
        if content.get("avg_paragraph_words", 999) <= 40:
            content_score += 10
        content_score = min(content_score, 100)

        # Technical (15%)
        tech_score = 0
        if tech.get("https"):
            tech_score += 25
        if tech.get("is_ssr"):
            tech_score += 25
        if tech.get("meta_description"):
            tech_score += 15
        if tech.get("title_length", 0) > 0:
            tech_score += 10
        if tech.get("canonical"):
            tech_score += 10
        if tech.get("language"):
            tech_score += 5
        sh = tech.get("security_headers", {})
        if sh.get("hsts"):
            tech_score += 5
        if sh.get("csp"):
            tech_score += 5
        tech_score = min(tech_score, 100)

        # Crawler Access (15%)
        allowed = sum(
            1 for c in crawlers.values() if c.get("status") == "allowed"
        )
        total_crawlers = len(crawlers) or 1
        crawler_score = int((allowed / total_crawlers) * 100)

        # Schema (10%)
        schema_score = schema.get("schema_score", 0)

        # Platform avg (15%)
        plat_scores = [
            v["score"] if isinstance(v, dict) else v
            for v in platforms.values()
            if isinstance(v, (int, float, dict))
        ]
        platform_score = int(sum(plat_scores) / max(len(plat_scores), 1))

        # GEO Score composite
        geo_score = int(
            citability * 0.25
            + content_score * 0.20
            + tech_score * 0.15
            + crawler_score * 0.15
            + schema_score * 0.10
            + platform_score * 0.15
        )

        return {
            "geo_score": geo_score,
            "citability": int(citability),
            "content_quality": content_score,
            "technical": tech_score,
            "crawler_access": crawler_score,
            "schema": schema_score,
            "platform_readiness": platform_score,
        }

    def _generate_recommendations(self, result: dict) -> list:
        recs = []
        content = result.get("content", {})
        schema = result.get("schema", {})
        tech = result.get("technical", {})
        crawlers = result.get("ai_crawlers", {})

        # Critical
        if not tech.get("https"):
            recs.append({
                "priority": "critical",
                "category": "Technique",
                "text": "Passer le site en HTTPS",
            })
        if not tech.get("is_ssr"):
            recs.append({
                "priority": "critical",
                "category": "Technique",
                "text": (
                    "Le contenu semble rendu en JavaScript (SPA) — les crawlers "
                    "IA ne peuvent pas le lire. Passer en SSR."
                ),
            })

        blocked = [
            f"{c} ({v['label']})"
            for c, v in crawlers.items()
            if v.get("status") == "blocked"
        ]
        if blocked:
            recs.append({
                "priority": "critical",
                "category": "Crawlers IA",
                "text": (
                    f"Crawlers bloques dans robots.txt : {', '.join(blocked)}. "
                    "Debloquer pour etre visible des IA."
                ),
            })

        # High
        if not schema.get("has_organization") and not schema.get("has_same_as"):
            recs.append({
                "priority": "high",
                "category": "Schema",
                "text": (
                    "Ajouter un schema Organization/LocalBusiness avec sameAs "
                    "(liens vers les profils sociaux). C'est le signal le plus "
                    "impactant pour le GEO."
                ),
            })
        if content.get("word_count", 0) < 800:
            recs.append({
                "priority": "high",
                "category": "Contenu",
                "text": (
                    f"Contenu trop court ({content.get('word_count', 0)} mots). "
                    "Viser 1500+ mots pour etre citable par les IA."
                ),
            })
        if content.get("citability_ratio", 0) < 15:
            recs.append({
                "priority": "high",
                "category": "Contenu",
                "text": (
                    "Taux de citabilite faible. Ajouter des paragraphes courts "
                    "(20-60 mots) qui repondent directement a des questions."
                ),
            })

        # Medium
        if not tech.get("llms_txt", {}).get("exists"):
            recs.append({
                "priority": "medium",
                "category": "IA",
                "text": (
                    "Creer un fichier llms.txt a la racine du site pour guider "
                    "les LLM."
                ),
            })
        if content.get("lists", 0) == 0:
            recs.append({
                "priority": "medium",
                "category": "Contenu",
                "text": (
                    "Ajouter des listes (ul/ol) — les IA les citent plus "
                    "facilement."
                ),
            })
        if content.get("images_with_alt", 0) < content.get("images", 0):
            missing = content.get("images", 0) - content.get("images_with_alt", 0)
            recs.append({
                "priority": "medium",
                "category": "Contenu",
                "text": f"{missing} images sans attribut alt.",
            })
        if not schema.get("has_article"):
            recs.append({
                "priority": "medium",
                "category": "Schema",
                "text": (
                    "Ajouter un schema Article/BlogPosting pour les pages de "
                    "contenu."
                ),
            })
        if not schema.get("has_breadcrumb"):
            recs.append({
                "priority": "medium",
                "category": "Schema",
                "text": "Ajouter un schema BreadcrumbList pour la navigation.",
            })

        return recs
