# Delivery App Backend

This project provides a FastAPI backend for the delivery app. Orders are retrieved from Shopify and optionally supplemented with data from a Google Sheet.

## Google Sheets Fallback

When scanning an order, the backend queries Shopify first. If Shopify does not return complete information for the order, the application can look up missing fields from a Google Sheet. The fallback is only used when the following environment variables are provided:

- `GOOGLE_APPLICATION_CREDENTIALS` – path to the service account JSON credentials file with access to the sheet.
- `SHEET_ID` – ID of the Google Sheet that contains order data (the first worksheet is used).

If either variable is not set, the Google Sheets lookup is skipped and only Shopify data is used.

```bash
# Example environment setup
export GOOGLE_APPLICATION_CREDENTIALS=/path/to/creds.json
export SHEET_ID=your-sheet-id
```

The sheet is expected to have columns for order number, customer name, phone and address. `sheet_utils.get_order_from_sheet` handles flexible column names and returns the first matching row for the order.
