# Delivery App Scan V2

## Sheet Migration

This repository includes a helper script for importing orders from a Google Sheet into the database.

### Required environment variables

- `GOOGLE_APPLICATION_CREDENTIALS` – path to a Google service account json file.
- `SHEET_ID` – spreadsheet identifier or name.
- `DATABASE_URL` – SQLAlchemy database url.
- `DRIVER_ID` – optional driver id for created orders (defaults to `abderrehman`).

### Usage

Run the migration script with Python:

```bash
python backend/scripts/migrate_sheet.py
```

The script reads the sheet specified by `SHEET_ID` and inserts every row after the header as an `Order` in the database.
