from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    database_url: str = "postgresql://pbn_user:password@localhost:5432/pbn_manager"
    jwt_secret: str = "change-this"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 1440

    admin_email: str = "admin@pbn.local"
    admin_password: str = "change-this"

    dataforseo_login: str = ""
    dataforseo_password: str = ""
    anthropic_api_key: str = ""
    replicate_api_token: str = ""
    semrush_api_key: str = ""
    # Clé OpenAI (nommée « generationimage ») — utilisée pour générer le logo
    # via gpt-image-1. À définir dans .env / variables d'environnement Coolify.
    openai_api_key: str = ""

    # Google OAuth (Search Console)
    gsc_client_id: str = ""
    gsc_client_secret: str = ""
    gsc_redirect_uri: str = "http://localhost:8000/auth/gsc/callback"

    redis_url: str = "redis://localhost:6379/0"
    proxy_url: str = ""

    # Clé Fernet pour chiffrer les credentials en BDD
    # Générer avec : python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    encryption_key: str = ""

    # CORS — mettre l'URL du dashboard (ex: https://dashboard.monserveur.com)
    allowed_origins: str = "http://localhost:3000,http://localhost:8000"

    class Config:
        env_file = ".env"


@lru_cache
def get_settings() -> Settings:
    return Settings()
