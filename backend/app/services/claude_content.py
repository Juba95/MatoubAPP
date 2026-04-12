import anthropic
from app.config import get_settings
from app.models.site import Site


class ClaudeContentService:
    def __init__(self):
        self.client = anthropic.Anthropic(api_key=get_settings().anthropic_api_key)

    def _build_system_prompt(self, site: Site) -> str:
        """Construit un system prompt unique par site (anti-footprint)"""
        tone_map = {
            "journalistique": "Tu es un journaliste spécialisé. Écris de façon factuelle, avec des sources et des chiffres. Utilise un vocabulaire soutenu, des transitions éditoriales.",
            "technique": "Tu es un expert technique dans ton domaine. Écris de façon précise et détaillée, avec du jargon professionnel. Utilise des listes, des comparatifs, des données concrètes.",
            "conversationnel": "Tu es un passionné qui partage son expérience. Écris de façon naturelle et accessible, comme si tu parlais à un ami. Utilise des anecdotes, des questions rhétoriques.",
            "commercial": "Tu es un consultant qui aide les gens à faire le bon choix. Écris de façon persuasive mais honnête, avec des arguments concrets et des recommandations claires.",
            "academique": "Tu es un chercheur qui vulgarise. Écris de façon structurée et rigoureuse, avec des définitions, des exemples et des nuances. Cite des études.",
        }
        tone_instruction = tone_map.get(site.editorial_tone, tone_map["conversationnel"])
        custom_style = site.editorial_style or ""
        avg_length = site.avg_article_length or 1500

        return f"""Tu rédiges du contenu pour un site web dans la niche "{site.niche or 'généraliste'}".

STYLE ÉDITORIAL:
{tone_instruction}
{custom_style}

CONTRAINTES:
- Longueur cible : environ {avg_length} mots
- SEO : intègre naturellement le mot-clé principal et des variantes
- Structure : utilise des H2 et H3 pertinents
- Pas de formulations IA détectables (pas de "dans le monde d'aujourd'hui", "il est important de noter", etc.)
- Chaque article doit avoir un angle unique et des informations concrètes
- Rédige en français
- Format HTML (h2, h3, p, ul, li) — pas de markdown"""

    def generate_article(self, site: Site, keyword: str, brief: str = "") -> dict:
        """Génère un article complet pour un site"""
        system = self._build_system_prompt(site)
        user_prompt = f"""Rédige un article complet optimisé pour le mot-clé : "{keyword}"

{f"Brief additionnel : {brief}" if brief else ""}

Retourne en JSON avec les clés :
- "title": titre H1 accrocheur (pas le mot-clé brut)
- "meta_title": meta title SEO (max 60 caractères)
- "meta_description": meta description (max 155 caractères)
- "slug": slug URL optimisé
- "content": contenu HTML complet (H2, H3, paragraphes)"""

        response = self.client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=4000,
            system=system,
            messages=[{"role": "user", "content": user_prompt}],
        )
        return {"raw": response.content[0].text, "usage": response.usage}

    def generate_geoloc_article(self, site: Site, keyword: str, city: str, department: str, postal_code: str, region: str) -> dict:
        """Génère un article géolocalisé avec contenu unique par ville"""
        system = self._build_system_prompt(site)
        user_prompt = f"""Rédige un article géolocalisé optimisé pour : "{keyword}"

INFORMATIONS LOCALES À INTÉGRER NATURELLEMENT :
- Ville : {city}
- Code postal : {postal_code}
- Département : {department}
- Région : {region}

IMPORTANT : Le contenu doit être UNIQUE et contenir des informations spécifiques à cette ville.
Mentionne des éléments locaux (quartiers, spécificités, contexte local) pour que chaque page soit différente.

Retourne en JSON avec les clés :
- "title": titre H1 avec la ville
- "meta_title": meta title SEO (max 60 chars)
- "meta_description": meta description (max 155 chars)  
- "slug": slug URL
- "content": contenu HTML complet"""

        response = self.client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=3000,
            system=system,
            messages=[{"role": "user", "content": user_prompt}],
        )
        return {"raw": response.content[0].text, "usage": response.usage}

    def optimize_page(self, site: Site, keyword: str, current_content: str, current_position: float) -> dict:
        """Optimise une page existante pour remonter en position"""
        system = self._build_system_prompt(site)
        user_prompt = f"""Cette page est actuellement en position {current_position} pour le mot-clé "{keyword}".
Elle a besoin d'être optimisée pour remonter.

CONTENU ACTUEL :
{current_content[:3000]}

ANALYSE ET OPTIMISE :
1. Identifie les faiblesses SEO (densité mot-clé, structure, profondeur)
2. Réécris les sections faibles
3. Ajoute du contenu pertinent si nécessaire
4. Optimise le title et la meta description

Retourne en JSON :
- "meta_title": nouveau meta title optimisé
- "meta_description": nouvelle meta description
- "content": contenu HTML optimisé complet
- "changes_summary": résumé des changements effectués"""

        response = self.client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=4000,
            system=system,
            messages=[{"role": "user", "content": user_prompt}],
        )
        return {"raw": response.content[0].text, "usage": response.usage}
