# Blood Donation Management System

A Flask + MySQL web application for centralized blood donation management with role-based portals:
- Admin
- Donor
- Hospital
- Blood Camp

The system includes approval workflows, donor matching logic, emergency prioritization, and optional SMS notifications.

## Architecture

### 1) Presentation Layer
- HTML templates in `templates/`
- Shared styling in `static/css/style.css`
- Minimal JS in `static/js/`

### 2) Application Layer
- Flask app bootstrap in `app.py`
- Route modules in `routes/`
- Business/auth helpers in `models/models.py`
- Notification service in `services/notifications.py`

### 3) Database Layer
- MySQL schema in `database/schema.sql`
- Incremental migrations in `database/migrations/`

## Features

- Landing page and Discover role selection page
- Role-based login/register flow
- Google OAuth validation scaffold for non-admin roles
- Email-based password reset for all roles (admin, donor, hospital, camp)
- Admin-only approval/rejection authority
- Donor profile eligibility checks:
  - Age 18 to 60
  - Health checkbox
  - Fitness confirmation
- Blood unit rule: `1 unit = 450ml`
- Hospital requests with emergency priority
- Smart donor matching score (city match, donation recency, rare-group priority)
- Unit-level blood inventory management:
  - tracking ID per unit
  - collection source (Camp / Direct Donor)
  - collection and expiry dates
  - status lifecycle (Available / Reserved / Used / Expired)
- Auto-expiry and low-stock emergency alerts on dashboards
- Request traceability:
  - status timeline history
  - allocation details with unit tracking IDs
- Blood camp event lifecycle:
  - create upcoming camp events (date, location, target units, required groups)
  - donor event registration with preferred time slot
  - mark registered donors as donated and auto-update blood stock
  - camp history and statistics (units collected, most common group, success rate)
- Admin analytics dashboard (Chart.js):
  - monthly units collected
  - blood group distribution
  - emergency vs normal request split
  - approval rate and most active camp
- Optional Twilio SMS notifications:
  - donor profile approval/rejection
  - hospital profile approval/rejection
  - camp profile approval/rejection
  - hospital blood request approval/rejection

## Folder Structure

```text
DanationManagement/
├── app.py
├── config.py
├── requirements.txt
├── .env
├── README.md
├── templates/
├── static/
├── routes/
├── models/
├── services/
├── database/
│   ├── schema.sql
│   ├── MIGRATIONS.md
│   └── migrations/
└── scripts/
```

## Prerequisites

- Python 3.10+
- MySQL Server
- (Optional) Google OAuth credentials
- (Optional) Twilio account for SMS

## Setup

1. Install dependencies:

```bash
pip install -r requirements.txt
```

2. Create database schema:
- Execute `database/schema.sql` in MySQL.

3. Run migrations (if upgrading existing DB):
- Follow `database/MIGRATIONS.md`.

4. Configure environment in `.env`:

```env
SECRET_KEY=change-this-secret
JWT_SECRET=change-this-jwt-secret
MYSQL_HOST=localhost
MYSQL_USER=root
MYSQL_PASSWORD=
MYSQL_DB=donation_management
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
TWILIO_ACCOUNT_SID=
TWILIO_AUTH_TOKEN=
TWILIO_FROM_NUMBER=
```

5. Insert default admin user:
- Generate hash:

```bash
python scripts/generate_admin_hash.py
```

- Run generated SQL insert for `admin@blood.com`.

6. Start app:

```bash
python app.py
```

### Quick demo donor seed (testing)

Create or refresh a ready-to-login donor account:

```bash
python scripts/seed_demo_donor.py
```

Default credentials:
- Email: `demo.donor@gmail.com`
- Password: `DemoDonor@123`

### Quick demo hospital seed (testing)

Create or refresh a ready-to-login hospital account:

```bash
python scripts/seed_demo_hospital.py
```

Default credentials:
- Email: `demo.hospital@gmail.com`
- Password: `DemoHospital@123`

### Quick demo camp seed (testing)

Create or refresh a ready-to-login blood camp account:

```bash
python scripts/seed_demo_camp.py
```

Default credentials:
- Email: `demo.camp@gmail.com`
- Password: `DemoCamp@123`

### Windows one-command setup + run

From project root in PowerShell:

```powershell
./scripts/setup_and_run.ps1
```

Optional flags:

```powershell
./scripts/setup_and_run.ps1 -SkipInstall
./scripts/setup_and_run.ps1 -SkipRun
```

### VS Code Task Runner

Tasks are preconfigured in `.vscode/tasks.json`:

- `Run Blood Donation App`
- `Install Dependencies Only`

Use: **Terminal → Run Task**

Open:
- `http://127.0.0.1:5000/`

## Default Flow

1. Landing Page
2. Discover Page
3. Choose role
4. Register/Login
5. Submit profile/request
6. Admin approves/rejects
7. Approved data becomes active

## API Endpoints (Admin)

- `GET /admin/api/metrics`
- `GET /admin/api/blood-availability`
- `GET /admin/api/analytics`

## Important Notes

- Admin registration is disabled by design.
- Admin login only works for credentials stored in `admin` table.
- Default seeded admin (on first run after schema setup): `onlinedonation185@gmail.com`.
- SMS delivery requires valid Twilio config.
- Google OAuth requires valid Google client credentials.
- Legacy monolithic template `templates/dashboard.html` has been removed; use role dashboards only.

## Blood Information Context

The project UI and rule messaging align with commonly used blood-safety guidance concepts such as timely access, voluntary donation, and screening-focused systems (for example, WHO blood safety publications). Actual medical eligibility criteria can vary by country and blood service policy; always verify with local regulations.

## Troubleshooting

- If imports fail in editor, ensure the selected Python interpreter matches your project environment.
- If MySQL connection fails, recheck `MYSQL_*` values in `.env`.
- If OAuth button fails, confirm `GOOGLE_CLIENT_ID` and callback settings.
- If SMS does not send, confirm Twilio values and destination phone format (E.164 recommended).
