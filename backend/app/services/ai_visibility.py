"""
Visibilité IA en direct — DataForSEO AI Optimization.

Deux tests complémentaires de la visibilité d'un domaine/marque dans les IA :

1. LLM Responses (live) : on pose de VRAIES questions à ChatGPT, Gemini,
   Claude et Perplexity (via DataForSEO, pas besoin de comptes chez chacun)
   et on vérifie si la marque/le domaine est mentionné dans la réponse et
   cité dans les sources.

2. AI Overview Google : pour chaque mot-clé, on regarde la SERP France réelle
   et on détecte si un bloc « AI Overview » est présent, et si le domaine y
   est cité comme source.
"""
import base64
import logging
import re

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

# Plateformes supportées par l'endpoint llm_responses de DataForSEO,
# avec un modèle économique par défaut et des candidats de repli.
PLATFORMS: dict[str, dict] = {
    "chat_gpt": {"label": "ChatGPT", "models": ["gpt-4o-mini", "gpt-4.1-mini", "gpt-4o"]},
    "gemini": {"label": "Gemini", "models": ["gemini-2.0-flash", "gemini-1.5-flash", "gemini-2.5-flash"]},
    "claude": {"label": "Claude", "models": ["claude-3-5-haiku-20241022", "claude-3-5-sonnet-20241022"]},
    "perplexity": {"label": "Perplexity", "models": ["sonar", "sonar-pro"]},
}

# Coûts indicatifs (USD) pour l'estimation affichée avant lancement
COST_PER_LLM_QUESTION = 0.015
COST_PER_AIO_KEYWORD = 0.003


def _collect_strings(node, texts: list, urls: list):
    """Parcourt récursivement une réponse JSON DataForSEO : collecte tout le
    texte visible et toutes les URLs (citations/sources/annotations), quel que
    soit le schéma exact — robuste aux évolutions de l'API."""
    if isinstance(node, dict):
        for k, v in node.items():
            if isinstance(v, str):
                if k in ("url", "source_url", "link"):
                    urls.append(v)
                elif k in ("text", "answer", "content", "title", "snippet"):
                    texts.append(v)
            else:
                _collect_strings(v, texts, urls)
    elif isinstance(node, list):
        for item in node:
            _collect_strings(item, texts, urls)


def _clean_domain(domain: str) -> str:
    d = (domain or "").strip().lower()
    for p in ("https://", "http://", "www."):
        if d.startswith(p):
            d = d[len(p):]
    return d.rstrip("/").split("/")[0]


class AIVisibilityTester:
    """Teste la visibilité IA réelle d'un domaine/marque via DataForSEO."""

    BASE = "https://api.dataforseo.com/v3"

    def __init__(self):
        s = get_settings()
        if not (s.dataforseo_login and s.dataforseo_password):
            raise RuntimeError("Identifiants DataForSEO non configurés")
        creds = base64.b64encode(f"{s.dataforseo_login}:{s.dataforseo_password}".encode()).decode()
        self.client = httpx.Client(
            headers={"Authorization": f"Basic {creds}", "Content-Type": "application/json"},
            proxy=s.proxy_url or None,
            timeout=120,
        )
        self._models_cache: dict[str, list[str]] = {}

    def close(self):
        try:
            self.client.close()
        except Exception:
            pass

    def _post(self, endpoint: str, payload: list[dict]) -> dict | None:
        try:
            resp = self.client.post(f"{self.BASE}{endpoint}", json=payload)
            if resp.status_code != 200:
                logger.warning("DFS %s -> %s %s", endpoint, resp.status_code, resp.text[:300])
                return None
            return resp.json()
        except Exception as exc:
            logger.warning("DFS %s exception: %s", endpoint, exc)
            return None

    def _available_models(self, platform: str) -> list[str]:
        """Liste des modèles disponibles pour une plateforme (avec cache)."""
        if platform in self._models_cache:
            return self._models_cache[platform]
        models: list[str] = []
        try:
            resp = self.client.get(f"{self.BASE}/ai_optimization/{platform}/llm_responses/models")
            if resp.status_code == 200:
                texts, _ = [], []
                data = resp.json()
                for task in data.get("tasks") or []:
                    for res in task.get("result") or []:
                        # le schéma renvoie soit une liste de strings, soit des dicts {model_name}
                        items = res if isinstance(res, list) else res.get("items", []) or [res]
                        for it in items:
                            if isinstance(it, str):
                                models.append(it)
                            elif isinstance(it, dict) and it.get("model_name"):
                                models.append(it["model_name"])
        except Exception:
            pass
        self._models_cache[platform] = models
        return models

    def _pick_model(self, platform: str) -> str:
        """Choisit un modèle : premier candidat par défaut disponible, sinon le
        premier modèle listé par l'API, sinon le candidat par défaut brut."""
        candidates = PLATFORMS[platform]["models"]
        available = self._available_models(platform)
        if available:
            for c in candidates:
                if c in available:
                    return c
            # candidat proche (préfixe) puis premier disponible
            for c in candidates:
                for a in available:
                    if a.startswith(c.split("-2024")[0]):
                        return a
            return available[0]
        return candidates[0]

    # ------------------------------------------------------------------
    # 1. Questions réelles posées aux LLM
    # ------------------------------------------------------------------

    def ask_llm(self, platform: str, question: str, domain: str, brand: str) -> dict:
        """Pose une question à un LLM et vérifie mention/citation du domaine."""
        model = self._pick_model(platform)
        payload = [{
            "user_prompt": question,
            "model_name": model,
            "web_search": True,          # active la recherche web (citations)
            "max_output_tokens": 1024,
        }]
        data = self._post(f"/ai_optimization/{platform}/llm_responses/live", payload)
        result = {
            "platform": platform, "label": PLATFORMS[platform]["label"],
            "model": model, "question": question,
            "mentioned": False, "cited": False, "snippet": "", "sources": [],
            "error": "",
        }
        if not data:
            result["error"] = "appel API en échec"
            return result
        task = (data.get("tasks") or [{}])[0]
        if task.get("status_code") and task["status_code"] >= 40000:
            result["error"] = task.get("status_message", "erreur API")[:120]
            return result

        texts: list[str] = []
        urls: list[str] = []
        _collect_strings(task.get("result"), texts, urls)
        full_text = "\n".join(texts)

        dom = _clean_domain(domain)
        brand_l = (brand or "").strip().lower()
        text_l = full_text.lower()

        # Mention : marque ou domaine dans le texte de la réponse
        needles = [n for n in (brand_l, dom, dom.split(".")[0] if dom else "") if n and len(n) >= 3]
        for n in needles:
            idx = text_l.find(n)
            if idx >= 0:
                result["mentioned"] = True
                start = max(0, idx - 80)
                result["snippet"] = ("…" if start else "") + full_text[start:idx + len(n) + 120].strip() + "…"
                break

        # Citation : domaine dans une URL source
        seen = set()
        for u in urls:
            ul = u.lower()
            host = _clean_domain(ul)
            if host and host not in seen:
                seen.add(host)
                result["sources"].append(host)
            if dom and dom in ul:
                result["cited"] = True
        result["sources"] = result["sources"][:8]
        return result

    # ------------------------------------------------------------------
    # 2. AI Overview Google par mot-clé
    # ------------------------------------------------------------------

    def check_ai_overview(self, keyword: str, domain: str,
                          location: str = "France", language: str = "fr") -> dict:
        """Détecte le bloc AI Overview sur la SERP Google d'un mot-clé et
        vérifie si le domaine y est cité comme source."""
        payload = [{
            "keyword": keyword,
            "location_name": location,
            "language_code": language,
            "depth": 10,
            "load_async_ai_overview": True,
        }]
        data = self._post("/serp/google/organic/live/advanced", payload)
        out = {"keyword": keyword, "has_aio": False, "cited": False,
               "sources": [], "error": ""}
        if not data:
            out["error"] = "appel API en échec"
            return out
        dom = _clean_domain(domain)
        for task in data.get("tasks") or []:
            for res in task.get("result") or []:
                for item in res.get("items") or []:
                    if item.get("type") != "ai_overview":
                        continue
                    out["has_aio"] = True
                    texts, urls = [], []
                    _collect_strings(item, texts, urls)
                    seen = set()
                    for u in urls:
                        host = _clean_domain(u.lower())
                        if host and host not in seen:
                            seen.add(host)
                            out["sources"].append(host)
                        if dom and dom in u.lower():
                            out["cited"] = True
        out["sources"] = out["sources"][:10]
        return out
