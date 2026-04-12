from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, Enum, Float
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base
import enum


class PageStatus(str, enum.Enum):
    DRAFT = "draft"
    PUBLISHED = "published"
    UPDATED = "updated"
    PENDING = "pending"  # en attente de validation
    FAILED = "failed"


class PageType(str, enum.Enum):
    CONTENT = "content"       # article classique
    GEOLOC = "geoloc"         # page géolocalisée
    OPTIMIZED = "optimized"   # page optimisée par l'agent


class Page(Base):
    __tablename__ = "pages"

    id = Column(Integer, primary_key=True)
    site_id = Column(Integer, ForeignKey("sites.id"), nullable=False)
    wp_post_id = Column(Integer)  # ID du post WordPress

    title = Column(String(500), nullable=False)
    slug = Column(String(500))
    url = Column(String(1000))
    content = Column(Text)
    meta_title = Column(String(500))
    meta_description = Column(String(500))

    page_type = Column(Enum(PageType), default=PageType.CONTENT)
    status = Column(Enum(PageStatus), default=PageStatus.DRAFT)

    # Géoloc
    city = Column(String(255))
    department = Column(String(10))
    postal_code = Column(String(20))
    region = Column(String(255))

    # Métriques (Search Console)
    impressions = Column(Integer, default=0)
    clicks = Column(Integer, default=0)
    ctr = Column(Float, default=0)
    avg_position = Column(Float)

    # Métriques précédentes (pour comparaison)
    prev_impressions = Column(Integer, default=0)
    prev_clicks = Column(Integer, default=0)
    prev_position = Column(Float)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    published_at = Column(DateTime(timezone=True))

    # Relations
    site = relationship("Site", back_populates="pages")
