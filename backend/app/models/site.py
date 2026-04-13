from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, JSON, Boolean
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base
from app.crypto import EncryptedString


class Site(Base):
    __tablename__ = "sites"

    id = Column(Integer, primary_key=True)
    name = Column(String(255), nullable=False)
    domain = Column(String(255), unique=True, nullable=False)
    niche = Column(String(255))

    # Hébergement (anti-footprint: tout doit être différent)
    host_provider = Column(String(100))  # hetzner, ovh, vultr, do, contabo
    host_ip = Column(String(45))
    vps_location = Column(String(50))  # DE, FR, US, NL...

    # WordPress
    wp_admin_url = Column(String(500))
    wp_username = Column(String(100))
    wp_app_password = Column(EncryptedString)
    wp_theme = Column(String(100))
    wp_seo_plugin = Column(String(50))  # yoast, rankmath, seopress

    # Search Console (1 compte par site)
    sc_email = Column(String(255))
    sc_token_json = Column(EncryptedString)
    sc_property = Column(String(500))  # ex: "sc-domain:lepaviste-pro.fr" ou "https://www.lepaviste-pro.fr/"

    # Profil éditorial (anti-footprint contenu)
    editorial_tone = Column(String(50))  # journalistique, technique, conversationnel
    editorial_style = Column(Text)  # instructions Claude pour ce site
    editorial_language = Column(String(10), default="fr")
    avg_article_length = Column(Integer, default=1500)

    # FTP (pour le générateur WP)
    ftp_host = Column(String(255))
    ftp_user = Column(String(100))
    ftp_password = Column(EncryptedString)
    ftp_path = Column(String(255), default="/")

    # BDD MySQL du site (pour le générateur WP)
    db_host = Column(String(255))
    db_name = Column(String(100))
    db_user = Column(String(100))
    db_password = Column(EncryptedString)

    # Stats (mises à jour par le scan)
    indexed_pages = Column(Integer, default=0)
    last_scan_at = Column(DateTime(timezone=True))

    # État
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relations
    pages = relationship("Page", back_populates="site", cascade="all, delete-orphan")
    keywords = relationship("Keyword", back_populates="site", cascade="all, delete-orphan")
    actions = relationship("Action", back_populates="site", cascade="all, delete-orphan")


class SiteConfig(Base):
    """Config proxy / publication par site pour anti-footprint"""
    __tablename__ = "site_configs"

    id = Column(Integer, primary_key=True)
    site_id = Column(Integer, ForeignKey("sites.id"), unique=True)
    proxy_url = Column(String(500))
    publish_min_hour = Column(Integer, default=8)   # heure min publication
    publish_max_hour = Column(Integer, default=22)   # heure max publication
    publish_days = Column(JSON, default=[0, 1, 2, 3, 4])  # lundi-vendredi
    scan_frequency_hours = Column(Integer, default=24)
    last_scan_at = Column(DateTime(timezone=True))
