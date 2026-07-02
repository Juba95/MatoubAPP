"""
Tâches Celery — pipeline génération + publication.
Lancé après validation d'une action dans la file.
"""
import random
from datetime import datetime, timezone, timedelta

from app.celery_app import celery_app


def _parse_claude_json(raw: str) -> dict:
    """Parse le JSON depuis la réponse Claude — gère les blocs ```json et le texte autour."""
    import json
    # Tenter le bloc ```json ... ``` en premier
    import re
    code_block = re.search(r'```(?:json)?\s*(\{[\s\S]*?\})\s*```', raw)
    if code_block:
        try:
            return json.loads(code_block.group(1))
        except json.JSONDecodeError:
            pass
    # Tenter de trouver le JSON brut (accolades équilibrées)
    depth = 0
    start = None
    for i, c in enumerate(raw):
        if c == '{':
            if depth == 0:
                start = i
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0 and start is not None:
                try:
                    return json.loads(raw[start:i + 1])
                except json.JSONDecodeError:
                    start = None
    return {}


def get_publish_eta(site_config) -> datetime | None:
    """
    Calcule une heure de publication aléatoire dans la fenêtre configurée.
    Retourne None si aucune SiteConfig trouvée (publication immédiate).
    """
    if not site_config:
        return None

    now = datetime.now(timezone.utc)
    min_h = site_config.publish_min_hour if site_config.publish_min_hour is not None else 8
    max_h = site_config.publish_max_hour if site_config.publish_max_hour is not None else 22
    valid_days = site_config.publish_days if site_config.publish_days is not None else [0, 1, 2, 3, 4]

    # Chercher le prochain créneau valide (aujourd'hui ou les 7 prochains jours)
    for days_ahead in range(8):
        candidate = now + timedelta(days=days_ahead)
        if candidate.weekday() in valid_days:
            if max_h <= min_h:
                rand_hour = min_h
            else:
                rand_hour = random.randint(min_h, max_h - 1)
            rand_min = random.randint(0, 59)
            eta = candidate.replace(hour=rand_hour, minute=rand_min, second=0, microsecond=0)
            if eta > now:
                return eta

    return None  # fallback : publication immédiate


@celery_app.task(bind=True, max_retries=3, default_retry_delay=60, name="app.tasks.execute_action")
def execute_action(self, action_id: int):
    """
    Pipeline complet pour une action validée :
    1. Génération du contenu via Claude API
    2. Publication sur WordPress (status=draft)
    3. Mise à jour BDD (Page créée/mise à jour, Action = DONE)
    """
    from app.database import SessionLocal
    from app.models.action import Action, ActionStatus, ActionType
    from app.models.page import Page, PageType, PageStatus
    from app.models.site import Site, SiteConfig
    from app.services.claude_content import ClaudeContentService
    from app.services.wp_publisher import WPPublisher

    db = SessionLocal()
    try:
        action = db.query(Action).filter(Action.id == action_id).first()
        # Accepter VALIDATED (cas normal) mais aussi EXECUTING/FAILED : lors d'un
        # retry Celery, l'action n'est plus VALIDATED — l'ancien garde-fou
        # court-circuitait tous les retries (échec définitif au 1er incident).
        if not action or action.status in (ActionStatus.DONE, ActionStatus.REJECTED, ActionStatus.PENDING):
            return {"skipped": True, "reason": "not found or not executable"}

        action.status = ActionStatus.EXECUTING
        db.commit()

        site = db.query(Site).filter(Site.id == action.site_id).first()
        if not site:
            action.status = ActionStatus.FAILED
            db.commit()
            return {"skipped": True, "reason": "site not found"}

        # Récupérer le proxy éventuel du site
        site_config = db.query(SiteConfig).filter(SiteConfig.site_id == site.id).first()
        proxy_url = site_config.proxy_url if site_config else None

        claude = ClaudeContentService()
        publisher = WPPublisher(site, proxy_url=proxy_url)

        # ── OPTIMIZE : retrouver la page WordPress à mettre à jour ─────────
        # Via page_id si le lien BDD existe, sinon par slug depuis l'URL
        # contenue dans la description (même logique que le mode sync).
        wp_post_id = None
        wp_type = "post"
        if action.action_type == ActionType.OPTIMIZE:
            if action.page_id and action.page and action.page.wp_post_id:
                wp_post_id = action.page.wp_post_id
            elif action.description and "Page: " in action.description:
                import re as _re
                m = _re.search(r"Page:\s*(https?://[^\s]+)", action.description)
                if m:
                    slug = m.group(1).rstrip("/").split("/")[-1]
                    if slug:
                        try:
                            found = publisher.find_post_by_slug(slug)
                        except Exception:
                            found = None
                        if found:
                            wp_post_id = found["wp_post_id"]
                            wp_type = found.get("type", "post")

        # ── Génération du contenu ──────────────────────────────────────────
        if action.action_type == ActionType.GEOLOC:
            extra = action.extra_data or {}
            result = claude.generate_geoloc_article(
                site,
                keyword=action.keyword,
                city=extra.get("city", ""),
                department=extra.get("department", ""),
                postal_code=extra.get("postal_code", ""),
                region=extra.get("region", ""),
            )

        elif action.action_type == ActionType.OPTIMIZE and wp_post_id:
            current_content = ""
            if action.page_id and action.page:
                current_content = action.page.content or ""
            if not current_content:
                try:
                    current_content = publisher.get_content(wp_post_id, wp_type)
                except Exception:
                    current_content = ""
            if current_content and len(current_content) >= 50:
                result = claude.optimize_page(
                    site,
                    keyword=action.keyword,
                    current_content=current_content,
                    current_position=action.current_position or 0,
                )
            else:
                result = claude.generate_article(site, action.keyword)

        else:  # CREATE (ou OPTIMIZE sans page retrouvée → nouveau brouillon)
            result = claude.generate_article(site, action.keyword)

        # ── Parser le JSON retourné par Claude ─────────────────────────────
        raw = result["raw"]
        content_data = _parse_claude_json(raw)

        # ── Coût réel (tokens → €) ─────────────────────────────────────────
        usage = result.get("usage")
        if usage and hasattr(usage, "input_tokens"):
            # Claude Sonnet : ~$3/MTok input, ~$15/MTok output
            input_cost = (usage.input_tokens / 1_000_000) * 3.0
            output_cost = (usage.output_tokens / 1_000_000) * 15.0
            action.actual_api_cost = round(input_cost + output_cost, 6)

        # ── Stocker le contenu généré ──────────────────────────────────────
        action.generated_content = content_data.get("content", raw)
        action.generated_meta_title = content_data.get("meta_title", "")
        action.generated_meta_description = content_data.get("meta_description", "")

        # ── Publication WordPress ──────────────────────────────────────────
        if action.action_type == ActionType.OPTIMIZE and wp_post_id:
            if wp_type == "page":
                wp_result = publisher.update_page(
                    page_id=wp_post_id,
                    content=action.generated_content,
                    meta_title=action.generated_meta_title,
                    meta_description=action.generated_meta_description,
                )
            else:
                wp_result = publisher.update_post(
                    post_id=wp_post_id,
                    title=content_data.get("title", action.title),
                    content=action.generated_content,
                    meta_title=action.generated_meta_title,
                    meta_description=action.generated_meta_description,
                )
            if action.page_id and action.page:
                page_obj = action.page
                page_obj.content = action.generated_content
                page_obj.meta_title = action.generated_meta_title
                page_obj.meta_description = action.generated_meta_description
                page_obj.status = PageStatus.UPDATED

        else:
            wp_result = publisher.create_post(
                title=content_data.get("title", action.title),
                content=action.generated_content,
                slug=content_data.get("slug", ""),
                status="draft",
                meta_title=action.generated_meta_title,
                meta_description=action.generated_meta_description,
            )
            extra = action.extra_data or {}
            page_type = PageType.GEOLOC if action.action_type == ActionType.GEOLOC else PageType.CONTENT
            page_obj = Page(
                site_id=site.id,
                wp_post_id=wp_result["wp_post_id"],
                title=content_data.get("title", action.title),
                slug=content_data.get("slug", ""),
                url=wp_result.get("url", ""),
                content=action.generated_content,
                meta_title=action.generated_meta_title,
                meta_description=action.generated_meta_description,
                page_type=page_type,
                status=PageStatus.DRAFT,
                city=extra.get("city"),
                department=extra.get("department"),
                postal_code=extra.get("postal_code"),
                region=extra.get("region"),
            )
            db.add(page_obj)

        action.status = ActionStatus.DONE
        action.executed_at = datetime.now(timezone.utc)
        db.commit()

        return {"action_id": action_id, "wp_post_id": wp_result.get("wp_post_id")}

    except Exception as exc:
        # Ne marquer FAILED qu'une fois les retries épuisés : l'ancien code
        # passait FAILED avant self.retry(), et le garde-fou d'entrée
        # court-circuitait alors toutes les tentatives suivantes.
        exhausted = self.request.retries >= self.max_retries
        try:
            db.query(Action).filter(Action.id == action_id).update(
                {"status": ActionStatus.FAILED if exhausted else ActionStatus.EXECUTING}
            )
            db.commit()
        except Exception:
            pass
        raise self.retry(exc=exc)
    finally:
        db.close()
