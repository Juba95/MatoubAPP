from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func as sqlfunc
from app.database import get_db
from app.auth import get_current_user
from app.models.site import Site
from app.models.page import Page, PageStatus
from app.models.action import Action, ActionStatus, ActionType
from app.models.keyword import Keyword

router = APIRouter(prefix="/overview", tags=["overview"], dependencies=[Depends(get_current_user)])


@router.get("/")
def get_overview(db: Session = Depends(get_db)):
    """Métriques globales pour le dashboard overview"""
    total_sites = db.query(sqlfunc.count(Site.id)).filter(Site.is_active == True).scalar()
    total_pages = db.query(sqlfunc.count(Page.id)).filter(
        Page.status == PageStatus.PUBLISHED
    ).scalar()
    avg_position = db.query(sqlfunc.avg(Page.avg_position)).filter(
        Page.avg_position.isnot(None)
    ).scalar()
    prev_avg_position = db.query(sqlfunc.avg(Page.prev_position)).filter(
        Page.prev_position.isnot(None)
    ).scalar()
    pending_actions = db.query(sqlfunc.count(Action.id)).filter(
        Action.status == ActionStatus.PENDING
    ).scalar()
    optimize_count = db.query(sqlfunc.count(Action.id)).filter(
        Action.status == ActionStatus.PENDING,
        Action.action_type == ActionType.OPTIMIZE,
    ).scalar()
    create_count = db.query(sqlfunc.count(Action.id)).filter(
        Action.status == ActionStatus.PENDING,
        Action.action_type.in_([ActionType.CREATE, ActionType.GEOLOC]),
    ).scalar()

    # Keyword position distribution
    kw_base = db.query(Keyword).filter(Keyword.current_position.isnot(None))
    total_keywords = kw_base.count()
    kw_top_3 = kw_base.filter(Keyword.current_position <= 3).count()
    kw_top_10 = kw_base.filter(Keyword.current_position <= 10).count()
    kw_top_20 = kw_base.filter(Keyword.current_position <= 20).count()
    kw_beyond = total_keywords - kw_top_20

    # Top 5 opportunities across all sites
    opportunities = (
        db.query(Keyword, Site.domain)
        .join(Site, Keyword.site_id == Site.id)
        .filter(
            Keyword.impressions > 20,
            Keyword.current_position > 10,
        )
        .order_by(Keyword.impressions.desc())
        .limit(5)
        .all()
    )
    top_opportunities = [
        {
            "keyword": kw.keyword,
            "impressions": kw.impressions or 0,
            "position": kw.current_position,
            "site_domain": domain,
        }
        for kw, domain in opportunities
    ]

    return {
        "total_sites": total_sites or 0,
        "total_pages": total_pages or 0,
        "avg_position": round(avg_position, 1) if avg_position else None,
        "prev_avg_position": round(prev_avg_position, 1) if prev_avg_position else None,
        "position_delta": round((prev_avg_position or 0) - (avg_position or 0), 1)
            if avg_position and prev_avg_position else 0,
        "pending_actions": pending_actions or 0,
        "pending_optimizations": optimize_count or 0,
        "pending_creations": create_count or 0,
        "total_keywords": total_keywords,
        "position_distribution": {
            "top_3": kw_top_3,
            "top_10": kw_top_10,
            "top_20": kw_top_20,
            "beyond": kw_beyond,
        },
        "top_opportunities": top_opportunities,
    }


@router.get("/budget")
def get_budget(db: Session = Depends(get_db)):
    """Suivi des coûts API en temps réel (DataForSEO + Claude)"""
    # Coût réel (actions terminées)
    actual_total = db.query(sqlfunc.sum(Action.actual_api_cost)).filter(
        Action.status == ActionStatus.DONE,
        Action.actual_api_cost.isnot(None),
    ).scalar() or 0

    # Coût estimé restant (actions en attente / en cours)
    estimated_pending = db.query(sqlfunc.sum(Action.estimated_api_cost)).filter(
        Action.status.in_([ActionStatus.PENDING, ActionStatus.VALIDATED, ActionStatus.EXECUTING]),
    ).scalar() or 0

    # Détail par type
    geoloc_spent = db.query(sqlfunc.sum(Action.actual_api_cost)).filter(
        Action.status == ActionStatus.DONE,
        Action.action_type == ActionType.GEOLOC,
        Action.actual_api_cost.isnot(None),
    ).scalar() or 0

    content_spent = db.query(sqlfunc.sum(Action.actual_api_cost)).filter(
        Action.status == ActionStatus.DONE,
        Action.action_type == ActionType.CREATE,
        Action.actual_api_cost.isnot(None),
    ).scalar() or 0

    optimize_spent = db.query(sqlfunc.sum(Action.actual_api_cost)).filter(
        Action.status == ActionStatus.DONE,
        Action.action_type == ActionType.OPTIMIZE,
        Action.actual_api_cost.isnot(None),
    ).scalar() or 0

    actions_done = db.query(sqlfunc.count(Action.id)).filter(
        Action.status == ActionStatus.DONE
    ).scalar() or 0

    return {
        "actual_spent_eur": round(actual_total, 4),
        "estimated_pending_eur": round(estimated_pending, 2),
        "projected_total_eur": round(actual_total + estimated_pending, 2),
        "breakdown": {
            "geoloc_eur": round(geoloc_spent, 4),
            "content_eur": round(content_spent, 4),
            "optimize_eur": round(optimize_spent, 4),
        },
        "actions_done": actions_done,
        "avg_cost_per_action_eur": round(actual_total / actions_done, 5) if actions_done else 0,
    }
