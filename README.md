# PBN Manager

Dashboard privé de gestion de PBN (Private Blog Network) avec agent SEO autonome.

## Architecture

```
pbn-manager/
├── backend/                  # Python FastAPI
│   ├── app/
│   │   ├── main.py           # Point d'entrée FastAPI
│   │   ├── config.py         # Settings + env vars
│   │   ├── database.py       # PostgreSQL connection
│   │   ├── auth.py           # JWT auth
│   │   ├── models/           # SQLAlchemy models
│   │   │   ├── site.py
│   │   │   ├── page.py
│   │   │   ├── action.py
│   │   │   └── keyword.py
│   │   ├── routers/          # API endpoints
│   │   │   ├── auth.py
│   │   │   ├── sites.py
│   │   │   ├── overview.py
│   │   │   ├── actions.py
│   │   │   ├── generator.py
│   │   │   └── geoloc.py
│   │   └── services/         # Business logic
│   │       ├── dataforseo.py
│   │       ├── search_console.py
│   │       ├── claude_content.py
│   │       ├── wp_publisher.py
│   │       ├── seo_agent.py
│   │       └── wp_installer.py
│   ├── requirements.txt
│   └── .env.example
├── frontend/                 # Next.js dashboard
│   └── (phase 5)
└── villes_france.csv         # Ta base de villes
```

## Quick Start

```bash
cd backend
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # Configurer tes API keys
uvicorn app.main:app --reload
```

## Stack
- **Backend**: Python 3.11+ / FastAPI / SQLAlchemy / Celery + Redis
- **BDD**: PostgreSQL
- **APIs**: DataForSEO, Google Search Console, Claude API
- **Frontend**: Next.js (Phase 5)
- **Hébergement**: VPS dédié (séparé des sites PBN)
