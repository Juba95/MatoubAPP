import httpx
import json
from datetime import datetime, timedelta
from app.models.site import Site


class SearchConsoleClient:
    """Client Google Search Console via OAuth par site"""

    TOKEN_URL = "https://oauth2.googleapis.com/token"
    API_URL = "https://www.googleapis.com/webmasters/v3"
    SEARCHANALYTICS_URL = "https://searchconsole.googleapis.com/webmasters/v3"

    def __init__(self, site: Site):
        self.site_url = f"sc-domain:{site.domain}"
        self.token_data = json.loads(site.sc_token_json) if site.sc_token_json else None

    def _get_access_token(self) -> str:
        """Refresh le token OAuth"""
        if not self.token_data:
            raise ValueError("No Search Console token configured for this site")
        resp = httpx.post(self.TOKEN_URL, data={
            "client_id": self.token_data["client_id"],
            "client_secret": self.token_data["client_secret"],
            "refresh_token": self.token_data["refresh_token"],
            "grant_type": "refresh_token",
        })
        resp.raise_for_status()
        return resp.json()["access_token"]

    def _client(self):
        token = self._get_access_token()
        return httpx.Client(
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )

    def get_performance(
        self,
        days: int = 7,
        dimensions: list[str] | None = None,
        row_limit: int = 1000,
    ) -> list:
        """Récupère les données de performance Search Console"""
        if dimensions is None:
            dimensions = ["query", "page"]

        end_date = datetime.now() - timedelta(days=3)  # SC a 3j de délai
        start_date = end_date - timedelta(days=days)

        payload = {
            "startDate": start_date.strftime("%Y-%m-%d"),
            "endDate": end_date.strftime("%Y-%m-%d"),
            "dimensions": dimensions,
            "rowLimit": row_limit,
            "startRow": 0,
        }

        with self._client() as client:
            resp = client.post(
                f"{self.SEARCHANALYTICS_URL}/sites/{self.site_url}/searchAnalytics/query",
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("rows", [])

    def get_pages_performance(self, days: int = 7) -> list:
        """Performance par page"""
        return self.get_performance(days=days, dimensions=["page"])

    def get_queries_performance(self, days: int = 7) -> list:
        """Performance par requête"""
        return self.get_performance(days=days, dimensions=["query"])

    def get_page_queries(self, page_url: str, days: int = 7) -> list:
        """Requêtes pour une page spécifique"""
        end_date = datetime.now() - timedelta(days=3)
        start_date = end_date - timedelta(days=days)

        payload = {
            "startDate": start_date.strftime("%Y-%m-%d"),
            "endDate": end_date.strftime("%Y-%m-%d"),
            "dimensions": ["query"],
            "dimensionFilterGroups": [{
                "filters": [{
                    "dimension": "page",
                    "operator": "equals",
                    "expression": page_url,
                }]
            }],
            "rowLimit": 100,
        }

        with self._client() as client:
            resp = client.post(
                f"{self.SEARCHANALYTICS_URL}/sites/{self.site_url}/searchAnalytics/query",
                json=payload,
            )
            resp.raise_for_status()
            return resp.json().get("rows", [])

    def get_indexed_pages(self) -> dict:
        """Nombre de pages indexées via l'API d'inspection"""
        with self._client() as client:
            resp = client.get(
                f"{self.API_URL}/sites/{self.site_url}/sitemaps",
            )
            resp.raise_for_status()
            sitemaps = resp.json().get("sitemap", [])
            total = sum(
                int(s.get("contents", [{}])[0].get("submitted", 0))
                for s in sitemaps
                if s.get("contents")
            )
            return {"indexed_estimate": total, "sitemaps": len(sitemaps)}
