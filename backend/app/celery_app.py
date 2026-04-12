from celery import Celery
from app.config import get_settings

settings = get_settings()

celery_app = Celery(
    "pbn_manager",
    broker=settings.redis_url,
    backend=settings.redis_url,
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="Europe/Paris",
    enable_utc=True,
    task_track_started=True,
    worker_prefetch_multiplier=1,  # 1 tâche à la fois par worker (génération de contenu = lent)
)

# Autodiscover les tâches dans app/tasks.py
celery_app.autodiscover_tasks(["app"])
