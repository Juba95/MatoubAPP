from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks
from sqlalchemy.orm import Session
from sqlalchemy import desc
from datetime import datetime, timezone
from typing import Optional
from app.database import get_db, SessionLocal
from app.auth import get_current_user
from app.models.action import Action, ActionStatus, ActionType
from app.models.site import SiteConfig

router = APIRouter(prefix="/actions", tags=["actions"], dependencies=[Depends(get_current_user)])


@router.get("/")
def list_actions(
    status: Optional[str] = Query(None),
    site_id: Optional[int] = Query(None),
    limit: int = Query(50, le=100),
    db: Session = Depends(get_db),
):
    """Liste des actions triées par impact_score"""
    q = db.query(Action)
    if status:
        try:
            q = q.filter(Action.status == ActionStatus(status))
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid status: {status}")
    else:
        q = q.filter(Action.status.in_([ActionStatus.PENDING, ActionStatus.VALIDATED, ActionStatus.EXECUTING, ActionStatus.FAILED]))
    if site_id:
        q = q.filter(Action.site_id == site_id)
    actions = q.order_by(desc(Action.impact_score)).limit(limit).all()
    return [
        {
            "id": a.id,
            "site_id": a.site_id,
            "page_id": a.page_id,
            "action_type": a.action_type,
            "status": a.status,
            "title": a.title,
            "description": a.description,
            "keyword": a.keyword,
            "search_volume": a.search_volume,
            "current_position": a.current_position,
            "previous_position": a.previous_position,
            "position_delta": a.position_delta,
            "impressions": a.impressions,
            "impact_score": a.impact_score,
            "estimated_api_cost": a.estimated_api_cost,
            "actual_api_cost": a.actual_api_cost,
            "created_at": a.created_at,
        }
        for a in actions
    ]


# Logs en mémoire par action_id
_action_logs: dict[int, list[str]] = {}


def _log(action_id: int, msg: str):
    if action_id not in _action_logs:
        _action_logs[action_id] = []
    _action_logs[action_id].append(msg)
    print(f"[ACTION {action_id}] {msg}")


def _run_action_sync(action_id: int):
    """Exécute une action en mode synchrone (sans Celery)."""
    from app.tasks import _parse_claude_json
    from app.models.page import Page, PageType, PageStatus
    from app.models.site import Site, SiteConfig as SC
    from app.services.claude_content import ClaudeContentService
    from app.services.wp_publisher import WPPublisher

    _action_logs[action_id] = ["Démarrage..."]
    db = SessionLocal()
    try:
        action = db.query(Action).filter(Action.id == action_id).first()
        if not action or action.status != ActionStatus.VALIDATED:
            _log(action_id, "SKIP: action non trouvée ou pas en statut VALIDATED")
            return

        action.status = ActionStatus.EXECUTING
        db.commit()

        site = db.query(Site).filter(Site.id == action.site_id).first()
        if not site:
            _log(action_id, "ERROR: site non trouvé")
            action.status = ActionStatus.FAILED
            db.commit()
            return

        _log(action_id, f"Site: {site.name} | Keyword: {action.keyword}")

        # Vérification credentials
        if not site.wp_username or not site.wp_app_password:
            _log(action_id, "ERROR: WP username ou app password manquant")
            raise Exception("WP credentials non configurés — remplis Username + App Password dans le panneau du site")

        sc = db.query(SC).filter(SC.site_id == site.id).first()
        proxy_url = sc.proxy_url if sc else None

        # Génération Claude
        claude = ClaudeContentService()
        publisher = WPPublisher(site, proxy_url=proxy_url)
        wp_post_id = None  # sera rempli pour OPTIMIZE

        if action.action_type == ActionType.GEOLOC:
            _log(action_id, "Génération article géolocalisé via Claude...")
            extra = action.extra_data or {}
            result = claude.generate_geoloc_article(site, keyword=action.keyword,
                city=extra.get("city",""), department=extra.get("department",""),
                postal_code=extra.get("postal_code",""), region=extra.get("region",""))

        elif action.action_type == ActionType.OPTIMIZE:
            # Récupérer le contenu actuel depuis WordPress
            current_content = ""
            wp_post_id = None

            if action.page_id and action.page and action.page.wp_post_id:
                wp_post_id = action.page.wp_post_id
            elif action.description and "Page: " in action.description:
                import re
                url_match = re.search(r'Page:\s*(https?://[^\s]+)', action.description)
                if url_match:
                    page_url = url_match.group(1).rstrip("/")
                    slug = page_url.split("/")[-1]
                    if slug:
                        _log(action_id, f"Recherche page WP par slug : {slug}")
                        wp_found = publisher.find_post_by_slug(slug)
                        if wp_found:
                            wp_post_id = wp_found["wp_post_id"]
                            _log(action_id, f"Trouvé : WP #{wp_post_id} ({wp_found['type']})")

            if wp_post_id:
                _log(action_id, f"Récupération du contenu actuel (WP #{wp_post_id})...")
                try:
                    post_data = publisher.get_post(wp_post_id)
                    current_content = post_data.get("content", {}).get("rendered", "")
                    _log(action_id, f"Contenu actuel récupéré ({len(current_content)} chars)")
                except Exception as e:
                    _log(action_id, f"Erreur récupération contenu : {e}")

            _log(action_id, f"Optimisation via Claude (keyword: {action.keyword}, pos: {action.current_position})...")
            result = claude.optimize_page(site, keyword=action.keyword,
                current_content=current_content, current_position=action.current_position or 0)

        else:
            _log(action_id, "Génération nouvel article via Claude...")
            result = claude.generate_article(site, action.keyword)

        raw = result["raw"]
        content_data = _parse_claude_json(raw)
        _log(action_id, f"Contenu généré ({len(raw)} chars)")

        usage = result.get("usage")
        if usage and hasattr(usage, "input_tokens"):
            cost = round((usage.input_tokens/1e6)*3 + (usage.output_tokens/1e6)*15, 6)
            action.actual_api_cost = cost
            _log(action_id, f"Tokens: {usage.input_tokens} in / {usage.output_tokens} out — coût: {cost}€")

        action.generated_content = content_data.get("content", raw)
        action.generated_meta_title = content_data.get("meta_title", "")
        action.generated_meta_description = content_data.get("meta_description", "")

        changes = content_data.get("changes_summary", "")
        if changes:
            _log(action_id, f"Modifications : {changes}")

        # Publication WP
        _log(action_id, f"Publication sur WordPress ({site.domain})...")

        if action.action_type == ActionType.OPTIMIZE and wp_post_id:
            # Mettre à jour la page existante
            try:
                wp_result = publisher.update_post(post_id=wp_post_id,
                    content=action.generated_content,
                    meta_title=action.generated_meta_title, meta_description=action.generated_meta_description)
                _log(action_id, f"SUCCESS: Page WP #{wp_post_id} mise à jour")
            except Exception:
                # Peut-être une page WP (pas un post) — essayer update_page
                wp_result = publisher.update_page(page_id=wp_post_id,
                    content=action.generated_content,
                    meta_title=action.generated_meta_title, meta_description=action.generated_meta_description)
                _log(action_id, f"SUCCESS: Page WP #{wp_post_id} mise à jour (type page)")
            if changes:
                _log(action_id, f"Résumé : {changes}")
        elif action.action_type == ActionType.OPTIMIZE:
            _log(action_id, "WARN: Page non trouvée sur WP — création d'un brouillon")
            wp_result = publisher.create_post(title=content_data.get("title", action.title),
                content=action.generated_content, slug=content_data.get("slug",""), status="draft",
                meta_title=action.generated_meta_title, meta_description=action.generated_meta_description)
            _log(action_id, f"Brouillon créé (WP #{wp_result['wp_post_id']})")
        else:
            # CREATE / GEOLOC → nouveau brouillon
            wp_result = publisher.create_post(title=content_data.get("title", action.title),
                content=action.generated_content, slug=content_data.get("slug",""), status="draft",
                meta_title=action.generated_meta_title, meta_description=action.generated_meta_description)
            extra = action.extra_data or {}
            page_type = PageType.GEOLOC if action.action_type == ActionType.GEOLOC else PageType.CONTENT
            page = Page(site_id=site.id, wp_post_id=wp_result["wp_post_id"],
                title=content_data.get("title", action.title), slug=content_data.get("slug",""),
                url=wp_result.get("url",""), content=action.generated_content,
                meta_title=action.generated_meta_title, meta_description=action.generated_meta_description,
                page_type=page_type, status=PageStatus.DRAFT,
                city=extra.get("city"), department=extra.get("department"),
                postal_code=extra.get("postal_code"), region=extra.get("region"))
            db.add(page)
            _log(action_id, f"SUCCESS: Brouillon créé (WP #{wp_result['wp_post_id']}) — {wp_result.get('url','')}")

        action.status = ActionStatus.DONE
        action.executed_at = datetime.now(timezone.utc)
        db.commit()
        _log(action_id, "DONE")
    except Exception as exc:
        import traceback
        error_msg = f"{type(exc).__name__}: {exc}"
        _log(action_id, f"ERROR: {error_msg}")
        traceback.print_exc()
        try:
            a = db.query(Action).filter(Action.id == action_id).first()
            if a:
                a.status = ActionStatus.FAILED
                a.description = (a.description or "").split("\n\nERREUR:")[0] + f"\n\nERREUR: {error_msg}"
            db.commit()
        except Exception:
            pass
    finally:
        db.close()


@router.post("/{action_id}/validate")
def validate_action(action_id: int, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """Valider une action — déclenche la génération de contenu"""
    action = db.query(Action).filter(Action.id == action_id).first()
    if not action:
        raise HTTPException(status_code=404, detail="Action not found")
    if action.status not in (ActionStatus.PENDING, ActionStatus.VALIDATED):
        raise HTTPException(status_code=400, detail=f"Action is {action.status}, cannot validate")
    action.status = ActionStatus.VALIDATED
    action.validated_at = datetime.now(timezone.utc)
    db.commit()

    # Tenter Celery, sinon fallback sur BackgroundTasks (synchrone dans le process)
    task_id = None
    try:
        from app.tasks import execute_action, get_publish_eta
        site_config = db.query(SiteConfig).filter(SiteConfig.site_id == action.site_id).first()
        eta = get_publish_eta(site_config)
        task = execute_action.apply_async(args=[action_id], eta=eta)
        task_id = task.id
    except Exception:
        # Celery/Redis down → exécuter directement via FastAPI BackgroundTasks
        background_tasks.add_task(_run_action_sync, action_id)

    return {
        "message": "Action validated — génération en cours",
        "id": action.id,
        "task_id": task_id,
        "mode": "celery" if task_id else "sync",
    }


@router.post("/{action_id}/retry")
def retry_action(action_id: int, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """Réessayer une action échouée"""
    action = db.query(Action).filter(Action.id == action_id).first()
    if not action:
        raise HTTPException(status_code=404, detail="Action not found")
    # Nettoyer l'erreur de la description
    if action.description and "\n\nERREUR:" in action.description:
        action.description = action.description.split("\n\nERREUR:")[0]
    action.status = ActionStatus.VALIDATED
    action.validated_at = datetime.now(timezone.utc)
    db.commit()

    try:
        from app.tasks import execute_action, get_publish_eta
        site_config = db.query(SiteConfig).filter(SiteConfig.site_id == action.site_id).first()
        eta = get_publish_eta(site_config)
        task = execute_action.apply_async(args=[action_id], eta=eta)
    except Exception:
        background_tasks.add_task(_run_action_sync, action_id)

    return {"message": "Action relancée"}


@router.get("/logs/{action_id}")
def get_action_logs(action_id: int):
    """Logs d'exécution d'une action en temps réel"""
    logs = _action_logs.get(action_id, [])
    return {"action_id": action_id, "logs": logs, "count": len(logs)}


@router.post("/validate-batch")
def validate_batch(action_ids: list[int], background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """Valider plusieurs actions d'un coup"""
    now = datetime.now(timezone.utc)
    count = 0
    task_ids = []

    # Charger toutes les SiteConfigs en une passe
    site_ids = {
        a.site_id for a in db.query(Action).filter(Action.id.in_(action_ids)).all()
    }
    site_configs = {
        sc.site_id: sc
        for sc in db.query(SiteConfig).filter(SiteConfig.site_id.in_(site_ids)).all()
    }

    for aid in action_ids:
        action = db.query(Action).filter(
            Action.id == aid, Action.status == ActionStatus.PENDING
        ).first()
        if action:
            action.status = ActionStatus.VALIDATED
            action.validated_at = now
            count += 1

    db.commit()

    try:
        from app.tasks import execute_action, get_publish_eta
        for aid in action_ids:
            action = db.query(Action).filter(Action.id == aid).first()
            if action and action.status == ActionStatus.VALIDATED:
                eta = get_publish_eta(site_configs.get(action.site_id))
                task = execute_action.apply_async(args=[aid], eta=eta)
                task_ids.append(task.id)
    except Exception:
        # Celery down → mode sync
        for aid in action_ids:
            background_tasks.add_task(_run_action_sync, aid)

    return {"message": f"{count} actions validated", "task_ids": task_ids}


@router.post("/{action_id}/reject")
def reject_action(action_id: int, db: Session = Depends(get_db)):
    action = db.query(Action).filter(Action.id == action_id).first()
    if not action:
        raise HTTPException(status_code=404, detail="Action not found")
    action.status = ActionStatus.REJECTED
    db.commit()
    return {"message": "Action rejected"}


@router.post("/scan/{site_id}")
def scan_site(site_id: int, db: Session = Depends(get_db)):
    """Lance un scan SEO sur un site et crée les actions dans la file"""
    from app.models.site import Site
    from app.services.seo_agent import SEOAgent
    site = db.query(Site).filter(Site.id == site_id).first()
    if not site:
        raise HTTPException(status_code=404, detail="Site not found")
    agent = SEOAgent(db)
    result = agent.scan_site(site)
    return result


@router.get("/history")
def action_history(
    limit: int = Query(50, le=200),
    db: Session = Depends(get_db),
):
    """Historique des actions exécutées"""
    actions = (
        db.query(Action)
        .filter(Action.status.in_([ActionStatus.DONE, ActionStatus.FAILED]))
        .order_by(desc(Action.executed_at))
        .limit(limit)
        .all()
    )
    return actions
