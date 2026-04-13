import httpx
import json
from datetime import datetime, timedelta
from urllib.parse import quote
from app.models.site import Site


class SearchConsoleClient:
    """Client Google Search Console via OAuth par site"""

    TOKEN_URL = "https://oauth2.googleapis.com/token"
    API_URL = "https://www.googleapis.com/webmasters/v3"
    SEARCHANALYTICS_URL = "https://searchconsole.googleapis.com/webmasters/v3"

    def __init__(self, site: Site, proxy_url: str | None = None):
        self.token_data = json.loads(site.sc_token_json) if site.sc_token_json else None
        self.proxy_url = proxy_url or None
        # Utiliser sc_property si défini, sinon construire depuis le domaine
        if hasattr(site, 'sc_property') and site.sc_property:
            self.site_url = quote(site.sc_property, safe='')
        else:
            domain = site.domain.strip()
            for prefix in ("https://", "http://"):
                if domain.startswith(prefix):
                    domain = domain[len(prefix):]
            if domain.startswith("www."):
                domain = domain[4:]
            domain = domain.rstrip("/")
            self.site_url = quote(f"sc-domain:{domain}", safe='')

    def _get_access_token(self) -> str:
        """Refresh le token OAuth"""
        if not self.token_data:
            raise ValueError("No Search Console token configured for this site")
        kwargs = {"data": {
            "client_id": self.token_data["client_id"],
            "client_secret": self.token_data["client_secret"],
            "refresh_token": self.token_data["refresh_token"],
            "grant_type": "refresh_token",
        }}
        if self.proxy_url:
            kwargs["proxy"] = self.proxy_url
        resp = httpx.post(self.TOKEN_URL, **kwargs)
        resp.raise_for_status()
        return resp.json()["access_token"]

    def _client(self):
        token = self._get_access_token()
        kwargs = {
            "headers": {"Authorization": f"Bearer {token}"},
            "timeout": 30,
        }
        if self.proxy_url:
            kwargs["proxy"] = self.proxy_url
        return httpx.Client(**kwargs)

    def get_performance(
        self,
        days: int = 7,
        dimensions: list[str] | None = None,
        row_limit: int = 1000,
        offset_days: int = 0,
    ) -> list:
        """Récupère les données de performance Search Console.

        offset_days=0  → période courante  (J-10 → J-3)
        offset_days=7  → période précédente (J-17 → J-10)
        """
        if dimensions is None:
            dimensions = ["query", "page"]

        end_date = datetime.now() - timedelta(days=3 + offset_days)  # SC a 3j de délai
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
        """Nombre de pages indexées via l'API sitemaps GSC."""
        try:
            with self._client() as client:
                resp = client.get(f"{self.API_URL}/sites/{self.site_url}/sitemaps")
                resp.raise_for_status()
                raw = resp.json()
                sitemaps = raw.get("sitemap", [])
                total_indexed = 0
                total_submitted = 0
                for s in sitemaps:
                    for content in s.get("contents", []):
                        total_submitted += int(content.get("submitted", 0))
                        total_indexed += int(content.get("indexed", 0))
                if total_indexed > 0 or total_submitted > 0:
                    return {"indexed": total_indexed, "submitted": total_submitted, "source": "sitemaps"}
        except Exception as e:
            print(f"[GSC SITEMAPS ERROR] {e}")
        # Fallback : compter les pages uniques avec impressions
        try:
            pages = self.get_performance(days=28, dimensions=["page"], row_limit=5000)
            count = len(set(row["keys"][0] for row in pages if row.get("keys")))
            return {"indexed": count, "submitted": 0, "source": "analytics_fallback"}
        except Exception:
            return {"indexed": 0, "submitted": 0, "source": "error"}

    def list_properties(self) -> list[dict]:
        """Liste toutes les propriétés Search Console du compte connecté."""
        with self._client() as client:
            resp = client.get(f"{self.API_URL}/sites")
            resp.raise_for_status()
            entries = resp.json().get("siteEntry", [])
            return [
                {"site_url": e["siteUrl"], "permission": e.get("permissionLevel", "")}
                for e in entries
            ]
