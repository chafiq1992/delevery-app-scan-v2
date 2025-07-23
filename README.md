# Delivery App Backend

This repository contains a FastAPI service used to scan delivery orders and
store them in a database. Order information is loaded from a Google Sheet
when scans occur.

## Environment variables

The backend is configured entirely through environment variables:

- `DATABASE_URL` – SQLAlchemy connection string for the database.
- `ADMIN_PASSWORD` – password for the admin interface (defaults to
  `admin123`).
- `REDIS_URL` – optional Redis instance used for caching.
- `SHEET_ID` – ID of the Google Sheet providing fallback order data.
- `GOOGLE_CREDENTIALS_B64` – **preferred**; base64 encoded service account JSON.
- `GOOGLE_APPLICATION_CREDENTIALS` – optional path to the credentials file.

Either `GOOGLE_CREDENTIALS_B64` or `GOOGLE_APPLICATION_CREDENTIALS` must be
provided so the application can access the Google Sheet.

To create a credentials file from the encoded value you can run:

```bash
echo "$GOOGLE_CREDENTIALS_B64" | base64 -d > creds.json
```

## Running the tests

Install the requirements from `backend/requirements.txt` (which now includes
`aiosqlite`) and run:

```bash
cd backend
pytest
```

## Starting the application

For local development the app can be started with `uvicorn`:

```bash
cd backend
uvicorn app.main:app --reload
```

The Dockerfile provided in `backend/` runs the same app using Gunicorn when
deployed.
Further UI notes, including the chat-style timeline for order notes, are documented in [docs/chat_timeline_notes.md](docs/chat_timeline_notes.md).
