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
import base64, json, os
import datetime as dt
from typing import List, Optional
from datetime import timezone          
import requests
from fastapi import FastAPI, HTTPException, BackgroundTasks, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from pydantic import BaseModel
from starlette.middleware.cors import CORSMiddleware
import gspread
from google.oauth2.service_account import Credentials

# ---   Google secret handling  ---------------------------------
cred_b64 = os.getenv("GOOGLE_CREDENTIALS_B64", "")
if not cred_b64:
    raise RuntimeError("Missing GOOGLE_CREDENTIALS_B64 env-var")

cred_json      = base64.b64decode(cred_b64).decode("utf-8")
creds_dict     = json.loads(cred_json)
SCOPES         = ["https://www.googleapis.com/auth/spreadsheets"]
credentials    = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
gc             = gspread.authorize(credentials)

spreadsheet_id = os.getenv("SPREADSHEET_ID")
if not spreadsheet_id:
    raise RuntimeError("Missing SPREADSHEET_ID env-var")
ss = gc.open_by_key(spreadsheet_id)

# ───────────────────────────────────────────────────────────────
# CONFIGURATION  ––––– edit via env-vars in Render dashboard
# ───────────────────────────────────────────────────────────────

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

# ✅ Define the path correctly
static_path = os.path.join(os.path.dirname(__file__), "static")

# ✅ Mount the /static directory
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
# 👇 ADD THIS EXACTLY BELOW


DRIVERS = {
    "driver1": {
        "sheet_id": spreadsheet_id,
        "order_tab": "driver1_Orders",
        "payouts_tab": "driver1_Payouts"
    },
    "driver2": {
        "sheet_id": spreadsheet_id,
        "order_tab": "driver2_Orders",
        "payouts_tab": "driver2_Payouts"
    },
    "driver3": {
        "sheet_id": spreadsheet_id,
        "order_tab": "driver3_Orders",
        "payouts_tab": "driver3_Payouts"
    },
    "driver4": {
        "sheet_id": spreadsheet_id,
        "order_tab": "driver4_Orders",
        "payouts_tab": "driver4_Payouts"
    },
}

@app.get("/", response_class=HTMLResponse)
async def show_login():
    return FileResponse(os.path.join(STATIC_DIR, "login.html"))

@app.post("/login", response_class=HTMLResponse)
async def login(driver_id: str = Form(...)):
    if driver_id in DRIVERS:
        response = RedirectResponse(url=f"/static/index.html?driver={driver_id}", status_code=302)
        return response
    return HTMLResponse("<h2>Invalid driver ID</h2>", status_code=401)

# Allow cross-origin requests
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
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
def _tabs_for(driver_id: str):
    cfg = DRIVERS.get(driver_id)
    if not cfg:
        raise HTTPException(status_code=400, detail="Invalid driver")
    return (
        _get_or_create_sheet(cfg["order_tab"],  ORDER_HEADER),
        _get_or_create_sheet(cfg["payouts_tab"], PAYOUT_HEADER)
    )

@app.get("/health", tags=["meta"])
def health():
    return {"status": "ok", "time": dt.datetime.utcnow().isoformat()}

# -------------------------------  SCAN  -------------------------------
@app.post("/scan", response_model=ScanResult, tags=["orders"])
def scan(
    payload: ScanIn,
    driver: str = Query(..., description="driver1 / driver2 / …")
):
    ws_orders, _ = _tabs_for(driver)
    barcode = payload.barcode.strip()
    order_number = "#" + "".join(filter(str.isdigit, barcode))

    if len(order_number) <= 1:
        raise HTTPException(status_code=400, detail="Invalid barcode")

    # already scanned?
    if order_exists(ws_orders, order_number):
        existing = get_order_row(ws_orders, order_number)
        return ScanResult(
            result="⚠️ Already scanned",
            order=order_number,
            tag=get_primary_display_tag(existing[5]),
            deliveryStatus=existing[9],
        )

    # --- Shopify look-up (unchanged) ----------------------------------
    window_start = dt.datetime.now(timezone.utc) - dt.timedelta(days=50)
    chosen_order, chosen_store_name = None, ""
    for store in SHOPIFY_STORES:
        order = get_order_from_store(order_number, store)
        if order:
            created_at = dt.datetime.fromisoformat(
                order["created_at"].replace("Z", "+00:00")
            )
            if created_at >= window_start and (
                not chosen_order
                or created_at > dt.datetime.fromisoformat(
                    chosen_order["created_at"].replace("Z", "+00:00")
                )
            ):
                chosen_order, chosen_store_name = order, store["name"]

    # --- sheet append (same logic, but to the driver tab) -------------
    tags = chosen_order.get("tags", "") if chosen_order else ""
    fulfillment = chosen_order.get("fulfillment_status", "unfulfilled") if chosen_order else ""
    order_status = "closed" if (chosen_order and chosen_order.get("cancelled_at")) else "open"
    customer_name = phone = address = ""
    cash_amount = 0.0
    result_msg = "❌ Not found"

    if chosen_order:
        result_msg = (
            "⚠️ Cancelled" if chosen_order.get("cancelled_at")
            else "❌ Unfulfilled" if fulfillment != "fulfilled"
            else "✅ OK"
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

    now_ts   = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    scan_day = dt.datetime.now().strftime("%Y-%m-%d")
    driver_fee = calculate_driver_fee(tags)

    ws_orders.append_row([
        now_ts, order_number, customer_name, phone, address, tags, fulfillment,
        order_status, chosen_store_name, "Dispatched", "", scan_day,
        cash_amount, driver_fee, ""
    ])

    return ScanResult(
        result=result_msg,
        order=order_number,
        tag=get_primary_display_tag(tags),
        deliveryStatus="Dispatched",
    )

# -----------------------------  ORDERS  -------------------------------
@app.get("/orders", tags=["orders"])
def list_active_orders(driver: str = Query(...)):
    ws_orders, _ = _tabs_for(driver)
    data = ws_orders.get_all_values()[1:]  # skip header
    active = []
    for r in data:
        if not r or r[9] in COMPLETED_STATUSES:
            continue
        active.append({
            "timestamp":    r[0],
            "orderName":    r[1],
            "customerName": r[2],
            "customerPhone":r[3],
            "address":      r[4],
            "tags":         r[5],
            "deliveryStatus": r[9] or "Dispatched",
            "notes":        r[10],
            "scanDate":     r[11],
            "cashAmount":   float(r[12] or 0),
            "driverFee":    float(r[13] or 0),
            "payoutId":     r[14],
        })
    return list(reversed(active))

@app.put("/order/status", tags=["orders"])
def update_order_status(
    payload: StatusUpdate,
    bg: BackgroundTasks,
    driver: str = Query(...)
):
    if payload.new_status and payload.new_status not in DELIVERY_STATUSES:
        raise HTTPException(status_code=400, detail="Invalid status")

    ws_orders, ws_payouts = _tabs_for(driver)
    cells = ws_orders.findall(payload.order_name)
    if not cells:
        raise HTTPException(status_code=404, detail="Order not found")

    row = cells[0].row
    row_vals = ws_orders.row_values(row)

    if payload.new_status:
        ws_orders.update_cell(row, 10, payload.new_status)
    if payload.note is not None:
        ws_orders.update_cell(row, 11, payload.note)
    if payload.cash_amount is not None:
        ws_orders.update_cell(row, 13, payload.cash_amount)

    # add to payout if freshly delivered
    if payload.new_status == "Livré" and row_vals[9] != "Livré":
        driver_fee = calculate_driver_fee(row_vals[5])
        cash_amt = payload.cash_amount or float(row_vals[12] or 0)
        add_to_payout(ws_orders, ws_payouts,
                      payload.order_name, cash_amt, driver_fee)

    # clean up list if returned
    if payload.new_status == "Returned":
        ws_orders.delete_rows(row, row)

    return {"success": True}

# ----------------------------  PAYOUTS  -------------------------------
@app.get("/payouts", tags=["payouts"])
def get_payouts(driver: str = Query(...)):
    _, ws_payouts = _tabs_for(driver)
    rows = ws_payouts.get_all_values()[1:]
    return [{
        "payoutId":   r[0],
        "dateCreated":r[1],
        "orders":     r[2],
        "totalCash":  float(r[3] or 0),
        "totalFees":  float(r[4] or 0),
        "totalPayout":float(r[5] or 0),
        "status":     r[6] or "pending",
        "datePaid":   r[7],
    } for r in reversed(rows)]

@app.post("/payout/mark-paid/{payout_id}", tags=["payouts"])
def mark_payout_paid(payout_id: str, driver: str = Query(...)):
    _, ws_payouts = _tabs_for(driver)
    cells = ws_payouts.findall(payout_id)
    if not cells:
        raise HTTPException(status_code=404, detail="Payout not found")

    row = cells[0].row
    ws_payouts.update_cell(row, 7, "paid")
    ws_payouts.update_cell(row, 8, dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
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
