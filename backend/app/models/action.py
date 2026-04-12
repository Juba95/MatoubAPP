from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, Enum, Float
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base
import enum


class ActionType(str, enum.Enum):
    OPTIMIZE = "optimize"       # optimiser une page existante
    CREATE = "create"           # créer un nouveau contenu
    GEOLOC = "geoloc"           # créer des pages géolocalisées


class ActionStatus(str, enum.Enum):
    PENDING = "pending"         # en attente de validation
    VALIDATED = "validated"     # validé, en cours d'exécution
    EXECUTING = "executing"     # en cours
    DONE = "done"               # terminé
    REJECTED = "rejected"       # rejeté par l'admin
    FAILED = "failed"


class Action(Base):
    __tablename__ = "actions"

    id = Column(Integer, primary_key=True)
    site_id = Column(Integer, ForeignKey("sites.id"), nullable=False)
    page_id = Column(Integer, ForeignKey("pages.id"), nullable=True)

    action_type = Column(Enum(ActionType), nullable=False)
    status = Column(Enum(ActionStatus), default=ActionStatus.PENDING)

    # Description
    title = Column(String(500), nullable=False)
    description = Column(Text)
    keyword = Column(String(500))

    # Métriques pour le scoring
    search_volume = Column(Integer, default=0)
    current_position = Column(Float)
    previous_position = Column(Float)
    impressions = Column(Integer, default=0)
    position_delta = Column(Float, default=0)  # négatif = baisse

    # Score d'impact (volume × |delta| × facteur)
    impact_score = Column(Float, default=0)

    # Contenu généré (après validation)
    generated_content = Column(Text)
    generated_meta_title = Column(String(500))
    generated_meta_description = Column(String(500))

    # Coût estimé
    estimated_api_cost = Column(Float, default=0)

    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    validated_at = Column(DateTime(timezone=True))
    executed_at = Column(DateTime(timezone=True))

    # Relations
    site = relationship("Site", back_populates="actions")
