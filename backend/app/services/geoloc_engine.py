"""
GeolocEngine — Advanced geoloc content engine.

Handles source analysis, anchor generation, block optimization,
competitor analysis, variant generation, and full pipeline assembly.
"""
import re
import json
import logging
import httpx
from typing import Any

import anthropic
from app.config import get_settings

logger = logging.getLogger(__name__)


class GeolocEngine:
    """Orchestrates advanced geoloc content generation via Claude API."""

    def __init__(self):
        settings = get_settings()
        self.client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        self.model = getattr(settings, "claude_model", "claude-sonnet-4-20250514")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _ask(self, system: str, user: str, max_tokens: int = 4096) -> str:
        """Send a prompt to Claude and return the text response."""
        resp = self.client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return resp.content[0].text

    def _ask_json(self, system: str, user: str, max_tokens: int = 4096) -> dict:
        """Send a prompt and parse a JSON response."""
        raw = self._ask(system, user, max_tokens)
        # Try to extract JSON from markdown code fences first
        m = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", raw)
        if m:
            return json.loads(m.group(1))
        # Fallback: find outermost braces
        depth = 0
        start = None
        for i, ch in enumerate(raw):
            if ch == "{":
                if depth == 0:
                    start = i
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0 and start is not None:
                    try:
                        return json.loads(raw[start : i + 1])
                    except json.JSONDecodeError:
                        start = None
        # Last resort
        return json.loads(raw)

    # ------------------------------------------------------------------
    # 1. Analyze source text
    # ------------------------------------------------------------------

    def analyze_source(self, source_text: str, brief: dict | None = None) -> dict:
        """Analyze source text and return a context dict (keyword, slug_base, templates, etc.)."""
        brief = brief or {}
        brief_section = ""
        if brief:
            brief_section = "\n\nBRIEF CLIENT:\n" + json.dumps(brief, ensure_ascii=False, indent=2)

        system = (
            "Tu es un expert SEO spécialisé dans la création de pages géolocalisées. "
            "Analyse le texte source et extrais les informations clés pour créer des pages locales."
        )
        user = f"""Analyse ce texte source et retourne un JSON avec :
- keyword : le mot-clé principal (sans ville)
- slug_base : le slug SEO de base (sans ville)
- title_template : template de titre avec {{ville}} et {{departement}}
- h1_template : template H1 avec {{ville}}
- meta_desc_template : template de meta description avec {{ville}} et {{departement}}
- themes : liste des thèmes abordés
- services : liste des services mentionnés
- arguments : liste des arguments de vente
- tone : le ton détecté (conversationnel, technique, commercial, etc.)
- target_length : longueur cible estimée en mots
{brief_section}

TEXTE SOURCE :
{source_text[:8000]}

Réponds UNIQUEMENT en JSON valide."""

        return self._ask_json(system, user)

    # ------------------------------------------------------------------
    # 2. Generate internal linking anchors
    # ------------------------------------------------------------------

    def generate_anchors(self, ctx: dict) -> list[str]:
        """Generate internal linking anchor texts based on context."""
        system = (
            "Tu es un expert en maillage interne SEO. "
            "Génère des ancres de liens internes variées et naturelles."
        )
        user = f"""A partir de ce contexte, génère 15-20 ancres de liens internes variées
pour le maillage entre pages géolocalisées.

Contexte :
{json.dumps(ctx, ensure_ascii=False, indent=2)}

Les ancres doivent :
- Contenir le mot-clé principal ou des variantes
- Inclure des formulations naturelles (pas juste le mot-clé brut)
- Varier entre ancres exactes, partielles, et génériques
- Utiliser {{ville}} comme placeholder pour la ville

Réponds en JSON : {{"anchors": ["ancre 1", "ancre 2", ...]}}"""

        result = self._ask_json(system, user)
        return result.get("anchors", [])

    # ------------------------------------------------------------------
    # 3. Optimize blocks (split content into 5 blocks)
    # ------------------------------------------------------------------

    def optimize_blocks(self, source_text: str, ctx: dict, anchors: list[str] | None = None) -> dict:
        """Split and optimize content into 5 SEO blocks."""
        anchors = anchors or []
        anchor_section = ""
        if anchors:
            anchor_section = "\n\nANCRES DISPONIBLES pour le maillage :\n" + "\n".join(f"- {a}" for a in anchors)

        system = (
            "Tu es un rédacteur SEO expert en pages géolocalisées. "
            "Tu dois découper et optimiser le contenu en 5 blocs distincts."
        )
        user = f"""Découpe et optimise ce contenu en 5 blocs pour des pages géolocalisées.

CONTEXTE :
{json.dumps(ctx, ensure_ascii=False, indent=2)}
{anchor_section}

TEXTE SOURCE :
{source_text[:8000]}

Retourne un JSON avec exactement ces 5 clés :
- BLOC_INTRO : introduction engageante (2-3 paragraphes) avec {{ville}} et {{departement}}
- BLOC_H2_1 : premier bloc H2 (services/expertise)
- BLOC_H2_2 : deuxième bloc H2 (avantages/pourquoi nous)
- BLOC_H2_3 : troisième bloc H2 (zone d'intervention/proximité)
- BLOC_H2_4 : quatrième bloc H2 (processus/comment ça marche)
- BLOC_FAQ : bloc FAQ avec 4-5 questions/réponses en HTML

Chaque bloc doit :
- Contenir du HTML valide (h2, h3, p, ul, li)
- Utiliser {{ville}}, {{departement}}, {{region}}, {{code_postal}} comme placeholders
- Être unique et apporter de la valeur
- Intégrer naturellement le mot-clé principal

Réponds UNIQUEMENT en JSON valide."""

        return self._ask_json(system, user, max_tokens=8192)

    # ------------------------------------------------------------------
    # 4. Analyze competitors
    # ------------------------------------------------------------------

    def analyze_competitors(self, urls: list[str], ctx: dict) -> dict:
        """Analyze competitor URLs and return missing themes, key arguments, enrichment suggestions."""
        fetched_contents = []
        for url in urls[:5]:  # Max 5 URLs
            try:
                with httpx.Client(timeout=15, follow_redirects=True) as http:
                    resp = http.get(url, headers={"User-Agent": "MatoubAPP/1.0"})
                    if resp.status_code == 200:
                        from bs4 import BeautifulSoup

                        soup = BeautifulSoup(resp.text, "html.parser")
                        # Remove script/style
                        for tag in soup(["script", "style", "nav", "footer", "header"]):
                            tag.decompose()
                        text = soup.get_text(separator="\n", strip=True)[:3000]
                        fetched_contents.append({"url": url, "text": text})
            except Exception as exc:
                logger.warning("Failed to fetch competitor %s: %s", url, exc)
                fetched_contents.append({"url": url, "text": "(inaccessible)"})

        system = (
            "Tu es un analyste SEO concurrentiel. "
            "Compare le contenu des concurrents avec le contexte du client."
        )
        competitor_texts = "\n\n---\n\n".join(
            f"URL: {c['url']}\n{c['text']}" for c in fetched_contents
        )
        user = f"""Analyse ces pages concurrentes par rapport au contexte client.

CONTEXTE CLIENT :
{json.dumps(ctx, ensure_ascii=False, indent=2)}

PAGES CONCURRENTES :
{competitor_texts[:12000]}

Retourne un JSON avec :
- themes_manquants : liste des thèmes abordés par les concurrents mais absents de notre contenu
- arguments_cles : liste des arguments forts des concurrents à reprendre/contrer
- enrichissement : liste de suggestions concrètes pour enrichir nos blocs

Réponds UNIQUEMENT en JSON valide."""

        return self._ask_json(system, user)

    # ------------------------------------------------------------------
    # 5. Generate variants
    # ------------------------------------------------------------------

    def generate_variants(self, blocs: dict, ctx: dict, count: int = 3) -> list[dict]:
        """Generate content variants to avoid duplicate content across cities."""
        system = (
            "Tu es un rédacteur SEO expert. "
            "Génère des variantes de contenu pour éviter le contenu dupliqué entre pages géolocalisées."
        )
        user = f"""A partir de ces blocs de contenu, génère {count} variantes distinctes.
Chaque variante doit reformuler le contenu en gardant le même sens et la même structure HTML,
mais avec des tournures de phrases, du vocabulaire et des angles différents.

CONTEXTE :
{json.dumps(ctx, ensure_ascii=False, indent=2)}

BLOCS ORIGINAUX :
{json.dumps(blocs, ensure_ascii=False, indent=2)}

Retourne un JSON : {{"variants": [{{...blocs variante 1...}}, {{...blocs variante 2...}}, ...]}}

Chaque variante doit avoir les mêmes clés que les blocs originaux (BLOC_INTRO, BLOC_H2_1, etc.)
Les placeholders {{ville}}, {{departement}}, {{region}}, {{code_postal}} doivent être conservés.

Réponds UNIQUEMENT en JSON valide."""

        result = self._ask_json(system, user, max_tokens=8192 * count)
        variants = result.get("variants", [])
        # Ensure we include the original as variant 0
        return [blocs] + variants[:count]

    # ------------------------------------------------------------------
    # 6. Assemble page for a city
    # ------------------------------------------------------------------

    def assemble_page(
        self,
        variant: dict,
        city: dict,
        anchors: list[str],
        all_cities: list[dict],
        ctx: dict,
        site_domain: str,
        use_divi: bool = False,
        images: list[str] | None = None,
        colors: dict | None = None,
    ) -> str:
        """Assemble a full page for a city from variant blocks + maillage."""
        images = images or []
        colors = colors or {}

        # Replace placeholders
        replacements = {
            "{ville}": city["name"],
            "{departement}": city["department"],
            "{region}": city.get("region", ""),
            "{code_postal}": city.get("postal_code", ""),
        }

        def replace_placeholders(text: str) -> str:
            for placeholder, value in replacements.items():
                text = text.replace(placeholder, value)
            return text

        # Build maillage (internal links) — villes les plus proches par distance
        import random
        from math import radians, sin, cos, sqrt, atan2

        def _haversine(lat1, lon1, lat2, lon2):
            R = 6371
            dlat, dlon = radians(lat2 - lat1), radians(lon2 - lon1)
            a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
            return R * 2 * atan2(sqrt(a), sqrt(1 - a))

        city_lat = city.get("lat", 0)
        city_lng = city.get("lng", 0)
        if city_lat and city_lng:
            # Trier par distance haversine
            candidates = [c for c in all_cities if c["name"] != city["name"] and c.get("lat") and c.get("lng")]
            candidates.sort(key=lambda c: _haversine(city_lat, city_lng, c["lat"], c["lng"]))
            nearby = candidates[:12]
        else:
            nearby = [c for c in all_cities if c["department"] == city["department"] and c["name"] != city["name"]][:12]

        # Prendre 6-8 villes parmi les plus proches avec un peu de randomisation
        selected = nearby[:4] + random.sample(nearby[4:12], min(4, len(nearby[4:12]))) if len(nearby) > 4 else nearby

        maillage_links = []
        for nc in selected[:8]:
            slug_base = ctx.get("slug_base", "service")
            anchor_pool = [a.replace("{ville}", nc["name"]) for a in anchors] if anchors else [nc["name"]]
            anchor = random.choice(anchor_pool) if anchor_pool else nc["name"]
            link = f'<a href="https://{site_domain}/{slug_base}-{nc["slug"]}/">{anchor}</a>'
            maillage_links.append(link)

        maillage_html = ""
        if maillage_links:
            maillage_html = (
                '<div class="maillage-interne">'
                "<h3>Nos interventions dans votre région</h3><ul>"
                + "".join(f"<li>{l}</li>" for l in maillage_links)
                + "</ul></div>"
            )

        # Insert images
        img_html_parts = []
        for idx, img_url in enumerate(images[:6]):
            alt = f"{ctx.get('keyword', '')} {city['name']}".strip()
            img_html_parts.append(
                f'<img src="{img_url}" alt="{alt}" loading="lazy" />'
            )

        # Assemble blocks in order
        blocks_order = ["BLOC_INTRO", "BLOC_H2_1", "BLOC_H2_2", "BLOC_H2_3", "BLOC_H2_4", "BLOC_FAQ"]
        content_parts = []
        img_idx = 0
        for block_key in blocks_order:
            block_content = variant.get(block_key, "")
            block_content = replace_placeholders(block_content)
            content_parts.append(block_content)
            # Insert an image after every other block
            if img_idx < len(img_html_parts) and block_key in ("BLOC_H2_1", "BLOC_H2_2", "BLOC_H2_3"):
                content_parts.append(img_html_parts[img_idx])
                img_idx += 1

        # Add maillage before FAQ
        content_parts.insert(-1, maillage_html)

        # Add remaining images
        while img_idx < len(img_html_parts):
            content_parts.append(img_html_parts[img_idx])
            img_idx += 1

        full_content = "\n\n".join(content_parts)

        # Wrap in Divi if needed
        if use_divi:
            bg_color = colors.get("light", "#FFFFFF")
            full_content = (
                f"[et_pb_section _builder_version='4.27.0' background_color='{bg_color}']"
                f"[et_pb_row _builder_version='4.27.0'][et_pb_column type='4_4' _builder_version='4.27.0']"
                f"[et_pb_text _builder_version='4.27.0']{full_content}[/et_pb_text]"
                f"[/et_pb_column][/et_pb_row][/et_pb_section]"
            )

        return full_content
