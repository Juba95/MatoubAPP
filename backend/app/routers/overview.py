from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func as sqlfunc
from app.database import get_db
from app.auth import get_current_user
from app.models.site import Site
from app.models.page import Page, PageStatus
from app.models.action import Action, ActionStatus

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
        Action.action_type == "optimize",
    ).scalar()
    create_count = db.query(sqlfunc.count(Action.id)).filter(
        Action.status == ActionStatus.PENDING,
        Action.action_type.in_(["create", "geoloc"]),
    ).scalar()

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
    }
