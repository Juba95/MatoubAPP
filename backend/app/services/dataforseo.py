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
