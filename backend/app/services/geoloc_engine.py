"""
Moteur de contenu geolocalisé pour pages SEO.

Genere des pages uniques par ville a partir d'un texte source,
avec variantes de contenu, maillage interne par proximite
geographique, analyse concurrentielle et export Excel
(compatible WP All Import / Divi Builder).
"""

import io
import json
import logging
import math
import re
import unicodedata
from datetime import datetime, timedelta
from typing import Optional

import anthropic
import httpx
from bs4 import BeautifulSoup
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

from app.config import get_settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Fonctions utilitaires
# ---------------------------------------------------------------------------


def slugify(text: str) -> str:
    """Convertit un texte en slug URL (ASCII, minuscules, tirets)."""
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"-+", "-", text)
    return text.strip("-")


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distance en kilometres entre deux points GPS (formule de Haversine)."""
    R = 6_371.0  # rayon moyen de la Terre en km
    rlat1, rlon1, rlat2, rlon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlat = rlat2 - rlat1
    dlon = rlon2 - rlon1
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(rlat1) * math.cos(rlat2) * math.sin(dlon / 2) ** 2
    )
    return R * 2 * math.asin(math.sqrt(a))


def replace_ville(html: str, ville: str) -> str:
    """Remplace le placeholder ``__VILLE__`` (insensible a la casse)."""
    return re.sub(r"__VILLE__", ville, html, flags=re.IGNORECASE)


def scrape_page(url: str, timeout: float = 15.0) -> dict:
    """Telecharge une URL et en extrait titre, H1, H2s et contenu texte."""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
    }
    try:
        with httpx.Client(follow_redirects=True, timeout=timeout) as client:
            resp = client.get(url, headers=headers)
            resp.raise_for_status()
    except (httpx.HTTPError, httpx.TimeoutException) as exc:
        logger.warning("Echec du scraping de %s : %s", url, exc)
        return {"url": url, "error": str(exc), "title": "", "h1": "", "h2s": [], "content": ""}

    soup = BeautifulSoup(resp.text, "html.parser")
    title_tag = soup.find("title")
    h1_tag = soup.find("h1")
    h2_tags = soup.find_all("h2")

    for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
        tag.decompose()
    body_text = soup.get_text(separator="\n", strip=True)[:5000]

    return {
        "url": url,
        "title": title_tag.get_text(strip=True) if title_tag else "",
        "h1": h1_tag.get_text(strip=True) if h1_tag else "",
        "h2s": [h2.get_text(strip=True) for h2 in h2_tags],
        "content": body_text,
    }


# ---------------------------------------------------------------------------
# Moteur principal
# ---------------------------------------------------------------------------


class GeolocEngine:
    """Orchestre la generation de pages geolocalisees SEO via Claude."""

    DEFAULT_MODEL = "claude-sonnet-4-6"

    def __init__(self, model: Optional[str] = None):
        settings = get_settings()
        self.client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        self.model = model or self.DEFAULT_MODEL

    # ------------------------------------------------------------------
    # Appels Claude
    # ------------------------------------------------------------------

    def _call_claude(
        self,
        system: str,
        user_prompt: str,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> str:
        """Envoie un prompt a Claude et retourne la reponse texte."""
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                temperature=temperature,
                system=system,
                messages=[{"role": "user", "content": user_prompt}],
            )
            return response.content[0].text
        except anthropic.APIError as exc:
            logger.error("Erreur API Claude : %s", exc)
            raise RuntimeError(f"Erreur API Claude : {exc}") from exc

    def _call_claude_json(
        self,
        system: str,
        user_prompt: str,
        max_tokens: int = 4096,
        temperature: float = 0.5,
    ) -> dict | list:
        """Envoie un prompt a Claude et parse la reponse en JSON."""
        raw = self._call_claude(system, user_prompt, max_tokens, temperature)

        # Tenter d'extraire le JSON depuis un bloc ```json ... ```
        match = re.search(r"```(?:json)?\s*([\[{][\s\S]*?[}\]])\s*```", raw)
        if match:
            json_str = match.group(1).strip()
        else:
            # Recherche de la premiere accolade / crochet ouvrant
            json_str = raw.strip()
            for i, ch in enumerate(json_str):
                if ch in ("{", "["):
                    json_str = json_str[i:]
                    break

        try:
            return json.loads(json_str)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Impossible de parser le JSON retourne par Claude : {exc}\n"
                f"Reponse brute (500 premiers caracteres) : {raw[:500]}"
            ) from exc

    # ------------------------------------------------------------------
    # 1. Analyse du texte source
    # ------------------------------------------------------------------

    def analyze_source(self, source_text: str, brief: dict) -> dict:
        """
        Analyse un texte source et un brief pour en extraire le contexte SEO.

        Args:
            source_text: texte brut (depuis DOCX ou colle).
            brief: dict avec des indications (ex: {"secteur": "plomberie"}).

        Returns:
            dict avec les cles : keyword, slug_base, secteur, template_title,
            template_h1, template_meta, services_cles, contexte_metier.
        """
        system = (
            "Tu es un expert SEO francophone specialise dans le contenu geolocalisé. "
            "Tu analyses des textes sources pour en extraire la structure semantique. "
            "Tu reponds exclusivement en JSON valide."
        )
        user_prompt = f"""Analyse le texte source ci-dessous et le brief associe.
Extrais les informations suivantes en JSON :

- "keyword" : mot-cle principal (2 a 4 mots, sans nom de ville)
- "slug_base" : slug URL de base (sans ville), ex: "plombier"
- "secteur" : description courte du secteur d'activite
- "template_title" : titre SEO avec le placeholder __VILLE__, max 60 caracteres
- "template_h1" : titre H1 avec __VILLE__
- "template_meta" : meta description avec __VILLE__, max 155 caracteres
- "services_cles" : liste de 5 a 8 services cles mentionnes
- "contexte_metier" : resume du contexte metier en 2-3 phrases

BRIEF :
{json.dumps(brief, ensure_ascii=False)}

TEXTE SOURCE :
{source_text[:6000]}

Reponds uniquement avec le JSON, sans commentaire."""

        return self._call_claude_json(system, user_prompt)

    # ------------------------------------------------------------------
    # 2. Generation des ancres de maillage interne
    # ------------------------------------------------------------------

    def generate_anchors(self, ctx: dict) -> list[str]:
        """
        Genere 6 ancres de liens internes naturelles avec le placeholder __VILLE__.
        """
        system = (
            "Tu es un expert en maillage interne SEO. "
            "Tu generes des textes d'ancre naturels et varies. "
            "Reponds en JSON (liste de strings)."
        )
        user_prompt = f"""Genere exactement 6 textes d'ancre pour des liens internes.
Chaque ancre doit :
- contenir le placeholder __VILLE__
- etre naturelle et variee (pas de repetition de formulation)
- integrer le secteur : {ctx.get("secteur", "")}
- etre courte (3 a 8 mots)

Mot-cle principal : {ctx.get("keyword", "")}
Services cles : {", ".join(ctx.get("services_cles", []))}

Reponds avec un tableau JSON de 6 strings, rien d'autre."""

        raw = self._call_claude_json(system, user_prompt)
        if isinstance(raw, list):
            return raw
        # Claude peut renvoyer un dict contenant une liste
        for val in raw.values():
            if isinstance(val, list):
                return val
        return []

    # ------------------------------------------------------------------
    # 3. Decoupage et optimisation en blocs
    # ------------------------------------------------------------------

    def optimize_blocks(self, source_text: str, ctx: dict, anchors: list[str]) -> dict:
        """
        Decoupe et optimise le contenu source en 6 blocs :
        BLOC_INTRO, BLOC_H2_1 .. BLOC_H2_4, BLOC_FAQ.
        """
        system = (
            "Tu es un redacteur SEO expert en contenu geolocalisé. "
            "Tu structures du contenu en blocs HTML optimises. "
            "Utilise __VILLE__ comme placeholder pour le nom de ville. "
            "Reponds exclusivement en JSON valide."
        )
        anchors_str = "\n".join(f"- {a}" for a in anchors)

        user_prompt = f"""A partir du texte source et du contexte ci-dessous, genere 6 blocs de contenu HTML.

CONTEXTE :
- Mot-cle : {ctx.get("keyword", "")}
- Secteur : {ctx.get("secteur", "")}
- Services : {", ".join(ctx.get("services_cles", []))}
- Contexte metier : {ctx.get("contexte_metier", "")}

ANCRES DE MAILLAGE (a integrer naturellement, 1 ou 2 par bloc) :
{anchors_str}

TEXTE SOURCE :
{source_text[:6000]}

STRUCTURE ATTENDUE (JSON) :
{{
  "BLOC_INTRO": "<p>Introduction engageante sans H2, avec __VILLE__. 100-150 mots.</p>",
  "BLOC_H2_1": "<h2>Titre section 1 a __VILLE__</h2><h3>...</h3><p>...</p>...",
  "BLOC_H2_2": "<h2>Titre section 2 a __VILLE__</h2><h3>...</h3><p>...</p>...",
  "BLOC_H2_3": "<h2>Titre section 3 a __VILLE__</h2><h3>...</h3><p>...</p>...",
  "BLOC_H2_4": "<h2>Titre section 4 a __VILLE__</h2><h3>...</h3><p>...</p>...",
  "BLOC_FAQ": "<h2>Questions frequentes sur ... a __VILLE__</h2><h3>Q1 ?</h3><p>R1</p>..."
}}

CONSIGNES :
- Chaque BLOC_H2 : 1 H2 + 2-3 H3 + paragraphes riches (200-300 mots)
- BLOC_FAQ : 5 questions/reponses avec H3 pour chaque question
- Ancres sous forme de <a href="__LIEN__">ancre</a>
- __VILLE__ present dans chaque bloc au moins une fois
- HTML propre : h2, h3, p, ul, li, a — pas de markdown
- Contenu expert avec des donnees concretes

Reponds uniquement avec le JSON."""

        return self._call_claude_json(system, user_prompt, max_tokens=8192)

    # ------------------------------------------------------------------
    # 4. Generation de variantes de contenu
    # ------------------------------------------------------------------

    def generate_variants(self, blocs: dict, ctx: dict, count: int = 3) -> list[dict]:
        """
        Genere *count* variantes des blocs pour eviter le duplicate content.

        Chaque variante conserve la meme structure HTML mais reformule
        le texte (synonymes, tournures, exemples differents).
        """
        variants: list[dict] = []
        system = (
            "Tu es un redacteur SEO expert. Tu reecris du contenu HTML "
            "en conservant la structure (memes balises H2, H3) mais en variant "
            "les formulations, exemples et tournures de phrases. "
            "Le contenu doit rester naturel, expert et optimise SEO. "
            "Garde le placeholder __VILLE__. Reponds en JSON valide."
        )

        for i in range(count):
            user_prompt = f"""Reecris les blocs de contenu ci-dessous pour creer la variante {i + 1}/{count}.

CONTEXTE :
- Mot-cle : {ctx.get("keyword", "")}
- Secteur : {ctx.get("secteur", "")}

BLOCS ORIGINAUX :
{json.dumps(blocs, ensure_ascii=False, indent=2)}

CONSIGNES :
- Conserve exactement la meme structure HTML (memes H2, H3)
- Modifie au moins 60 % du texte : reformulations, synonymes, exemples differents
- Garde les ancres de liens (<a>) intactes
- Garde __VILLE__ comme placeholder
- La variante {i + 1} doit etre significativement differente des precedentes
- Retourne le JSON avec les memes cles (BLOC_INTRO, BLOC_H2_1 .. BLOC_H2_4, BLOC_FAQ)

Reponds uniquement avec le JSON."""

            try:
                variant = self._call_claude_json(system, user_prompt, max_tokens=8192)
                variants.append(variant)
            except (ValueError, RuntimeError) as exc:
                logger.error("Erreur generation variante %d : %s", i + 1, exc)
                variants.append(blocs)  # fallback : reutiliser l'original

        return variants

    # ------------------------------------------------------------------
    # 5. Analyse concurrentielle
    # ------------------------------------------------------------------

    def analyze_competitors(self, urls: list[str], ctx: dict) -> dict:
        """
        Scrape les pages concurrentes et les analyse via Claude.

        Returns:
            dict avec themes_manquants, arguments_cles, enrichissement.
        """
        scraped: list[dict] = []
        for url in urls[:5]:
            data = scrape_page(url)
            if not data.get("error"):
                scraped.append(data)

        if not scraped:
            return {
                "themes_manquants": [],
                "arguments_cles": [],
                "enrichissement": "Aucune page concurrente n'a pu etre analysee.",
            }

        competitors_text = ""
        for s in scraped:
            competitors_text += (
                f"\n--- {s['url']} ---\n"
                f"Title : {s['title']}\n"
                f"H1 : {s['h1']}\n"
                f"H2s : {', '.join(s['h2s'][:10])}\n"
                f"Extrait : {s['content'][:1500]}\n"
            )

        system = (
            "Tu es un expert SEO qui analyse la concurrence. "
            "Reponds en JSON valide."
        )
        user_prompt = f"""Analyse les pages concurrentes ci-dessous par rapport a notre contexte.

NOTRE CONTEXTE :
- Mot-cle : {ctx.get("keyword", "")}
- Secteur : {ctx.get("secteur", "")}
- Services : {", ".join(ctx.get("services_cles", []))}

PAGES CONCURRENTES :
{competitors_text}

Retourne un JSON avec :
- "themes_manquants" : liste de themes que les concurrents couvrent et qui manquent dans notre contenu
- "arguments_cles" : liste d'arguments commerciaux ou techniques pertinents reperes
- "enrichissement" : conseils en 3-5 phrases pour enrichir notre contenu

Reponds uniquement avec le JSON."""

        return self._call_claude_json(system, user_prompt)

    # ------------------------------------------------------------------
    # 6. Maillage interne (liens vers villes proches)
    # ------------------------------------------------------------------

    def generate_maillage(
        self,
        ville: str,
        all_villes: list[dict],
        slug_base: str,
        anchors: list[str],
        nb: int = 8,
    ) -> str:
        """
        Genere un bloc HTML ``<ul>`` de liens internes vers les *nb* villes
        les plus proches geographiquement de *ville*.

        Chaque element de *all_villes* doit avoir les cles :
            nom, slug, lat, lon.
        """
        # Identifier la ville courante
        current = None
        for v in all_villes:
            if v.get("nom", "").lower() == ville.lower():
                current = v
                break

        if not current or "lat" not in current or "lon" not in current:
            return ""

        # Calculer les distances et trier par proximite
        distances: list[tuple[dict, float]] = []
        for v in all_villes:
            if v.get("nom", "").lower() == ville.lower():
                continue
            if "lat" not in v or "lon" not in v:
                continue
            dist = haversine(current["lat"], current["lon"], v["lat"], v["lon"])
            distances.append((v, dist))

        distances.sort(key=lambda x: x[1])
        closest = distances[:nb]
        if not closest:
            return ""

        lines = ['<ul class="maillage-interne">']
        for i, (v, _dist) in enumerate(closest):
            anchor_tpl = (
                anchors[i % len(anchors)] if anchors else "Nos services a __VILLE__"
            )
            anchor_text = replace_ville(anchor_tpl, v["nom"])
            v_slug = v.get("slug", slugify(v["nom"]))
            href = f"/{slug_base}-{v_slug}/"
            lines.append(
                f'  <li><a href="{href}" title="{anchor_text}">{anchor_text}</a></li>'
            )
        lines.append("</ul>")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # 7. Assemblage de la page complete
    # ------------------------------------------------------------------

    def assemble_page(
        self,
        blocs: dict,
        ville: dict,
        ctx: dict,
        maillage: str,
        variant_idx: int = 0,
        use_divi: bool = False,
        images: Optional[list[str]] = None,
        colors: Optional[dict] = None,
    ) -> str:
        """
        Assemble les blocs, le maillage et les images en une page HTML
        complete (ou en shortcodes Divi si *use_divi* est True).

        *ville* doit contenir au minimum ``{"nom": "Paris"}``.
        """
        nom_ville = ville.get("nom", "")
        images = images or []
        colors = colors or {"primary": "#2EA3F2", "secondary": "#E02B20"}

        # Remplacement des placeholders dans chaque bloc
        page_blocs: dict[str, str] = {}
        for key, html in blocs.items():
            page_blocs[key] = replace_ville(str(html), nom_ville)

        # Remplacement du placeholder de lien __LIEN__
        slug_base = ctx.get("slug_base", "service")
        ville_slug = ville.get("slug", slugify(nom_ville))
        for key in page_blocs:
            page_blocs[key] = page_blocs[key].replace(
                "__LIEN__", f"/{slug_base}-{ville_slug}/"
            )

        if use_divi:
            return self._wrap_divi(page_blocs, maillage, images, colors)
        return self._wrap_html(page_blocs, maillage, images)

    # --- assemblage HTML simple -------------------------------------------

    def _wrap_html(self, blocs: dict, maillage: str, images: list[str]) -> str:
        parts: list[str] = []

        parts.append(blocs.get("BLOC_INTRO", ""))

        if images:
            parts.append(
                f'<figure><img src="{images[0]}" alt="" loading="lazy" /></figure>'
            )

        for i in range(1, 5):
            key = f"BLOC_H2_{i}"
            if key in blocs:
                parts.append(blocs[key])
            if i < len(images):
                parts.append(
                    f'<figure><img src="{images[i]}" alt="" loading="lazy" /></figure>'
                )

        if "BLOC_FAQ" in blocs:
            parts.append(blocs["BLOC_FAQ"])

        if maillage:
            parts.append('<div class="maillage-section">')
            parts.append("<h2>Nos services dans votre region</h2>")
            parts.append(maillage)
            parts.append("</div>")

        return "\n\n".join(parts)

    # --- assemblage Divi Builder ------------------------------------------

    def _wrap_divi(
        self, blocs: dict, maillage: str, images: list[str], colors: dict
    ) -> str:
        primary = colors.get("primary", "#2EA3F2")
        secondary = colors.get("secondary", "#E02B20")

        def section(content: str, bg: str = "#ffffff") -> str:
            return (
                f'[et_pb_section fb_built="1" background_color="{bg}" '
                f'custom_padding="40px||40px||false|false"]'
                f'[et_pb_row][et_pb_column type="4_4"]'
                f'[et_pb_text text_font_size="16px" text_line_height="1.8em"]'
                f"{content}"
                f"[/et_pb_text][/et_pb_column][/et_pb_row][/et_pb_section]"
            )

        def img_section(url: str) -> str:
            return (
                f'[et_pb_section fb_built="1" custom_padding="20px||20px||false|false"]'
                f'[et_pb_row][et_pb_column type="4_4"]'
                f'[et_pb_image src="{url}" align="center" '
                f'force_fullwidth="on"][/et_pb_image]'
                f"[/et_pb_column][/et_pb_row][/et_pb_section]"
            )

        parts: list[str] = []

        # Intro
        parts.append(section(blocs.get("BLOC_INTRO", "")))
        if images:
            parts.append(img_section(images[0]))

        # Blocs H2 avec alternance de fond
        bg_alt = ["#ffffff", "#f7f7f7", "#ffffff", "#f7f7f7"]
        for i in range(1, 5):
            key = f"BLOC_H2_{i}"
            if key in blocs:
                parts.append(section(blocs[key], bg_alt[i - 1]))
            if i < len(images):
                parts.append(img_section(images[i]))

        # FAQ
        if "BLOC_FAQ" in blocs:
            parts.append(section(blocs["BLOC_FAQ"], "#f0f4f8"))

        # Maillage
        if maillage:
            parts.append(
                section(
                    f"<h2>Nos services dans votre region</h2>\n{maillage}",
                    "#eaf2e3",
                )
            )

        # CTA final
        cta = (
            f'[et_pb_section fb_built="1" background_color="{primary}" '
            f'custom_padding="30px||30px||false|false"]'
            f'[et_pb_row][et_pb_column type="4_4"]'
            f'[et_pb_cta title="Contactez-nous" '
            f'button_text="Demander un devis" '
            f'button_url="#contact" '
            f'use_background_color="off" '
            f'header_text_color="#ffffff" '
            f'body_text_color="#ffffff" '
            f'custom_button="on" '
            f'button_text_color="#ffffff" '
            f'button_bg_color="{secondary}" '
            f'button_border_color="{secondary}"]'
            f"<p>Nous intervenons rapidement. Contactez notre equipe "
            f"pour un devis gratuit.</p>"
            f"[/et_pb_cta][/et_pb_column][/et_pb_row][/et_pb_section]"
        )
        parts.append(cta)

        return "\n".join(parts)

    # ------------------------------------------------------------------
    # 8. Generation du fichier Excel
    # ------------------------------------------------------------------

    def build_excel(
        self,
        villes: list[dict],
        ctx: dict,
        blocs: dict,
        variants: list[dict],
        config: dict,
    ) -> bytes:
        """
        Genere un fichier Excel d'import WordPress (WP All Import).

        Colonnes produites :
            Ville actuelle, SLUG, post_title, H1, post_description,
            post_content, post_thumbnail, post_date, post_author,
            post_category, post_tag, post_status, Population, Type,
            _et_pb_use_builder, _et_pb_old_content

        *config* attend les cles :
            post_author, post_category, post_tag, post_status,
            post_date_start (YYYY-MM-DD), use_divi, images, colors, anchors.
        """
        wb = Workbook()
        ws = wb.active
        ws.title = "Pages Geoloc"

        headers = [
            "Ville actuelle",
            "SLUG",
            "post_title",
            "H1",
            "post_description",
            "post_content",
            "post_thumbnail",
            "post_date",
            "post_author",
            "post_category",
            "post_tag",
            "post_status",
            "Population",
            "Type",
            "_et_pb_use_builder",
            "_et_pb_old_content",
        ]

        header_font = Font(bold=True, color="FFFFFF", size=11)
        header_fill = PatternFill(
            start_color="2E86C1", end_color="2E86C1", fill_type="solid"
        )
        for col_idx, header in enumerate(headers, start=1):
            cell = ws.cell(row=1, column=col_idx, value=header)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal="center")

        # Lecture de la configuration
        slug_base = ctx.get("slug_base", "service")
        template_title = ctx.get("template_title", "__VILLE__")
        template_h1 = ctx.get("template_h1", "__VILLE__")
        template_meta = ctx.get("template_meta", "__VILLE__")
        anchors = config.get("anchors", [])
        use_divi = config.get("use_divi", False)
        images = config.get("images", [])
        colors = config.get("colors", {"primary": "#2EA3F2", "secondary": "#E02B20"})
        post_author = config.get("post_author", "admin")
        post_category = config.get("post_category", "")
        post_tag = config.get("post_tag", "")
        post_status = config.get("post_status", "draft")
        post_date_start = config.get("post_date_start", "2026-01-15")

        # Rotation original + variantes
        all_blocs = [blocs] + variants

        for row_idx, ville in enumerate(villes, start=2):
            nom = ville.get("nom", "")
            ville_slug = ville.get("slug", slugify(nom))

            variant_idx = (row_idx - 2) % len(all_blocs)
            current_blocs = all_blocs[variant_idx]

            # Maillage interne
            maillage = self.generate_maillage(
                nom, villes, slug_base, anchors, nb=8
            )

            # Assemblage de la page
            content = self.assemble_page(
                current_blocs,
                ville,
                ctx,
                maillage,
                variant_idx=variant_idx,
                use_divi=use_divi,
                images=images,
                colors=colors,
            )

            # Date incrementale (+1 jour par ville)
            try:
                base_date = datetime.strptime(post_date_start, "%Y-%m-%d")
                post_date = (base_date + timedelta(days=row_idx - 2)).strftime(
                    "%Y-%m-%d"
                )
            except (ValueError, TypeError):
                post_date = post_date_start

            slug_full = f"{slug_base}-{ville_slug}"
            title = replace_ville(template_title, nom)
            h1 = replace_ville(template_h1, nom)
            meta = replace_ville(template_meta, nom)

            row_data = [
                nom,                                    # Ville actuelle
                slug_full,                              # SLUG
                title,                                  # post_title
                h1,                                     # H1
                meta,                                   # post_description
                content,                                # post_content
                images[0] if images else "",             # post_thumbnail
                post_date,                              # post_date
                post_author,                            # post_author
                post_category,                          # post_category
                post_tag,                               # post_tag
                post_status,                            # post_status
                ville.get("population", ""),             # Population
                ville.get("type", ""),                   # Type
                "on" if use_divi else "",                # _et_pb_use_builder
                "",                                      # _et_pb_old_content
            ]

            for col_idx, value in enumerate(row_data, start=1):
                cell = ws.cell(row=row_idx, column=col_idx, value=value)
                if col_idx == 6:
                    cell.alignment = Alignment(wrap_text=True)

        # Largeurs de colonnes
        col_widths = {
            1: 18,  2: 30,  3: 50,  4: 50,  5: 60,
            6: 80,  7: 40,  8: 14,  9: 12,  10: 20,
            11: 20, 12: 10, 13: 12, 14: 12, 15: 18, 16: 15,
        }
        for col, width in col_widths.items():
            letter = ws.cell(row=1, column=col).column_letter
            ws.column_dimensions[letter].width = width

        buffer = io.BytesIO()
        wb.save(buffer)
        return buffer.getvalue()
