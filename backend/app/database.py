from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from app.config import get_settings


engine = create_engine(get_settings().database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    from app.models import site, page, action, keyword  # noqa
    Base.metadata.create_all(bind=engine)
    _migrate(engine)


def _migrate(eng):
    """Ajoute les colonnes manquantes sur les tables existantes."""
    from sqlalchemy import text, inspect
    insp = inspect(eng)

    migrations = [
        ("actions", "actual_api_cost", "FLOAT DEFAULT 0"),
        ("actions", "extra_data", "JSON"),
        ("sites", "sc_property", "VARCHAR(500)"),
        ("sites", "indexed_pages", "INTEGER DEFAULT 0"),
        ("sites", "last_scan_at", "TIMESTAMP WITH TIME ZONE"),
    ]

    with eng.connect() as conn:
        for table, column, col_type in migrations:
            if table not in insp.get_table_names():
                continue
            existing = [c["name"] for c in insp.get_columns(table)]
            if column not in existing:
                conn.execute(text(f'ALTER TABLE {table} ADD COLUMN {column} {col_type}'))
                conn.commit()
