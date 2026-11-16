# FitTrack — Hybrid SQL + MongoDB Fitness Management System

FitTrack is a database-management project with a small web interface and a Flask API. It deliberately uses relational SQL for entities that require constraints and relationships, and MongoDB for flexible, evolving activity documents.

## Database design

### MySQL (relational source of truth)
- `members`: account identity, role and status
- `trainers`: trainer records
- `membership_plans`: plan catalogue, duration and price
- `memberships`: member-to-plan relationship and validity
- `payments`: payment records linked to members and memberships
- `fitness_classes`: scheduled classes and trainer ownership
- `class_assignments`: member bookings with a unique class/member constraint

Foreign keys, unique constraints, check constraints and indexes protect relational integrity.

### MongoDB (flexible document store)
- `workout_logs`: variable exercises, sets, repetitions, duration and notes
- `progress_logs`: body measurements and progress snapshots
- `routines`: user-defined training routines
- `activity_logs`: flexible activity history
- `feedback`: member feedback

Every Mongo document is scoped by `member_id`, which references the SQL `members.id`. This demonstrates a deliberate cross-database relationship without pretending MySQL and MongoDB share a distributed transaction.

## Run locally

1. Install Python 3.11+.
2. Create a MySQL database and a MongoDB database (local or Atlas).
3. Copy `.env.example` to `.env` and fill in credentials.
4. Install and run:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Open http://localhost:5000. The relational tables are created on the first API health check/request. Set `ADMIN_EMAIL` to the email address that should receive the administrator role when registering.

## Deploy to Vercel

Import this repository as a Python project. Configure these Production and Preview environment variables in Vercel:
- `JWT_SECRET`: long, random secret (never commit it)
- `ADMIN_EMAIL`: administrator account email
- `MYSQL_HOST`, `MYSQL_PORT`, `MYSQL_USER`, `MYSQL_PASSWORD`, `MYSQL_DATABASE`
- `MONGO_URI`, `MONGO_DATABASE`

Vercel serverless functions are stateless. Use managed MySQL and MongoDB Atlas; do not rely on local database files. The `/api/health` endpoint checks both stores and returns HTTP 503 if either is unavailable. A deployment is not considered operational until both checks return `ok`.

## API

- `POST /api/auth/register`, `POST /api/auth/login`, `GET /api/auth/me`
- `GET /api/health`
- `GET /api/dashboard` (JWT required)
- Admin CRUD: `/api/admin/{members,trainers,plans,memberships,payments,classes,assignments}` (admin JWT required)
- Member documents: `GET/POST /api/logs/{workouts,progress,routines,activities,feedback}`; `DELETE /api/logs/{kind}/{id}`

All protected routes require `Authorization: Bearer <token>`.

## Important implementation note

MySQL and MongoDB do not participate in one shared ACID transaction here. MySQL owns members and management relationships; MongoDB owns variable activity documents. The API validates and scopes each operation to the authenticated member, and the health check makes missing cloud configuration visible rather than silently falling back to ephemeral storage.
