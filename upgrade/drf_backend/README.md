# DRF Upgrade Backend (Phase 1 Scaffold)

This folder introduces a parallel **Django REST + PostgreSQL** backend to migrate the existing Flask + MySQL prototype toward the enterprise stack.

## What is included

- Django REST project scaffold (`config`, `network`)
- Core role-aware data models
- Blood request + donor response API endpoints
- Admin approval endpoints for requests/camps
- Geospatial-ready columns (`latitude`, `longitude`) for users, requests, and camps
- Impact counters endpoint

## Setup

1. Create a virtual environment (recommended)
2. Install dependencies:

```powershell
pip install -r upgrade/drf_backend/requirements.txt
```

3. Configure PostgreSQL env vars (examples):

```powershell
$env:POSTGRES_DB="donation_management"
$env:POSTGRES_USER="postgres"
$env:POSTGRES_PASSWORD="postgres"
$env:POSTGRES_HOST="localhost"
$env:POSTGRES_PORT="5432"
```

4. Run migrations:

```powershell
cd upgrade/drf_backend
python manage.py makemigrations
python manage.py migrate
```

5. Start API server:

```powershell
python manage.py runserver 0.0.0.0:8001
```

## Initial API routes

- `GET /api/v1/health`
- `POST /api/v1/users/register`
- `POST /api/v1/requests`
- `GET /api/v1/requests/pending`
- `POST /api/v1/requests/{id}/approve`
- `POST /api/v1/requests/{id}/respond`
- `GET /api/v1/map/donors`
- `POST /api/v1/camps`
- `POST /api/v1/camps/{id}/approve`
- `GET /api/v1/inventory`
- `GET /api/v1/notifications/{user_id}`
- `GET /api/v1/counters`

## Next migration steps

1. Add JWT authentication and role-based permission classes.
2. Replace simple distance scoring with PostGIS radius queries.
3. Move AI scoring into dedicated Python services (fraud, demand prediction, chatbot).
4. Build React/Flutter frontend against these APIs.
