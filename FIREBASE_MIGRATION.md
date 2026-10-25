# Migrating Automan from SQLite to Firebase (Firestore + Firebase Auth)

This documents the migration of the Automan Car Care backend from
Flask-SQLAlchemy (SQLite) to **Firebase Auth + Cloud Firestore**, while
keeping the Flask server.

## What changed

| Area | Before (SQLite) | After (Firebase) |
|------|-----------------|------------------|
| Database | SQLite via `flask_sqlalchemy` | Cloud Firestore (`google-cloud-firestore`) |
| Auth | `flask_jwt_extended` JWT | Firebase Authentication (ID-token verification) |
| IDs | Integer PKs | Firestore auto document IDs (strings) |
| Relations | Integer FKs | String document IDs |
| Many-to-many | `appointment_services` / `job_order_services` tables | `service_ids` array field on appointments/job orders |
| Sessions/transactions | `db.session` | Firestore writes per `save()` / `commit()` |

## Key files

- `firebase_config.py` – initializes the Firebase Admin SDK (Auth + Firestore),
  exposes `init_firebase()`, `get_db()`, `verify_id_token()`, `create_firebase_user()`.
- `firestore_db.py` – Firestore-backed data layer that mimics the SQLAlchemy
  API (`Model.query.filter_by().first()`, `db.session.add/commit`, etc.) so
  route code keeps working.
- `firebase_auth_utils.py` – `firebase_required` decorator + `get_jwt_identity()`
  replacing `flask_jwt_extended`.
- `flask_jwt_extended.py` – compatibility shim so existing `from flask_jwt_extended
  import ...` imports keep resolving (backed by Firebase).
- `models/*.py` – rewritten as Firestore `DocumentModel` subclasses.
- `routes/auth.py` – rewritten to create/verify Firebase accounts.
- `export_to_firestore.py` – **the data migration script** (SQLite -> Firestore).
- `mock_firestore.py` – offline in-memory Firestore for testing (`FIRESTORE_MOCK=1`).

## Setup

1. `pip install -r requirements.txt` (adds `firebase-admin`, `google-cloud-firestore`).
2. Create a Firebase project, enable **Authentication** (Email/Password) and
   **Firestore**.
3. Generate a service-account key and save it as `firebase-credentials.json`.
4. Copy `.env.example` to `.env` and set `FIREBASE_CREDENTIALS`.

## Running

```
# 1) (One time) Export existing SQLite data into Firestore
set FIREBASE_CREDENTIALS=C:\path\to\serviceAccountKey.json
set SQLITE_DB=C:\path\to\automan.db
python export_to_firestore.py

# 2) Run the Flask app (Firebase-backed)
python app.py
```

The client must sign in with Firebase and send the resulting ID token in the
`Authorization: Bearer <id_token>` header. `POST /api/auth/login` verifies the
token and returns the Firestore user profile.

## Offline testing (no Firebase project)

```
set FIRESTORE_MOCK=1
python app.py
```
Uses an in-memory Firestore so the whole app boots and the export script runs
without real credentials.

## Notes / caveats

- Firestore has no server-side JOINs or SQL. Relationships are resolved in code
  (e.g. `Appointment.services` reads `service_ids`).
- `db.session.execute(...)` for raw SQL is unsupported; association inserts are
  translated to `service_ids` array updates.
- `count()` reads documents (no native aggregate); acceptable for this dataset.
- Demo accounts are re-created in Firebase Auth by `POST /api/auth/reset-demo`.
