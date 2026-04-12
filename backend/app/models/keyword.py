from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Float
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base


class Keyword(Base):
    __tablename__ = "keywords"

    id = Column(Integer, primary_key=True)
    site_id = Column(Integer, ForeignKey("sites.id"), nullable=False)
    page_id = Column(Integer, ForeignKey("pages.id"), nullable=True)

    keyword = Column(String(500), nullable=False)
    search_volume = Column(Integer, default=0)
    difficulty = Column(Float, default=0)

    # Position actuelle
    current_position = Column(Float)
    previous_position = Column(Float)
    best_position = Column(Float)

    # Search Console data
    impressions = Column(Integer, default=0)
    clicks = Column(Integer, default=0)
    ctr = Column(Float, default=0)

    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relations
    site = relationship("Site", back_populates="keywords")
    history = relationship("KeywordHistory", back_populates="keyword", cascade="all, delete-orphan")


class KeywordHistory(Base):
    __tablename__ = "keyword_history"

    id = Column(Integer, primary_key=True)
    keyword_id = Column(Integer, ForeignKey("keywords.id"), nullable=False)
    position = Column(Float)
    impressions = Column(Integer, default=0)
    clicks = Column(Integer, default=0)
    recorded_at = Column(DateTime(timezone=True), server_default=func.now())

    keyword = relationship("Keyword", back_populates="history")
