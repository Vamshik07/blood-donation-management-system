# AI-Powered Blood Donation Management System

Real-time digital blood network connecting donors, requesters, hospitals, and blood camp organizers for faster emergency response and safer blood request handling.

## Project Goal

Build a scalable healthcare platform that:
- Connects donors and patients quickly
- Reduces delay in emergency blood availability
- Prevents fraudulent blood requests
- Supports hospitals and blood camp operations
- Uses AI modules for matching, prioritization, and prediction

## Current Implementation

This repository currently runs as a **Flask + MySQL** web platform with role-based dashboards and notification services.

## System Roles

### Admin
- Approves blood requests
- Approves hospitals
- Approves blood camp events
- Manages inventory, users, donations, analytics, and notifications

### Users (Donors / Requesters)
- Self-register (no admin approval required)
- Register as donor
- Submit blood requests
- Respond to donor alerts
- Register for approved blood camps

### Hospitals
- Register and log in to hospital dashboard
- View inventory
- Request blood and track status
- Requires admin approval

### Blood Camp Organizers
- Submit camp events and volunteer details
- Camp events require admin approval before publishing

## Supported Blood Groups

`A+`, `A-`, `B+`, `B-`, `AB+`, `AB-`, `O+`, `O-`

These groups are used in donor matching, request routing, and inventory tracking.

## Core Workflows

### 1) Donor Registration & Eligibility
Required fields:
- Full name, email, phone, password
- Blood group, age, gender
- Location (city/area)
- Last donation date
- Health eligibility answers

Eligibility rules:
- Age 18 to 60
- Minimum 3 months since last donation
- Must pass health eligibility screening

Eligible users are marked as active donors.

### 2) Fraud-Protected Blood Request
Every request requires:
- Patient name
- Required blood group
- Hospital name and location
- Units required
- Emergency level (`Normal` / `Urgent`)
- Contact number
- Relationship with patient
- Medical proof document upload
- Notes

All requests go through admin verification.

### 3) Blood Request Lifecycle
1. Requester submits blood request
2. Admin approves or rejects
3. If approved, system finds nearby eligible donors with matching blood group
4. Notifications sent (email, SMS, in-app)
5. Donors accept or decline
6. Requester sees accepted donor contacts (limited by units required)

### 4) Emergency Mode
For urgent cases:
- Instant nearby donor search
- Priority notification dispatch
- Real-time donor response updates
- Immediate accepted donor visibility

## Portals

### User Dashboard
- Submit blood requests
- View nearby camps
- Respond to donation requests
- Track donation history
- Manage availability and notifications

### Hospital Dashboard
- View blood inventory
- Request blood
- Track request history/status

### Admin Dashboard
- Manage blood requests, hospitals, camps, users
- Manage blood inventory
- View analytics and donation activity

## AI Modules (Planned / Optional)

- AI donor matching (blood group, distance, history, response rate)
- Emergency priority detection
- Blood demand prediction by city
- Smart donor eligibility checker
- AI chatbot for eligibility/camp/availability questions
- Fraud detection for suspicious/repeated requests

## AI Features Implemented

- AI-assisted request priority score (`ai_priority_score`) using emergency level, units, blood rarity, and critical-note keywords.
- Fraud risk score (`fraud_risk_score`) with flags (`fraud_flags`) based on repeated request/contact patterns.
- Enhanced donor matching now incorporates donor response rate and enforces active-donor eligibility window.

## Live Features

- Donor map with blood-group and distance filters
- Nearby hospital visibility
- Real-time donor availability
- Donor reward tiers (Bronze, Silver, Gold, Life Saver)
- Donation eligibility reminders
- Homepage impact counters (donors, requests, lives saved, camps)

## Prompt Coverage Notes

Implemented in this repo:
- Donor accounts are auto-created (no admin approval gate for donor participation).
- Blood requests remain admin-verified before donor response workflows.
- Expanded donor registration/profile fields for eligibility capture.
- Real-time admin/hospital/donor monitoring panels via polling APIs.

Still planned for full enterprise scope:
- React/Flutter frontend and Node/Django API migration (current stack is Flask + Jinja).
- True geospatial radius mapping (current matching uses location text similarity).
- ML demand prediction and AI chatbot as separate production AI services.

## Data Model (Key Tables)

- `users`
- `blood_requests`
- `donor_responses`
- `hospitals`
- `hospital_requests`
- `blood_inventory`
- `blood_camps`
- `notifications`

## Tech Stack

### Current Repo
- Backend: Flask (Python)
- Database: MySQL
- UI: Jinja templates + CSS/JS
- Notifications: Email + SMS integrations

### Scalable Target Architecture
- Frontend: React or Flutter
- Backend API: Node.js/Express or Django
- Database: PostgreSQL or Firebase
- External services: Email API, SMS gateway, Map API
- AI: Python ML services

## Quick Start (This Repo)

### Run with VS Code task
- `Run Blood Donation App`

### Install dependencies only
- `Install Dependencies Only`

### Run with virtual environment (recommended)
```powershell
cd C:\xampp\htdocs\DanationManagement
.\.venv-1\Scripts\Activate.ps1
python app.py
```

Or run directly without activation:
```powershell
cd C:\xampp\htdocs\DanationManagement
.\.venv-1\Scripts\python.exe app.py
```

### PowerShell (manual)
```powershell
cd C:\xampp\htdocs\DanationManagement
./scripts/setup_and_run.ps1
```

### Tier Routing Configuration
Admin routing controls can be adjusted via `.env`:

```env
DONOR_ROUTING_EMERGENCY_PRIORITY_THRESHOLD=70
RARE_BLOOD_PROTECTION_ENABLED=true
```

- `DONOR_ROUTING_EMERGENCY_PRIORITY_THRESHOLD`: Minimum AI priority score required to activate Tier 4 (`O-`) emergency routing.
- `RARE_BLOOD_PROTECTION_ENABLED`: When `true`, protects `O-` from non-critical cross-group usage.

## Security & Compliance Essentials

- Role-based access control
- Admin approvals for high-risk workflows
- Medical-proof upload for blood requests
- Audit-friendly request/donation tracking
- Input validation and secure auth/session practices

## Project Status

Production-oriented foundation is implemented. AI modules and map intelligence can be enabled incrementally as separate services.

## Upgrade Scaffold Added

A parallel Django REST + PostgreSQL migration scaffold is available at [upgrade/drf_backend](upgrade/drf_backend) with geospatial-ready models and starter APIs.

## Technologies and Languages Used

### Languages
- Python
- SQL
- HTML
- CSS
- JavaScript
- PowerShell

### Technologies
- Flask (web framework)
- Jinja2 (templating)
- MySQL (database)
- Django REST Framework scaffold (upgrade path)
- VS Code Tasks (development workflow)