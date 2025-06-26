"""
delivery-app FastAPI backend
───────────────────────────
✓ Barcode / manual scan → Google Sheet (+ Shopify lookup)
✓ Re-usable Google Sheet rows & data-validation
✓ Driver-fee calculation + Payout roll-up
✓ Order & payout queries for the mobile / web app
✓ Status update incl. “Returned” handling
✓ Daily archive of yesterday’s rows

Déployé sur Render via the Dockerfile you created earlier.
"""
from dotenv import load_dotenv
load_dotenv()

import os
import json
import datetime as dt
from typing import List, Optional
from datetime import timezone          
import requests
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel



# ───────────────────────────────────────────────────────────────
# CONFIGURATION  ––––– edit via env-vars in Render dashboard
# ───────────────────────────────────────────────────────────────
DELIVERY_GUY_NAME      = os.getenv("DELIVERY_GUY_NAME", "anouar")
SHEET_NAME             = f"{DELIVERY_GUY_NAME}_Orders"
PAYOUTS_SHEET_NAME     = f"{DELIVERY_GUY_NAME}_Payouts"

SHOPIFY_STORES = [
    {
        "name": "irrakids",
        "api_key": os.getenv("IRRAKIDS_API_KEY", ""),
        "password": os.getenv("IRRAKIDS_PASSWORD", ""),
        "domain": "nouralibas.myshopify.com",
    },
    {
        "name": "irranova",
        "api_key": os.getenv("IRRANOVA_API_KEY", ""),
        "password": os.getenv("IRRANOVA_PASSWORD", ""),
        "domain": "fdd92b-2e.myshopify.com",
    },
]

DELIVERY_STATUSES   = [
    "Dispatched", "Livré", "En cours",
    "Pas de réponse 1", "Pas de réponse 2", "Pas de réponse 3",
    "Annulé", "Refusé", "Rescheduled", "Returned"
]
COMPLETED_STATUSES  = ["Livré", "Annulé", "Refusé"]
NORMAL_DELIVERY_FEE = 20
EXCHANGE_DELIVERY_FEE = 10

# Google-Service-Account credentials JSON (Render env-var, **not** a file path)
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
if not GOOGLE_CREDENTIALS_JSON:
    raise RuntimeError("Missing GOOGLE_SERVICE_ACCOUNT_JSON env-var")

# ───────────────────────────────────────────────────────────────
# GOOGLE SHEETS HELPERS  (gspread + service account)
# ───────────────────────────────────────────────────────────────
import gspread
from google.oauth2.service_account import Credentials

SCOPES         = ["https://www.googleapis.com/auth/spreadsheets"]
creds_dict     = json.loads(GOOGLE_CREDENTIALS_JSON)
credentials    = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
gc             = gspread.authorize(credentials)
spreadsheet_id = creds_dict.get("spreadsheet_id")  # store your main sheet id here
if not spreadsheet_id:
    raise RuntimeError("Add 'spreadsheet_id' key to your service-account JSON")

ss = gc.open_by_key(spreadsheet_id)


def _get_or_create_sheet(sheet_name: str, header: List[str]) -> gspread.Worksheet:
    try:
        return ss.worksheet(sheet_name)
    except gspread.WorksheetNotFound:
        ws = ss.add_worksheet(title=sheet_name, rows="1", cols=str(len(header)))
        ws.append_row(header)
        return ws


# ───────────────────────────────────────────────────────────────
# Pydantic models
# ───────────────────────────────────────────────────────────────

class ScanIn(BaseModel):
    barcode: str

class ScanResult(BaseModel):
    result: str
    order: str
    tag: str = ""
    deliveryStatus: str = "Dispatched"


class StatusUpdate(BaseModel):
    order_name: str
    new_status: Optional[str] = None   # one of DELIVERY_STATUSES
    note: Optional[str] = None
    cash_amount: Optional[float] = None


class ManualAdd(BaseModel):
    order_name: str


# ───────────────────────────────────────────────────────────────
# FastAPI init + CORS (mobile apps & localhost dev)
# ───────────────────────────────────────────────────────────────
app = FastAPI(title="Delivery FastAPI backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5500"],
    allow_methods=["GET", "POST", "PUT"],
    allow_headers=["*"],
)


# ───────────────────────────────────────────────────────────────
# Utility functions – exact ports of your Apps Script logic
# ───────────────────────────────────────────────────────────────
def calculate_driver_fee(tags: str) -> int:
    return EXCHANGE_DELIVERY_FEE if "ch" in (tags or "").lower() else NORMAL_DELIVERY_FEE


def get_primary_display_tag(tags: str) -> str:
    l = (tags or "").lower()
    if "big" in l:
        return "big"
    if "k" in l:
        return "k"
    if "12livery" in l:
        return "12livery"
    if "12livrey" in l:
        return "12livrey"
    if "fast" in l:
        return "fast"
    if "oscario" in l:
        return "oscario"
    if "sand" in l:
        return "sand"
    return ""


def get_order_from_store(order_name: str, store_cfg: dict) -> Optional[dict]:
    """Call Shopify Admin API by order name (#1234)."""
    auth = (store_cfg["api_key"], store_cfg["password"])
    url = f"https://{store_cfg['domain']}/admin/api/2023-07/orders.json"
    params = {"name": order_name}
    r = requests.get(url, auth=auth, params=params, timeout=10)
    try:
        r.raise_for_status()
    except requests.HTTPError:
        return None
    data = r.json()
    return data.get("orders", [{}])[0] if data.get("orders") else None


# ───────────────────────────────────────────────────────────────
# Core functions – Sheets logic
# ───────────────────────────────────────────────────────────────
ORDER_HEADER = [
    "Timestamp", "Order Name", "Customer Name", "Customer Phone",
    "Address", "Tags", "Fulfillment", "Order Status",
    "Store", "Delivery Status", "Notes", "Scan Date",
    "Cash Amount", "Driver Fee", "Payout ID"
]

PAYOUT_HEADER = [
    "Payout ID", "Date Created", "Orders", "Total Cash",
    "Total Fees", "Total Payout", "Status", "Date Paid"
]


def order_exists(ws: gspread.Worksheet, order_name: str) -> bool:
    col = ws.col_values(2)  # column B
    return order_name in col


def get_order_row(ws: gspread.Worksheet, order_name: str) -> Optional[List]:
    data = ws.get_all_values()
    for row in data[1:]:
        if row[1] == order_name:
            return row
    return None


def add_to_payout(ws_orders: gspread.Worksheet, payout_ws: gspread.Worksheet,
                  order_name: str, cash_amount: float, driver_fee: float) -> str:
    """Create (or extend) an open payout row and write payout ID back to orders sheet."""
    data = payout_ws.get_all_values()
    open_row_idx = None
    open_payout_id = None
    # search from bottom up
    for idx in range(len(data) - 1, 0, -1):
        if data[idx][6].lower() != "paid":      # status column
            open_row_idx = idx
            open_payout_id = data[idx][0]
            break

    if open_row_idx is None:
        # create new payout
        now = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        payout_id = f"PO-{dt.datetime.now().strftime('%Y%m%d-%H%M')}"
        new_row = [
            payout_id, now, order_name,
            cash_amount, driver_fee,
            cash_amount - driver_fee,
            "pending", ""
        ]
        payout_ws.append_row(new_row)
    else:
        # update existing payout line
        orders_cell = payout_ws.cell(open_row_idx + 1, 3)  # 1-based API
        cash_cell = payout_ws.cell(open_row_idx + 1, 4)
        fee_cell = payout_ws.cell(open_row_idx + 1, 5)
        payout_cell = payout_ws.cell(open_row_idx + 1, 6)

        orders_cell.value = f"{orders_cell.value}, {order_name}" if orders_cell.value else order_name
        cash_total = float(cash_cell.value or 0) + cash_amount
        fee_total = float(fee_cell.value or 0) + driver_fee
        payout_total = cash_total - fee_total

        cash_cell.value = cash_total
        fee_cell.value = fee_total
        payout_cell.value = payout_total
        payout_ws.update_cells([orders_cell, cash_cell, fee_cell, payout_cell])

        payout_id = open_payout_id

    # write back to orders sheet
    order_cells = ws_orders.findall(order_name)
    if order_cells:
        row_idx = order_cells[0].row
        ws_orders.update_cell(row_idx, 15, payout_id)

    return payout_id


# ───────────────────────────────────────────────────────────────
# FastAPI ROUTES
# ───────────────────────────────────────────────────────────────
@app.get("/health", tags=["meta"])
def health():
    return {"status": "ok", "time": dt.datetime.utcnow().isoformat()}


@app.post("/scan", response_model=ScanResult, tags=["orders"])
def scan(payload: ScanIn):
    barcode = payload.barcode
    """
    Scan a barcode (or manual order name).
    Replicates `appendScan()` logic.
    """
    ws = _get_or_create_sheet(SHEET_NAME, ORDER_HEADER)

    order_number = "#" + "".join(filter(str.isdigit, barcode.strip()))
    if len(order_number) <= 1:
        raise HTTPException(status_code=400, detail="Invalid barcode")

    # already scanned?
    if order_exists(ws, order_number):
        existing = get_order_row(ws, order_number)
        return ScanResult(
            result="⚠️ Already Scanned",
            order=order_number,
            tag=get_primary_display_tag(existing[5]),
            deliveryStatus=existing[9],
        )

    # Shopify look-up – last 50 days window
    window_start = dt.datetime.now(timezone.utc) - dt.timedelta(days=50)
    chosen_order = None
    chosen_store_name = ""
    for store in SHOPIFY_STORES:
        order = get_order_from_store(order_number, store)
        if not order:
            continue
        created_at = dt.datetime.fromisoformat(order["created_at"].replace("Z", "+00:00"))
        if created_at >= window_start and (not chosen_order or created_at > dt.datetime.fromisoformat(chosen_order["created_at"].replace("Z", "+00:00"))):
            chosen_order = order
            chosen_store_name = store["name"]

    # default values
    tags = fulfillment = order_status = customer_name = phone = address = ""
    cash_amount = 0.0
    result_msg = "❌ Not Found"

    if chosen_order:
        tags = chosen_order.get("tags", "")
        fulfillment = chosen_order.get("fulfillment_status", "unfulfilled")
        order_status = "closed" if chosen_order.get("cancelled_at") else "open"
        result_msg = "⚠️ Cancelled" if chosen_order.get("cancelled_at") else (
            "❌ Unfulfilled" if fulfillment != "fulfilled" else "✅ OK"
        )
        cash_amount = float(chosen_order.get("total_price") or 0)
        if chosen_order.get("shipping_address"):
            sa = chosen_order["shipping_address"]
            customer_name = sa.get("name", "")
            phone = sa.get("phone", "") or chosen_order.get("phone", "")
            address = ", ".join(filter(None, [
                sa.get("address1"), sa.get("address2"),
                sa.get("city"), sa.get("province")
            ]))

    driver_fee = calculate_driver_fee(tags)
    now_ts = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    scan_date = dt.datetime.now().strftime("%Y-%m-%d")

    # Actually append row
    new_row = [
        now_ts, order_number, customer_name, phone, address, tags, fulfillment,
        order_status, chosen_store_name, "Dispatched", "", scan_date,
        cash_amount, driver_fee, ""
    ]
    ws.append_row(new_row)

    return ScanResult(
        result=result_msg,
        order=order_number,
        tag=get_primary_display_tag(tags),
        deliveryStatus="Dispatched"
    )


@app.get("/orders", tags=["orders"])
def list_active_orders():
    ws = _get_or_create_sheet(SHEET_NAME, ORDER_HEADER)
    data = ws.get_all_values()[1:]  # skip header
    orders = []
    for row in data:
        if not row or row[9] in COMPLETED_STATUSES:
            continue
        orders.append({
            "timestamp": row[0],
            "orderName": row[1],
            "customerName": row[2],
            "customerPhone": row[3],
            "address": row[4],
            "tags": row[5],
            "deliveryStatus": row[9] or "Dispatched",
            "notes": row[10],
            "scanDate": row[11],
            "cashAmount": float(row[12] or 0),
            "driverFee": float(row[13] or 0),
            "payoutId": row[14],
        })
    return orders[::-1]  # newest first


@app.put("/order/status", tags=["orders"])
def update_order_status(payload: StatusUpdate, bg: BackgroundTasks):
    if payload.new_status and payload.new_status not in DELIVERY_STATUSES:
        raise HTTPException(status_code=400, detail="Invalid new_status")

    ws = _get_or_create_sheet(SHEET_NAME, ORDER_HEADER)
    payout_ws = _get_or_create_sheet(PAYOUTS_SHEET_NAME, PAYOUT_HEADER)

    cells = ws.findall(payload.order_name)
    if not cells:
        raise HTTPException(status_code=404, detail="Order not found")

    row_idx = cells[0].row
    row_values = ws.row_values(row_idx)

    # update delivery status
    if payload.new_status:
        ws.update_cell(row_idx, 10, payload.new_status)  # column J
    # update notes
    if payload.note is not None:
        ws.update_cell(row_idx, 11, payload.note)
    # update cash amount
    if payload.cash_amount is not None:
        ws.update_cell(row_idx, 13, payload.cash_amount)

    # handle payout on Livré
    if payload.new_status == "Livré" and row_values[9] != "Livré":
        tags = row_values[5]
        driver_fee = calculate_driver_fee(tags)
        cash_amt = payload.cash_amount or float(row_values[12] or 0)
        add_to_payout(ws, payout_ws, payload.order_name, cash_amt, driver_fee)

    # remove from dashboard on Returned
    if payload.new_status == "Returned":
        ws.delete_rows(row_idx, row_idx)

    return {"success": True}


@app.get("/payouts", tags=["payouts"])
def get_payouts():
    ws = _get_or_create_sheet(PAYOUTS_SHEET_NAME, PAYOUT_HEADER)
    data = ws.get_all_values()[1:]
    return [
        {
            "payoutId": r[0],
            "dateCreated": r[1],
            "orders": r[2],
            "totalCash": float(r[3] or 0),
            "totalFees": float(r[4] or 0),
            "totalPayout": float(r[5] or 0),
            "status": r[6] or "pending",
            "datePaid": r[7],
        }
        for r in reversed(data)
    ]


@app.post("/payout/mark-paid/{payout_id}", tags=["payouts"])
def mark_payout_paid(payout_id: str):
    ws = _get_or_create_sheet(PAYOUTS_SHEET_NAME, PAYOUT_HEADER)
    cells = ws.findall(payout_id)
    if not cells:
        raise HTTPException(status_code=404, detail="Payout not found")

    row_idx = cells[0].row
    ws.update_cell(row_idx, 7, "paid")  # status
    ws.update_cell(row_idx, 8, dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    return {"success": True}


@app.post("/archive-yesterday", tags=["maintenance"])
def archive_yesterday():
    ws = _get_or_create_sheet(SHEET_NAME, ORDER_HEADER)
    data = ws.get_all_values()
    if len(data) <= 1:
        return {"archived": 0}

    header = data[0]
    today = dt.datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    archive_rows = []
    keep_rows = [header]

    for row in data[1:]:
        ts_str = row[0]
        try:
            ts = dt.datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            keep_rows.append(row)
            continue
        if ts < today:
            archive_rows.append(row)
        else:
            keep_rows.append(row)

    if archive_rows:
        y_date = (today - dt.timedelta(days=1)).strftime("%Y-%m-%d")
        archive_name = f"{DELIVERY_GUY_NAME}_Archive_{y_date}"
        archive_ws = _get_or_create_sheet(archive_name, header)
        archive_ws.append_rows(archive_rows)
        ws.clear()
        ws.append_rows(keep_rows)
    return {"archived": len(archive_rows)}
from fastapi.staticfiles import StaticFiles
app.mount("/", StaticFiles(directory=".", html=True), name="static")