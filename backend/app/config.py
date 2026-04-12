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

    redis_url: str = "redis://localhost:6379/0"
    proxy_url: str = ""

    class Config:
        env_file = ".env"


@lru_cache
def get_settings() -> Settings:
    return Settings()
