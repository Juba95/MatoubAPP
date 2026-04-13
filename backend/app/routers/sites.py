from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func as sqlfunc
from pydantic import BaseModel
from typing import Optional
from app.database import get_db
from app.auth import get_current_user
from app.models.site import Site
from app.models.page import Page, PageStatus
from app.models.action import Action, ActionStatus
from app.models.keyword import Keyword

router = APIRouter(prefix="/sites", tags=["sites"], dependencies=[Depends(get_current_user)])


class SiteCreate(BaseModel):
    name: str
    domain: str
    niche: Optional[str] = None
    host_provider: Optional[str] = None
    host_ip: Optional[str] = None
    vps_location: Optional[str] = None
    wp_admin_url: Optional[str] = None
    wp_username: Optional[str] = None
    wp_app_password: Optional[str] = None
    wp_theme: Optional[str] = None
    wp_seo_plugin: Optional[str] = None
    sc_email: Optional[str] = None
    editorial_tone: Optional[str] = "conversationnel"
    editorial_style: Optional[str] = None
    avg_article_length: Optional[int] = 1500
    ftp_host: Optional[str] = None
    ftp_user: Optional[str] = None
    ftp_password: Optional[str] = None
    db_host: Optional[str] = None
    db_name: Optional[str] = None
    db_user: Optional[str] = None
    db_password: Optional[str] = None


class SiteUpdate(SiteCreate):
    name: Optional[str] = None
    domain: Optional[str] = None


@router.get("/")
def list_sites(db: Session = Depends(get_db)):
    sites = db.query(Site).filter(Site.is_active == True).all()
    result = []
    for s in sites:
        total_pages = db.query(sqlfunc.count(Page.id)).filter(
            Page.site_id == s.id, Page.status == PageStatus.PUBLISHED
        ).scalar()
        pending_actions = db.query(sqlfunc.count(Action.id)).filter(
            Action.site_id == s.id, Action.status == ActionStatus.PENDING
        ).scalar()
        # Position moyenne depuis les Keywords (pas les Pages)
        avg_pos = db.query(sqlfunc.avg(Keyword.current_position)).filter(
            Keyword.site_id == s.id, Keyword.current_position.isnot(None)
        ).scalar()
        prev_avg_pos = db.query(sqlfunc.avg(Keyword.previous_position)).filter(
            Keyword.site_id == s.id, Keyword.previous_position.isnot(None)
        ).scalar()

        total_keywords = db.query(sqlfunc.count(Keyword.id)).filter(
            Keyword.site_id == s.id
        ).scalar()

        # Distribution par site
        kw_base = db.query(Keyword).filter(Keyword.site_id == s.id, Keyword.current_position.isnot(None))
        kw_top3 = kw_base.filter(Keyword.current_position <= 3).count()
        kw_4_10 = kw_base.filter(Keyword.current_position > 3, Keyword.current_position <= 10).count()
        kw_11_20 = kw_base.filter(Keyword.current_position > 10, Keyword.current_position <= 20).count()

        # Top 3 opportunités par site
        site_opps = (
            db.query(Keyword)
            .filter(Keyword.site_id == s.id, Keyword.impressions > 10, Keyword.current_position > 10)
            .order_by(Keyword.impressions.desc())
            .limit(3)
            .all()
        )

        # Top 3 drops par site
        site_drops = (
            db.query(Keyword)
            .filter(
                Keyword.site_id == s.id,
                Keyword.current_position.isnot(None),
                Keyword.previous_position.isnot(None),
            )
            .all()
        )
        drops = sorted(
            [k for k in site_drops if k.current_position - (k.previous_position or k.current_position) >= 3],
            key=lambda k: k.current_position - k.previous_position,
            reverse=True,
        )[:3]

        result.append({
            "id": s.id,
            "name": s.name,
            "domain": s.domain,
            "niche": s.niche,
            "host_provider": s.host_provider,
            "vps_location": s.vps_location,
            "wp_theme": s.wp_theme,
            "editorial_tone": s.editorial_tone,
            "total_pages": total_pages or 0,
            "total_keywords": total_keywords or 0,
            "avg_position": round(avg_pos, 1) if avg_pos else None,
            "prev_avg_position": round(prev_avg_pos, 1) if prev_avg_pos else None,
            "position_delta": round((prev_avg_pos or 0) - (avg_pos or 0), 1) if avg_pos and prev_avg_pos else 0,
            "pending_actions": pending_actions or 0,
            "indexed_pages": s.indexed_pages or 0,
            "sc_connected": bool(s.sc_token_json),
            "sc_property": s.sc_property,
            "distribution": {"top_3": kw_top3, "top_4_10": kw_4_10, "top_11_20": kw_11_20},
            "top_opportunities": [
                {"keyword": k.keyword, "position": k.current_position, "impressions": k.impressions}
                for k in site_opps
            ],
            "top_drops": [
                {"keyword": k.keyword, "from": k.previous_position, "to": k.current_position,
                 "delta": round(k.current_position - k.previous_position)}
                for k in drops
            ],
            "is_active": s.is_active,
        })
    return result


@router.post("/")
def create_site(data: SiteCreate, db: Session = Depends(get_db)):
    existing = db.query(Site).filter(Site.domain == data.domain).first()
    if existing:
        raise HTTPException(status_code=400, detail="Domain already exists")
    site = Site(**data.model_dump(exclude_none=True))
    db.add(site)
    db.commit()
    db.refresh(site)
    return {"id": site.id, "domain": site.domain, "message": "Site created"}


@router.get("/{site_id}")
def get_site(site_id: int, db: Session = Depends(get_db)):
    site = db.query(Site).filter(Site.id == site_id).first()
    if not site:
        raise HTTPException(status_code=404, detail="Site not found")
    return {
        "id": site.id,
        "name": site.name,
        "domain": site.domain,
        "niche": site.niche,
        "host_provider": site.host_provider,
        "host_ip": site.host_ip,
        "vps_location": site.vps_location,
        "wp_admin_url": site.wp_admin_url,
        "wp_username": site.wp_username,
        "wp_theme": site.wp_theme,
        "wp_seo_plugin": site.wp_seo_plugin,
        "sc_email": site.sc_email,
        "sc_connected": bool(site.sc_token_json),
        "sc_property": site.sc_property,
        "editorial_tone": site.editorial_tone,
        "editorial_style": site.editorial_style,
        "avg_article_length": site.avg_article_length,
        "is_active": site.is_active,
    }


@router.put("/{site_id}")
def update_site(site_id: int, data: SiteUpdate, db: Session = Depends(get_db)):
    site = db.query(Site).filter(Site.id == site_id).first()
    if not site:
        raise HTTPException(status_code=404, detail="Site not found")
    for k, v in data.model_dump(exclude_none=True).items():
        setattr(site, k, v)
    db.commit()
    return {"message": "Updated"}


@router.delete("/{site_id}")
def delete_site(site_id: int, db: Session = Depends(get_db)):
    site = db.query(Site).filter(Site.id == site_id).first()
    if not site:
        raise HTTPException(status_code=404, detail="Site not found")
    db.delete(site)
    db.commit()
    return {"message": "Deleted"}
