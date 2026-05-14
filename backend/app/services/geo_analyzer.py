"""
Analyse GEO (Generative Engine Optimization) — visibilité IA d'un site.
Inspiré du framework geo-seo-claude + DataForSEO AI Optimization API.
"""
import httpx
import json
import re
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
        self.client = httpx.Client(timeout=30, follow_redirects=True,
                                   headers={"User-Agent": "MatoubeAPP-GEO/1.0"})

    def analyze(self, url: str) -> dict:
        """Analyse GEO complète d'une URL."""
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
            result["technical"]["response_time_ms"] = int(resp.elapsed.total_seconds() * 1000)
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
            "canonical": soup.find("link", rel="canonical")["href"] if soup.find("link", rel="canonical") else None,
            "language": soup.find("html").get("lang", "") if soup.find("html") else "",
            "security_headers": security,
            "is_ssr": is_ssr,
            "framework": next((k for k, v in ssr_signals.items() if v), "unknown"),
            "og_tags": {k: v for k, v in meta_tags.items() if k.startswith("og:")},
        }

    def _analyze_content(self, soup) -> dict:
        # Extraire le texte
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
        avg_para_length = sum(len(p.get_text().split()) for p in paragraphs) / max(para_count, 1)

        # Listes
        lists = len(soup.find_all(["ul", "ol"]))

        # Images
        images = soup.find_all("img")
        images_with_alt = sum(1 for img in images if img.get("alt", "").strip())

        # Liens
        links = soup.find_all("a", href=True)
        internal = sum(1 for a in links if not a["href"].startswith("http"))
        external = len(links) - internal

        # Citability — les blocs qui pourraient être cités par un LLM
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
            "citability_ratio": round(citable_blocks / max(para_count, 1) * 100),
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
            if isinstance(t, list):
                types.extend(t)
            else:
                types.append(t)
            if t in ("Organization", "LocalBusiness"):
                has_org = True
                if s.get("sameAs"):
                    has_same_as = True
            if t in ("Article", "NewsArticle", "BlogPosting"):
                has_article = True
                if s.get("speakable"):
                    has_speakable = True
            if t == "FAQPage":
                has_faq = True
            if t == "BreadcrumbList":
                has_breadcrumb = True
            if t == "Person":
                has_person = True

        score = 0
        if schemas:
            score += 20
        if has_org:
            score += 20
        if has_same_as:
            score += 15  # Critical for GEO entity linking
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
                        # Check if disallowed
                        blocked = False
                        lines = robots.split("\n")
                        in_agent = False
                        for line in lines:
                            line = line.strip()
                            if line.startswith("user-agent:") and crawler.lower() in line:
                                in_agent = True
                            elif line.startswith("user-agent:") and in_agent:
                                in_agent = False
                            elif in_agent and line.startswith("disallow: /"):
                                blocked = True
                        result[crawler] = {"label": label, "status": "blocked" if blocked else "allowed", "in_robots": True}
                    else:
                        result[crawler] = {"label": label, "status": "allowed", "in_robots": False}
            else:
                for crawler, label in AI_CRAWLERS.items():
                    result[crawler] = {"label": label, "status": "allowed", "in_robots": False}
        except Exception:
            for crawler, label in AI_CRAWLERS.items():
                result[crawler] = {"label": label, "status": "unknown", "in_robots": False}
        return result

    def _check_llms_txt(self, domain: str) -> dict:
        try:
            resp = self.client.get(f"https://{domain}/llms.txt")
            if resp.status_code == 200:
                content = resp.text[:2000]
                return {"exists": True, "length": len(resp.text), "preview": content}
            return {"exists": False}
        except Exception:
            return {"exists": False}

    def _score_platforms(self, result: dict) -> dict:
        tech = result.get("technical", {})
        content = result.get("content", {})
        schema = result.get("schema", {})
        crawlers = result.get("ai_crawlers", {})

        def crawler_ok(name):
            return crawlers.get(name, {}).get("status") == "allowed"

        # Google AI Overviews
        google_score = 0
        if tech.get("https"):
            google_score += 15
        if content.get("h_count", 0) >= 3:
            google_score += 15
        if content.get("lists", 0) >= 1:
            google_score += 10
        if schema.get("has_organization"):
            google_score += 15
        if schema.get("has_article"):
            google_score += 10
        if content.get("word_count", 0) >= 800:
            google_score += 15
        if content.get("citability_ratio", 0) >= 20:
            google_score += 10
        if crawler_ok("Googlebot"):
            google_score += 10

        # ChatGPT
        chatgpt_score = 0
        if crawler_ok("GPTBot"):
            chatgpt_score += 25
        if content.get("word_count", 0) >= 500:
            chatgpt_score += 15
        if content.get("citable_blocks", 0) >= 5:
            chatgpt_score += 20
        if schema.get("has_same_as"):
            chatgpt_score += 15
        if content.get("external_links", 0) >= 2:
            chatgpt_score += 10
        if tech.get("meta_description"):
            chatgpt_score += 15

        # Perplexity
        perplexity_score = 0
        if crawler_ok("PerplexityBot"):
            perplexity_score += 25
        if content.get("citable_blocks", 0) >= 3:
            perplexity_score += 20
        if content.get("word_count", 0) >= 1000:
            perplexity_score += 15
        if content.get("lists", 0) >= 2:
            perplexity_score += 15
        if content.get("external_links", 0) >= 1:
            perplexity_score += 10
        if schema.get("schemas_found", 0) >= 1:
            perplexity_score += 15

        # Gemini
        gemini_score = 0
        if crawler_ok("Google-Extended"):
            gemini_score += 20
        if schema.get("has_organization"):
            gemini_score += 20
        if content.get("h_count", 0) >= 4:
            gemini_score += 15
        if tech.get("https"):
            gemini_score += 10
        if content.get("images_with_alt", 0) >= 2:
            gemini_score += 15
        if content.get("word_count", 0) >= 800:
            gemini_score += 10
        if schema.get("has_same_as"):
            gemini_score += 10

        # Bing Copilot
        bing_score = 0
        if crawler_ok("Bingbot"):
            bing_score += 20
        if tech.get("meta_description"):
            bing_score += 20
        if content.get("word_count", 0) >= 500:
            bing_score += 15
        if schema.get("has_organization"):
            bing_score += 15
        if content.get("h_count", 0) >= 3:
            bing_score += 15
        if tech.get("https"):
            bing_score += 15

        return {
            "google_aio": min(google_score, 100),
            "chatgpt": min(chatgpt_score, 100),
            "perplexity": min(perplexity_score, 100),
            "gemini": min(gemini_score, 100),
            "bing_copilot": min(bing_score, 100),
        }

    def _get_llm_mentions(self, domain: str) -> dict:
        """Récupère les mentions LLM via DataForSEO AI Optimization API."""
        try:
            dfs = DataForSEOClient()
            with dfs._client() as client:
                # Mentions du domaine
                resp = client.post(
                    f"{dfs.BASE_URL}/ai_optimization/llm_mentions/search/live",
                    json=[{
                        "target": [{"domain": domain, "search_filter": "include", "search_scope": "any"}],
                        "platform": "google",
                        "location_code": 2250,  # France
                        "language_code": "fr",
                        "limit": 20,
                    }]
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
        allowed = sum(1 for c in crawlers.values() if c.get("status") == "allowed")
        blocked = sum(1 for c in crawlers.values() if c.get("status") == "blocked")
        total_crawlers = len(crawlers) or 1
        crawler_score = int((allowed / total_crawlers) * 100)

        # Schema (10%)
        schema_score = schema.get("schema_score", 0)

        # Platform avg (15%)
        plat_scores = [v for v in platforms.values() if isinstance(v, int)]
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
        scores = result.get("scores", {})
        content = result.get("content", {})
        schema = result.get("schema", {})
        tech = result.get("technical", {})
        crawlers = result.get("ai_crawlers", {})

        # Critical
        if not tech.get("https"):
            recs.append({"priority": "critical", "category": "Technique", "text": "Passer le site en HTTPS"})
        if not tech.get("is_ssr"):
            recs.append({"priority": "critical", "category": "Technique", "text": "Le contenu semble rendu en JavaScript (SPA) — les crawlers IA ne peuvent pas le lire. Passer en SSR."})

        blocked = [f"{c} ({v['label']})" for c, v in crawlers.items() if v.get("status") == "blocked"]
        if blocked:
            recs.append({"priority": "critical", "category": "Crawlers IA", "text": f"Crawlers bloqués dans robots.txt : {', '.join(blocked)}. Débloquer pour être visible des IA."})

        # High
        if not schema.get("has_organization") and not schema.get("has_same_as"):
            recs.append({"priority": "high", "category": "Schema", "text": "Ajouter un schema Organization/LocalBusiness avec sameAs (liens vers les profils sociaux). C'est le signal le plus impactant pour le GEO."})
        if content.get("word_count", 0) < 800:
            recs.append({"priority": "high", "category": "Contenu", "text": f"Contenu trop court ({content.get('word_count', 0)} mots). Viser 1500+ mots pour être citable par les IA."})
        if content.get("citability_ratio", 0) < 15:
            recs.append({"priority": "high", "category": "Contenu", "text": "Taux de citabilité faible. Ajouter des paragraphes courts (20-60 mots) qui répondent directement à des questions."})

        # Medium
        if not tech.get("llms_txt", {}).get("exists"):
            recs.append({"priority": "medium", "category": "IA", "text": "Créer un fichier llms.txt à la racine du site pour guider les LLM."})
        if content.get("lists", 0) == 0:
            recs.append({"priority": "medium", "category": "Contenu", "text": "Ajouter des listes (ul/ol) — les IA les citent plus facilement."})
        if content.get("images_with_alt", 0) < content.get("images", 0):
            recs.append({"priority": "medium", "category": "Contenu", "text": f"{content.get('images',0) - content.get('images_with_alt',0)} images sans attribut alt."})
        if not schema.get("has_article"):
            recs.append({"priority": "medium", "category": "Schema", "text": "Ajouter un schema Article/BlogPosting pour les pages de contenu."})
        if not schema.get("has_breadcrumb"):
            recs.append({"priority": "medium", "category": "Schema", "text": "Ajouter un schema BreadcrumbList pour la navigation."})

        return recs
