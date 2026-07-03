import httpx
import base64
from app.config import get_settings


class DataForSEOClient:
    BASE_URL = "https://api.dataforseo.com/v3"

    def __init__(self):
        s = get_settings()
        creds = base64.b64encode(f"{s.dataforseo_login}:{s.dataforseo_password}".encode()).decode()
        self.headers = {
            "Authorization": f"Basic {creds}",
            "Content-Type": "application/json",
        }
        self.proxy = s.proxy_url or None

    def _client(self):
        return httpx.Client(headers=self.headers, proxy=self.proxy, timeout=30)

    def get_serp_positions(self, keyword: str, location: str = "France", language: str = "fr"):
        """Récupérer les positions SERP pour un mot-clé (mode Standard = moins cher)"""
        with self._client() as client:
            resp = client.post(f"{self.BASE_URL}/serp/google/organic/task_post", json=[{
                "keyword": keyword,
                "location_name": location,
                "language_code": language,
                "depth": 100,
            }])
            resp.raise_for_status()
            data = resp.json()
            if data.get("tasks"):
                task_id = data["tasks"][0].get("id")
                return {"task_id": task_id, "status": "queued"}
            return {"error": data}

    def get_task_result(self, task_id: str):
        """Récupérer le résultat d'une tâche SERP"""
        with self._client() as client:
            resp = client.get(f"{self.BASE_URL}/serp/google/organic/task_get/regular/{task_id}")
            resp.raise_for_status()
            return resp.json()

    def get_keyword_data(self, keywords: list[str], location: str = "France"):
        """Volume de recherche et difficulté pour une liste de mots-clés"""
        with self._client() as client:
            resp = client.post(f"{self.BASE_URL}/keywords_data/google_ads/search_volume/live", json=[{
                "keywords": keywords,
                "location_name": location,
                "language_code": "fr",
            }])
            resp.raise_for_status()
            return resp.json()

    def get_people_also_ask(self, keyword: str, location: str = "France", language: str = "fr") -> list[str]:
        """Questions « People Also Ask » réelles de la SERP Google pour un mot-clé.

        Utilisées pour générer des FAQ qui reprennent mot pour mot les questions
        posées sur Google (ciblage featured snippet / position 0).
        """
        with self._client() as client:
            resp = client.post(f"{self.BASE_URL}/serp/google/organic/live/advanced", json=[{
                "keyword": keyword,
                "location_name": location,
                "language_code": language,
                "depth": 10,
                "people_also_ask_click_depth": 1,
            }])
            resp.raise_for_status()
            data = resp.json()

        questions: list[str] = []
        for task in data.get("tasks") or []:
            for result in task.get("result") or []:
                for item in result.get("items") or []:
                    if item.get("type") != "people_also_ask":
                        continue
                    for paa in item.get("items") or []:
                        q = (paa.get("title") or "").strip()
                        if q and q not in questions:
                            questions.append(q)
        return questions

    def get_serp_top_results(self, keyword: str, location: str = "France", language: str = "fr", n: int = 10) -> list[dict]:
        """Top résultats organiques réels d'une SERP Google (titre + snippet + url).

        Sert à l'information gain : comprendre ce que couvrent les pages qui
        rankent déjà pour produire un contenu qui les dépasse.
        """
        with self._client() as client:
            resp = client.post(f"{self.BASE_URL}/serp/google/organic/live/advanced", json=[{
                "keyword": keyword,
                "location_name": location,
                "language_code": language,
                "depth": max(10, n),
            }])
            resp.raise_for_status()
            data = resp.json()

        out: list[dict] = []
        for task in data.get("tasks") or []:
            for result in task.get("result") or []:
                for item in result.get("items") or []:
                    if item.get("type") != "organic":
                        continue
                    out.append({
                        "title": item.get("title", ""),
                        "description": item.get("description", ""),
                        "url": item.get("url", ""),
                    })
                    if len(out) >= n:
                        return out
        return out

    def get_domain_position(self, keyword: str, target_domain: str,
                            location: str = "France", language: str = "fr",
                            depth: int = 100) -> dict:
        """Position d'un domaine dans la SERP Google pour un mot-clé (live, sans proxy).

        Retourne le rang organique (1..depth) du domaine cible, l'URL classée, et
        le top 3 concurrents. rank=None si absent du top `depth`. Légal et propre :
        DataForSEO renvoie la SERP Google — aucun scraping ni proxy nécessaire.
        """
        tgt = target_domain.replace("https://", "").replace("http://", "").replace("www.", "").rstrip("/").lower()
        with self._client() as client:
            resp = client.post(f"{self.BASE_URL}/serp/google/organic/live/advanced", json=[{
                "keyword": keyword,
                "location_name": location,
                "language_code": language,
                "depth": depth,
            }])
            resp.raise_for_status()
            data = resp.json()

        rank = None
        ranked_url = ""
        top3: list[dict] = []
        for task in data.get("tasks") or []:
            for result in task.get("result") or []:
                for item in result.get("items") or []:
                    if item.get("type") != "organic":
                        continue
                    pos = item.get("rank_group") or item.get("rank_absolute")
                    dom = (item.get("domain") or "").replace("www.", "").lower()
                    url = item.get("url", "")
                    if len(top3) < 3:
                        top3.append({"position": pos, "domain": dom, "url": url})
                    if rank is None and (dom == tgt or tgt in url.lower()):
                        rank = pos
                        ranked_url = url
        return {
            "keyword": keyword,
            "location": location,
            "language": language,
            "rank": rank,
            "url": ranked_url,
            "top3": top3,
        }

    def get_ranked_keywords(self, domain: str, location: str = "France"):
        """Mots-clés sur lesquels un domaine est positionné"""
        with self._client() as client:
            resp = client.post(f"{self.BASE_URL}/dataforseo_labs/google/ranked_keywords/live", json=[{
                "target": domain,
                "location_name": location,
                "language_code": "fr",
                "limit": 100,
                "order_by": ["keyword_data.keyword_info.search_volume,desc"],
            }])
            resp.raise_for_status()
            return resp.json()
