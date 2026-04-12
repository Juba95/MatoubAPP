from sqlalchemy.orm import Session
from datetime import datetime, timezone
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

    def scan_site(self, site: Site, max_actions: int = 50) -> dict:
        """Scan complet d'un site — crée les actions dans la file"""
        results = {"optimizations": 0, "new_content": 0, "errors": []}

        try:
            sc = SearchConsoleClient(site)
            # Période courante : J-10 → J-3 (délai SC de 3j)
            current_data = sc.get_performance(days=7, dimensions=["query", "page"])
            # Période précédente : J-17 → J-10 (même durée, décalée de 7j)
            previous_data = sc.get_performance(days=7, dimensions=["query", "page"], offset_days=7)
        except Exception as e:
            results["errors"].append(f"Search Console error: {str(e)}")
            return results

        # Indexer les données actuelles par query
        current_by_query = {}
        for row in current_data:
            keys = row.get("keys", [])
            if len(keys) >= 2:
                query, page = keys[0], keys[1]
                current_by_query[query] = {
                    "page": page,
                    "position": row.get("position", 0),
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
                previous_by_query[query] = {
                    "position": row.get("position", 0),
                    "impressions": row.get("impressions", 0),
                }

        actions_to_create = []

        # 1. Détecter les baisses de positions
        for query, data in current_by_query.items():
            prev = previous_by_query.get(query)
            if not prev:
                continue

            position_delta = data["position"] - prev["position"]

            # Baisse de 3+ positions = action d'optimisation
            if position_delta >= 3:
                impact = data["impressions"] * abs(position_delta)
                actions_to_create.append({
                    "action_type": ActionType.OPTIMIZE,
                    "title": f"{site.domain} — \"{query}\"",
                    "description": f"Position {prev['position']:.0f} → {data['position']:.0f} ({position_delta:+.0f}). Page: {data['page']}",
                    "keyword": query,
                    "search_volume": data["impressions"],
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
                impact = data["impressions"] * (data["position"] / 10)
                actions_to_create.append({
                    "action_type": ActionType.CREATE,
                    "title": f"{site.domain} — \"{query}\"",
                    "description": f"Impressions fortes ({data['impressions']}) mais position faible ({data['position']:.0f}). Créer une page dédiée.",
                    "keyword": query,
                    "search_volume": data["impressions"],
                    "current_position": data["position"],
                    "impressions": data["impressions"],
                    "impact_score": impact,
                    "estimated_api_cost": 0.03,
                })

        # Trier par impact et limiter
        actions_to_create.sort(key=lambda a: a["impact_score"], reverse=True)
        actions_to_create = actions_to_create[:max_actions]

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
