from sqlalchemy.orm import Session
from datetime import datetime, timezone
from app.config import get_settings
from app.models.site import Site
from app.models.page import Page
from app.models.keyword import Keyword, KeywordHistory
from app.models.action import Action, ActionType, ActionStatus
from app.services.search_console import SearchConsoleClient
from app.services.dataforseo import DataForSEOClient


class SEOAgent:
    """
    Agent SEO autonome qui :
    1. Tire les données SC + DataForSEO
    2. Détecte les baisses de positions
    3. Identifie les opportunités de contenu
    4. Crée des actions dans la file de validation
    """

    def __init__(self, db: Session):
        self.db = db
        self.dataforseo = DataForSEOClient()

    def _sync_keywords(self, site: Site, current_by_query: dict, previous_by_query: dict) -> int:
        """Upsert SC data into the Keyword table and add history entries."""
        synced = 0
        for query, data in current_by_query.items():
            kw = self.db.query(Keyword).filter(
                Keyword.site_id == site.id,
                Keyword.keyword == query,
            ).first()

            prev = previous_by_query.get(query)
            prev_position = prev["position"] if prev else None

            if kw is None:
                kw = Keyword(
                    site_id=site.id,
                    keyword=query,
                    current_position=data["position"],
                    previous_position=prev_position,
                    best_position=data["position"],
                    impressions=data["impressions"],
                    clicks=data["clicks"],
                    ctr=data["ctr"],
                )
                self.db.add(kw)
            else:
                kw.previous_position = prev_position
                kw.current_position = data["position"]
                kw.impressions = data["impressions"]
                kw.clicks = data["clicks"]
                kw.ctr = data["ctr"]
                if kw.best_position is None or data["position"] < kw.best_position:
                    kw.best_position = data["position"]

            self.db.flush()  # ensure kw.id is available

            history = KeywordHistory(
                keyword_id=kw.id,
                position=data["position"],
                impressions=data["impressions"],
                clicks=data["clicks"],
            )
            self.db.add(history)
            synced += 1

        return synced

    def _enrich_with_dataforseo(self, site: Site, current_by_query: dict) -> int:
        """Enrich top keywords with DataForSEO search volumes. Returns count enriched."""
        settings = get_settings()
        if not settings.dataforseo_login:
            return 0

        # Sort keywords by impressions descending and take the top ones
        sorted_queries = sorted(
            current_by_query.keys(),
            key=lambda q: current_by_query[q]["impressions"],
            reverse=True,
        )

        enriched = 0
        # Process in batches of 100
        for i in range(0, len(sorted_queries), 100):
            batch = sorted_queries[i:i + 100]
            try:
                response = self.dataforseo.get_keyword_data(batch)
                tasks = response.get("tasks", [])
                for task in tasks:
                    result_items = (task.get("result") or [])
                    for item in result_items:
                        kw_text = item.get("keyword")
                        volume = item.get("search_volume")
                        info = item.get("keyword_info") or {}
                        if volume is None:
                            volume = info.get("search_volume")
                        difficulty = item.get("keyword_difficulty") or item.get("competition") or info.get("competition")

                        if kw_text is None:
                            continue

                        kw_obj = self.db.query(Keyword).filter(
                            Keyword.site_id == site.id,
                            Keyword.keyword == kw_text,
                        ).first()
                        if kw_obj:
                            if volume is not None:
                                kw_obj.search_volume = int(volume)
                            if difficulty is not None:
                                kw_obj.difficulty = float(difficulty)
                            enriched += 1
            except Exception:
                # DataForSEO failure should not break the scan
                continue

        return enriched

    def _get_search_volume(self, site_id: int, query: str, fallback_impressions: int) -> int:
        """Return search_volume from Keyword if available, else fallback to impressions."""
        kw = self.db.query(Keyword).filter(
            Keyword.site_id == site_id,
            Keyword.keyword == query,
        ).first()
        if kw and kw.search_volume and kw.search_volume > 0:
            return kw.search_volume
        return fallback_impressions

    def scan_site(self, site: Site, max_actions: int = 50) -> dict:
        """Scan complet d'un site — crée les actions dans la file"""
        results = {
            "optimizations": 0,
            "new_content": 0,
            "keywords_synced": 0,
            "dataforseo_enriched": 0,
            "errors": [],
        }

        try:
            sc = SearchConsoleClient(site)
            # Période courante : J-10 → J-3 (délai SC de 3j)
            current_data = sc.get_performance(days=7, dimensions=["query", "page"])
            # Période précédente : J-17 → J-10 (même durée, décalée de 7j)
            previous_data = sc.get_performance(days=7, dimensions=["query", "page"], offset_days=7)
        except Exception as e:
            results["errors"].append(f"Search Console error: {str(e)}")
            return results

        # Pages indexées
        try:
            idx = sc.get_indexed_pages()
            results["indexed_pages"] = idx.get("indexed_estimate", 0)
        except Exception:
            results["indexed_pages"] = 0

        # Indexer les données actuelles par query
        # Garder la MEILLEURE page par query (celle avec la meilleure position)
        current_by_query = {}
        for row in current_data:
            keys = row.get("keys", [])
            if len(keys) >= 2:
                query, page = keys[0], keys[1]
                pos = row.get("position", 0)
                existing = current_by_query.get(query)
                if existing is None or pos < existing["position"]:
                    current_by_query[query] = {
                        "page": page,
                        "position": pos,
                        "impressions": row.get("impressions", 0),
                        "clicks": row.get("clicks", 0),
                        "ctr": row.get("ctr", 0),
                    }

        # Indexer les données précédentes
        previous_by_query = {}
        for row in previous_data:
            keys = row.get("keys", [])
            if len(keys) >= 2:
                query = keys[0]
                pos = row.get("position", 0)
                existing = previous_by_query.get(query)
                if existing is None or pos < existing["position"]:
                    previous_by_query[query] = {
                        "position": pos,
                        "impressions": row.get("impressions", 0),
                    }

        # --- Step 1: Sync keywords to DB ---
        try:
            results["keywords_synced"] = self._sync_keywords(site, current_by_query, previous_by_query)
            self.db.flush()
        except Exception as e:
            results["errors"].append(f"Keyword sync error: {str(e)}")

        # --- Step 2: Enrich with DataForSEO ---
        try:
            results["dataforseo_enriched"] = self._enrich_with_dataforseo(site, current_by_query)
            self.db.flush()
        except Exception as e:
            results["errors"].append(f"DataForSEO error: {str(e)}")

        actions_to_create = []

        # 1. Détecter les baisses de positions
        for query, data in current_by_query.items():
            prev = previous_by_query.get(query)
            if not prev:
                continue

            position_delta = data["position"] - prev["position"]

            # Baisse de 3+ positions = action d'optimisation
            if position_delta >= 3:
                volume = self._get_search_volume(site.id, query, data["impressions"])
                impact = volume * abs(position_delta)
                actions_to_create.append({
                    "action_type": ActionType.OPTIMIZE,
                    "title": f"{site.name} — \"{query}\"",
                    "description": f"Position {prev['position']:.0f} → {data['position']:.0f} ({position_delta:+.0f}). Page: {data['page']}",
                    "keyword": query,
                    "search_volume": volume,
                    "current_position": data["position"],
                    "previous_position": prev["position"],
                    "position_delta": -position_delta,
                    "impressions": data["impressions"],
                    "impact_score": impact,
                    "estimated_api_cost": 0.04,
                })

        # 2. Identifier les opportunités de contenu
        # Requêtes avec impressions élevées mais pas de page dédiée
        high_impression_queries = sorted(
            current_by_query.items(),
            key=lambda x: x[1]["impressions"],
            reverse=True,
        )

        existing_keywords = {
            a["keyword"] for a in actions_to_create
        }

        for query, data in high_impression_queries:
            if query in existing_keywords:
                continue
            # Position > 20 et impressions fortes = opportunité de contenu dédié
            if data["position"] > 20 and data["impressions"] > 50:
                volume = self._get_search_volume(site.id, query, data["impressions"])
                impact = volume * (data["position"] / 10)
                actions_to_create.append({
                    "action_type": ActionType.CREATE,
                    "title": f"{site.name} — \"{query}\"",
                    "description": f"Impressions fortes ({data['impressions']}) mais position faible ({data['position']:.0f}). Créer une page dédiée.",
                    "keyword": query,
                    "search_volume": volume,
                    "current_position": data["position"],
                    "impressions": data["impressions"],
                    "impact_score": impact,
                    "estimated_api_cost": 0.03,
                })

        # Trier par impact et limiter — max 3 actions par page pour diversifier
        actions_to_create.sort(key=lambda a: a["impact_score"], reverse=True)
        page_counts = {}
        diversified = []
        for a in actions_to_create:
            page_url = a.get("description", "").split("Page: ")[-1] if "Page: " in a.get("description", "") else ""
            page_counts[page_url] = page_counts.get(page_url, 0) + 1
            if page_counts[page_url] <= 3:
                diversified.append(a)
            if len(diversified) >= max_actions:
                break
        actions_to_create = diversified

        # Créer les actions en BDD
        for action_data in actions_to_create:
            # Vérifier qu'une action similaire n'existe pas déjà
            existing = self.db.query(Action).filter(
                Action.site_id == site.id,
                Action.keyword == action_data["keyword"],
                Action.status == ActionStatus.PENDING,
            ).first()
            if existing:
                continue

            action = Action(site_id=site.id, **action_data)
            self.db.add(action)

            if action_data["action_type"] == ActionType.OPTIMIZE:
                results["optimizations"] += 1
            else:
                results["new_content"] += 1

        # Mettre à jour les stats du site
        site.last_scan_at = datetime.now(timezone.utc) if hasattr(site, 'last_scan_at') else None

        self.db.commit()
        return results

    def scan_all_sites(self, max_actions_per_site: int = 50) -> dict:
        """Scan tous les sites actifs"""
        sites = self.db.query(Site).filter(Site.is_active == True).all()
        total_results = {"sites_scanned": 0, "total_actions": 0, "errors": []}

        for site in sites:
            result = self.scan_site(site, max_actions_per_site)
            total_results["sites_scanned"] += 1
            total_results["total_actions"] += result["optimizations"] + result["new_content"]
            total_results["errors"].extend(result["errors"])

        return total_results
