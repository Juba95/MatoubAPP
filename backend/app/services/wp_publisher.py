import httpx
import base64
from app.models.site import Site


class WPPublisher:
    """Publie du contenu sur WordPress via l'API REST"""

    def __init__(self, site: Site, proxy_url: str | None = None):
        # Nettoyer le domaine si l'utilisateur a mis https:// ou www.
        domain = site.domain.strip().rstrip("/")
        if not domain.startswith("http"):
            domain = f"https://{domain}"
        self.base_url = f"{domain}/wp-json/wp/v2"
        creds = base64.b64encode(
            f"{site.wp_username}:{site.wp_app_password}".encode()
        ).decode()
        self.headers = {
            "Authorization": f"Basic {creds}",
            "Content-Type": "application/json",
        }
        self.proxy_url = proxy_url or None

    def _client(self):
        kwargs = {"headers": self.headers, "timeout": 30}
        if self.proxy_url:
            kwargs["proxy"] = self.proxy_url
        return httpx.Client(**kwargs)

    def create_post(
        self,
        title: str,
        content: str,
        slug: str = "",
        status: str = "draft",
        meta_title: str = "",
        meta_description: str = "",
        categories: list[int] | None = None,
    ) -> dict:
        """Créer un nouveau post WordPress"""
        payload = {
            "title": title,
            "content": content,
            "status": status,
            "slug": slug,
        }
        if categories:
            payload["categories"] = categories

        # Meta SEO via Yoast / RankMath / SEOPress
        meta = {}
        if meta_title:
            meta["yoast_wpseo_title"] = meta_title
            meta["rank_math_title"] = meta_title
            meta["_seopress_titles_title"] = meta_title
        if meta_description:
            meta["yoast_wpseo_metadesc"] = meta_description
            meta["rank_math_description"] = meta_description
            meta["_seopress_titles_desc"] = meta_description
        if meta:
            payload["meta"] = meta

        with self._client() as client:
            resp = client.post(f"{self.base_url}/posts", json=payload)
            resp.raise_for_status()
            data = resp.json()
            return {
                "wp_post_id": data["id"],
                "url": data["link"],
                "status": data["status"],
            }

    def update_post(
        self,
        post_id: int,
        title: str = "",
        content: str = "",
        meta_title: str = "",
        meta_description: str = "",
    ) -> dict:
        """Mettre à jour un post existant"""
        payload = {}
        if title:
            payload["title"] = title
        if content:
            payload["content"] = content

        meta = {}
        if meta_title:
            meta["yoast_wpseo_title"] = meta_title
            meta["rank_math_title"] = meta_title
            meta["_seopress_titles_title"] = meta_title
        if meta_description:
            meta["yoast_wpseo_metadesc"] = meta_description
            meta["rank_math_description"] = meta_description
            meta["_seopress_titles_desc"] = meta_description
        if meta:
            payload["meta"] = meta

        with self._client() as client:
            resp = client.post(f"{self.base_url}/posts/{post_id}", json=payload)
            resp.raise_for_status()
            data = resp.json()
            return {
                "wp_post_id": data["id"],
                "url": data["link"],
                "status": data["status"],
            }

    def get_post(self, post_id: int) -> dict:
        with self._client() as client:
            resp = client.get(f"{self.base_url}/posts/{post_id}")
            resp.raise_for_status()
            return resp.json()

    def get_content(self, post_id: int, content_type: str = "post") -> str:
        """Récupère le contenu HTML d'un post ou d'une page."""
        endpoint = "pages" if content_type == "page" else "posts"
        with self._client() as client:
            resp = client.get(f"{self.base_url}/{endpoint}/{post_id}")
            resp.raise_for_status()
            data = resp.json()
            return data.get("content", {}).get("rendered", "")

    def list_posts(self, per_page: int = 100, page: int = 1) -> list:
        with self._client() as client:
            resp = client.get(
                f"{self.base_url}/posts",
                params={"per_page": per_page, "page": page, "status": "publish,draft"},
            )
            resp.raise_for_status()
            return resp.json()

    def find_post_by_slug(self, slug: str) -> dict | None:
        """Cherche un post/page publié par son slug."""
        with self._client() as client:
            # Chercher dans les posts
            resp = client.get(f"{self.base_url}/posts", params={"slug": slug, "status": "publish,draft"})
            resp.raise_for_status()
            posts = resp.json()
            if posts:
                return {"wp_post_id": posts[0]["id"], "url": posts[0]["link"], "type": "post"}
            # Chercher dans les pages
            resp = client.get(f"{self.base_url}/pages", params={"slug": slug, "status": "publish,draft"})
            resp.raise_for_status()
            pages = resp.json()
            if pages:
                return {"wp_post_id": pages[0]["id"], "url": pages[0]["link"], "type": "page"}
        return None

    def update_page(self, page_id: int, title: str = "", content: str = "",
                    meta_title: str = "", meta_description: str = "") -> dict:
        """Mettre à jour une page WordPress (pas un post)."""
        payload = {}
        if title:
            payload["title"] = title
        if content:
            payload["content"] = content
        meta = {}
        if meta_title:
            meta["yoast_wpseo_title"] = meta_title
            meta["rank_math_title"] = meta_title
            meta["_seopress_titles_title"] = meta_title
        if meta_description:
            meta["yoast_wpseo_metadesc"] = meta_description
            meta["rank_math_description"] = meta_description
            meta["_seopress_titles_desc"] = meta_description
        if meta:
            payload["meta"] = meta
        with self._client() as client:
            resp = client.post(f"{self.base_url}/pages/{page_id}", json=payload)
            resp.raise_for_status()
            data = resp.json()
            return {"wp_post_id": data["id"], "url": data["link"], "status": data["status"]}

    def create_category(self, name: str, slug: str = "") -> dict:
        with self._client() as client:
            payload = {"name": name}
            if slug:
                payload["slug"] = slug
            resp = client.post(f"{self.base_url}/categories", json=payload)
            resp.raise_for_status()
            return resp.json()

    def test_connection(self) -> dict:
        """Teste la connexion au site WordPress"""
        try:
            with self._client() as client:
                resp = client.get(f"{self.base_url}/posts", params={"per_page": 1})
                resp.raise_for_status()
                return {"ok": True, "status_code": resp.status_code}
        except Exception as e:
            return {"ok": False, "error": str(e)}
