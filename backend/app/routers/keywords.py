from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import func as sqlfunc
from app.database import get_db
from app.auth import get_current_user
from app.models.keyword import Keyword

router = APIRouter(prefix="/keywords", tags=["keywords"], dependencies=[Depends(get_current_user)])

SORT_COLUMNS = {
    "position": Keyword.current_position,
    "volume": Keyword.search_volume,
    "impressions": Keyword.impressions,
}


def _serialize_keyword(kw: Keyword) -> dict:
    current = kw.current_position
    previous = kw.previous_position
    delta = round(previous - current, 1) if current is not None and previous is not None else None
    return {
        "id": kw.id,
        "keyword": kw.keyword,
        "position": current,
        "previous_position": previous,
        "delta": delta,
        "impressions": kw.impressions or 0,
        "clicks": kw.clicks or 0,
        "ctr": kw.ctr or 0,
        "search_volume": kw.search_volume or 0,
        "difficulty": kw.difficulty or 0,
    }


@router.get("/{site_id}")
def list_keywords(
    site_id: int,
    sort_by: str = Query("position", regex="^(position|volume|impressions)$"),
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
):
    """Liste tous les mots-cles suivis pour un site."""
    col = SORT_COLUMNS[sort_by]
    keywords = (
        db.query(Keyword)
        .filter(Keyword.site_id == site_id)
        .order_by(col.asc() if sort_by == "position" else col.desc())
        .limit(limit)
        .all()
    )
    return [_serialize_keyword(kw) for kw in keywords]


@router.get("/{site_id}/opportunities")
def get_opportunities(
    site_id: int,
    db: Session = Depends(get_db),
):
    """Mots-cles avec beaucoup d'impressions mais mal positionnés (>10)."""
    keywords = (
        db.query(Keyword)
        .filter(
            Keyword.site_id == site_id,
            Keyword.impressions > 20,
            Keyword.current_position > 10,
        )
        .order_by(Keyword.impressions.desc())
        .all()
    )
    results = []
    for kw in keywords:
        data = _serialize_keyword(kw)
        data["opportunity_score"] = round(kw.impressions * (kw.current_position / 10), 1)
        results.append(data)
    return results


@router.get("/{site_id}/drops")
def get_drops(
    site_id: int,
    db: Session = Depends(get_db),
):
    """Mots-cles ayant perdu 3+ positions."""
    keywords = (
        db.query(Keyword)
        .filter(
            Keyword.current_position.isnot(None),
            Keyword.previous_position.isnot(None),
            Keyword.site_id == site_id,
            (Keyword.current_position - Keyword.previous_position) >= 3,
        )
        .order_by((Keyword.current_position - Keyword.previous_position).desc())
        .all()
    )
    return [_serialize_keyword(kw) for kw in keywords]


@router.get("/{site_id}/distribution")
def get_distribution(
    site_id: int,
    db: Session = Depends(get_db),
):
    """Répartition des positions pour un site."""
    base = db.query(Keyword).filter(
        Keyword.site_id == site_id,
        Keyword.current_position.isnot(None),
    )

    top_3 = base.filter(Keyword.current_position <= 3).count()
    top_10 = base.filter(Keyword.current_position <= 10).count()
    top_20 = base.filter(Keyword.current_position <= 20).count()
    top_50 = base.filter(Keyword.current_position <= 50).count()
    total = base.count()
    beyond_50 = total - top_50

    return {
        "top_3": top_3,
        "top_10": top_10,
        "top_20": top_20,
        "top_50": top_50,
        "beyond_50": beyond_50,
        "total": total,
    }
