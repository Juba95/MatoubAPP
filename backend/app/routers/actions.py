from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import desc
from datetime import datetime, timezone
from typing import Optional
from app.database import get_db
from app.auth import get_current_user
from app.models.action import Action, ActionStatus, ActionType

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
        q = q.filter(Action.status == status)
    else:
        q = q.filter(Action.status == ActionStatus.PENDING)
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
            "created_at": a.created_at,
        }
        for a in actions
    ]


@router.post("/{action_id}/validate")
def validate_action(action_id: int, db: Session = Depends(get_db)):
    """Valider une action — déclenche la génération de contenu"""
    action = db.query(Action).filter(Action.id == action_id).first()
    if not action:
        raise HTTPException(status_code=404, detail="Action not found")
    if action.status != ActionStatus.PENDING:
        raise HTTPException(status_code=400, detail=f"Action is {action.status}, not pending")
    action.status = ActionStatus.VALIDATED
    action.validated_at = datetime.now(timezone.utc)
    db.commit()
    # TODO: déclencher la tâche Celery de génération
    return {"message": "Action validated", "id": action.id}


@router.post("/validate-batch")
def validate_batch(action_ids: list[int], db: Session = Depends(get_db)):
    """Valider plusieurs actions d'un coup"""
    now = datetime.now(timezone.utc)
    count = 0
    for aid in action_ids:
        action = db.query(Action).filter(
            Action.id == aid, Action.status == ActionStatus.PENDING
        ).first()
        if action:
            action.status = ActionStatus.VALIDATED
            action.validated_at = now
            count += 1
    db.commit()
    return {"message": f"{count} actions validated"}


@router.post("/{action_id}/reject")
def reject_action(action_id: int, db: Session = Depends(get_db)):
    action = db.query(Action).filter(Action.id == action_id).first()
    if not action:
        raise HTTPException(status_code=404, detail="Action not found")
    action.status = ActionStatus.REJECTED
    db.commit()
    return {"message": "Action rejected"}


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
