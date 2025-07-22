"""
delivery-app FastAPI backend
───────────────────────────
✓ Barcode / manual scan stored in the database (orders looked up from Shopify)
✓ Optionally falls back to Google Sheets when Shopify details are missing
✓ SQLAlchemy models for drivers, orders & payouts
✓ Driver-fee calculation + payout roll-up
✓ Order & payout queries for the mobile / web app
✓ Status update incl. “Returned” handling
✓ Daily archive of yesterday’s orders

Déployé sur Render via the Dockerfile you created earlier.
"""

from dotenv import load_dotenv

load_dotenv()
import os
import json
import asyncio
import logging

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)
import datetime as dt
from typing import List, Optional
from datetime import timezone
import httpx
from fastapi import (
    FastAPI,
    HTTPException,
    BackgroundTasks,
    Query,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi import Form
from cachetools import TTLCache
from fastapi.responses import HTMLResponse, FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from .sheet_utils import load_sheet_orders, get_order_from_sheet

try:
    import redis.asyncio as redis  # type: ignore
except Exception:  # pragma: no cover - redis optional
    redis = None

from pydantic import BaseModel

from sqlalchemy import select, or_, func
from sqlalchemy.ext.asyncio import AsyncSession
from .db import (
    get_session,
    init_db,
    Driver,
    Order,
    Payout,
    EmployeeLog,
    DeliveryNote,
    DeliveryNoteItem,
    VerificationOrder,
)

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


DELIVERY_STATUSES = [
    "Dispatched",
    "Livré",
    "En cours",
    "Pas de réponse 1",
    "Pas de réponse 2",
    "Pas de réponse 3",
    "Annulé",
    "Refusé",
    "Rescheduled",
    "Returned",
    "Deleted",
]
COMPLETED_STATUSES = [
    "Livré",
    "Deleted",
]
NORMAL_DELIVERY_FEE = 20
EXCHANGE_DELIVERY_FEE = 10


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
    noteId: Optional[int] = None


class StatusUpdate(BaseModel):
    order_name: str
    new_status: Optional[str] = None  # one of DELIVERY_STATUSES
    note: Optional[str] = None
    driver_note: Optional[str] = None
    cash_amount: Optional[float] = None
    scheduled_time: Optional[str] = None
    comm_log: Optional[str] = None
    follow_log: Optional[str] = None


class ManualAdd(BaseModel):
    order_name: str


class EmployeeLog(BaseModel):
    employee: str
    order: Optional[str] = None
    amount: Optional[float] = None


class VerificationUpdate(BaseModel):
    driver_id: Optional[str] = None
    scan_time: Optional[str] = None  # YYYY-MM-DD HH:MM:SS


class PayoutUpdate(BaseModel):
    orders: Optional[str] = None
    total_cash: Optional[float] = None
    total_fees: Optional[float] = None
    total_payout: Optional[float] = None
    date_created: Optional[str] = None


# ───────────────────────────────────────────────────────────────
# FastAPI init + CORS (mobile apps & localhost dev)
# ───────────────────────────────────────────────────────────────
app = FastAPI(title="Delivery FastAPI backend")


@app.on_event("startup")
async def startup_event():
    await init_db()


# ✅ Define the path correctly
static_path = os.path.join(os.path.dirname(__file__), "static")

# ✅ Mount the /static directory
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


async def load_drivers(session):
    result = await session.execute(select(Driver))
    return {d.id: d for d in result.scalars()}


# Simple admin password (override via env var)
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")

# Shared cache (Redis if available, fallback to in-memory TTLCache)
REDIS_URL = os.getenv("REDIS_URL")
if redis and REDIS_URL:
    redis_client = redis.from_url(REDIS_URL, decode_responses=True)
else:  # pragma: no cover - used in local/dev
    redis_client = None

orders_cache = TTLCache(maxsize=8, ttl=60)
payouts_cache = TTLCache(maxsize=8, ttl=60)
archive_cache = TTLCache(maxsize=8, ttl=60)
followups_cache = TTLCache(maxsize=8, ttl=60)


async def cache_get(namespace: str, key: str):
    if redis_client:
        val = await redis_client.hget(namespace, key)
        return json.loads(val) if val else None
    cache = {
        "orders": orders_cache,
        "payouts": payouts_cache,
        "archive": archive_cache,
        "followups": followups_cache,
    }[namespace]
    return cache.get(key)


async def cache_set(namespace: str, key: str, value, ttl: int = 60):
    if redis_client:
        await redis_client.hset(namespace, key, json.dumps(value))
        await redis_client.expire(namespace, ttl)
    else:
        cache = {
            "orders": orders_cache,
            "payouts": payouts_cache,
            "archive": archive_cache,
            "followups": followups_cache,
        }[namespace]
        cache[key] = value


async def cache_delete(namespace: str, key: str):
    if redis_client:
        await redis_client.hdel(namespace, key)
    else:
        cache = {
            "orders": orders_cache,
            "payouts": payouts_cache,
            "archive": archive_cache,
            "followups": followups_cache,
        }[namespace]
        cache.pop(key, None)


class ConnectionManager:
    """WebSocket connection manager for push notifications."""

    def __init__(self) -> None:
        self.active: list[WebSocket] = []

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self.active.append(ws)

    def disconnect(self, ws: WebSocket) -> None:
        if ws in self.active:
            self.active.remove(ws)

    async def broadcast(self, data: dict) -> None:
        for ws in list(self.active):
            try:
                await ws.send_json(data)
            except Exception:
                self.disconnect(ws)


manager = ConnectionManager()


async def sync_verification_orders(date_str: str, session: AsyncSession) -> None:
    """Import new orders from the Google Sheet for the given date."""
    rows = await asyncio.to_thread(load_sheet_orders)
    logger.info("Loaded %d rows from sheet", len(rows))
    if not rows:
        return
    created = 0
    seen: set[str] = set()
    for row in rows:
        row_date = row.get("order_date") or date_str
        if row_date != date_str:
            continue
        order_num = row["order_name"]
        if order_num in seen:
            continue
        seen.add(order_num)
        existing = await session.scalar(
            select(VerificationOrder).where(
                VerificationOrder.order_name == order_num
            )
        )
        if existing:
            # If driver not yet set, try to fetch from scans
            if not existing.driver_id:
                scanned = await session.scalar(
                    select(Order)
                    .where(Order.order_name == order_num)
                    .order_by(Order.timestamp.desc())
                )
                if scanned:
                    existing.driver_id = scanned.driver_id
                    existing.scan_time = scanned.timestamp
                    logger.info(
                        "Matched %s to driver %s", row["order_name"], scanned.driver_id
                    )
            continue

        scanned = await session.scalar(
            select(Order)
            .where(Order.order_name == order_num)
            .order_by(Order.timestamp.desc())
        )
        vo = VerificationOrder(
            order_date=date_str,
            order_name=order_num,
            customer_name=row.get("customer_name", ""),
            customer_phone=row.get("customer_phone", ""),
            address=row.get("address", ""),
            cod_total=row.get("cod_total", ""),
            city=row.get("city", ""),
            driver_id=scanned.driver_id if scanned else None,
            scan_time=scanned.timestamp if scanned else None,
        )
        session.add(vo)
        created += 1
        if scanned:
            logger.info(
                "Matched %s to driver %s", row["order_name"], scanned.driver_id
            )
        logger.info("Created verification order for %s", row["order_name"])
    await session.commit()
    if created:
        logger.info("Imported %d verification rows", created)


@app.get("/", response_class=HTMLResponse)
async def show_login():
    return FileResponse(os.path.join(STATIC_DIR, "login.html"))


@app.get("/admin", response_class=HTMLResponse)
async def show_admin_login():
    return FileResponse(os.path.join(STATIC_DIR, "admin_login.html"))


@app.get("/follow", response_class=HTMLResponse)
async def show_follow_login():
    """Serve login page for follow agents."""
    return FileResponse(os.path.join(STATIC_DIR, "follow_login.html"))


@app.post("/login", response_class=HTMLResponse)
async def login(driver_id: str = Form(...)):
    async for session in get_session():
        drivers = await load_drivers(session)
        if driver_id in drivers:
            response = RedirectResponse(
                url=f"/static/index.html?driver={driver_id}", status_code=302
            )
            return response
    return HTMLResponse("<h2>Invalid driver ID</h2>", status_code=401)


@app.post("/admin/login")
async def admin_login(password: str = Form(...)):
    if password == ADMIN_PASSWORD:
        return {"success": True}
    raise HTTPException(status_code=401, detail="Invalid admin password")


@app.post("/follow/login")
async def follow_login(password: str = Form(...)):
    """Authenticate follow agents using the admin password for now."""
    if password == ADMIN_PASSWORD:
        return {"success": True}
    raise HTTPException(status_code=401, detail="Invalid follow password")


@app.get("/drivers")
async def list_drivers():
    async for session in get_session():
        drivers = await load_drivers(session)
        return list(drivers.keys())


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await manager.connect(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(ws)


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
    return (
        EXCHANGE_DELIVERY_FEE if "ch" in (tags or "").lower() else NORMAL_DELIVERY_FEE
    )


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


def safe_float(val):
    """Return a float or 0.0 for falsy/non-numeric values."""
    try:
        return float(val)
    except (TypeError, ValueError):
        return 0.0


def get_cell(row, idx, default=""):
    """Safely access a cell from a row, providing a default if missing."""
    return row[idx] if idx < len(row) else default


def parse_timestamp(val: str) -> dt.datetime:
    """Parse timestamp strings with optional microseconds and timezone."""
    val = (val or "").strip()
    parts = val.split()
    if len(parts) > 2 and parts[-1].isalpha():
        val = " ".join(parts[:-1])
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            return dt.datetime.strptime(val, fmt)
        except ValueError:
            continue
    return dt.datetime.fromisoformat(val)


async def get_order_from_store(order_name: str, store_cfg: dict) -> Optional[dict]:
    """Call Shopify Admin API by order name (#1234) using async HTTP."""
    auth = (store_cfg["api_key"], store_cfg["password"])
    url = f"https://{store_cfg['domain']}/admin/api/2023-07/orders.json"
    params = {"name": order_name}
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            r = await client.get(url, auth=auth, params=params)
            r.raise_for_status()
        except httpx.HTTPError:
            return None
    data = r.json()
    return data.get("orders", [{}])[0] if data.get("orders") else None


# ───────────────────────────────────────────────────────────────
# Core functions – Database logic
# ───────────────────────────────────────────────────────────────
ORDER_HEADER = [
    "Timestamp",
    "Order Name",
    "Customer Name",
    "Customer Phone",
    "Address",
    "Tags",
    "Fulfillment",
    "Order Status",
    "Store",
    "Delivery Status",
    "Notes",
    "Scheduled Time",
    "Scan Date",
    "Cash Amount",
    "Driver Fee",
    "Payout ID",
    "Status Log",
    "Comm Log",
]

PAYOUT_HEADER = [
    "Payout ID",
    "Date Created",
    "Orders",
    "Total Cash",
    "Total Fees",
    "Total Payout",
    "Status",
    "Date Paid",
]

EMPLOYEE_HEADER = ["Timestamp", "Employee", "Order Number", "Amount"]


async def order_exists(session: AsyncSession, driver_id: str, order_name: str) -> bool:
    result = await session.scalar(
        select(Order).where(
            Order.driver_id == driver_id, Order.order_name == order_name
        )
    )
    return result is not None


async def get_order_row(
    session: AsyncSession, driver_id: str, order_name: str
) -> Optional[Order]:
    return await session.scalar(
        select(Order).where(
            Order.driver_id == driver_id, Order.order_name == order_name
        )
    )


async def get_open_delivery_note(session: AsyncSession, driver_id: str) -> DeliveryNote:
    note = await session.scalar(
        select(DeliveryNote).where(
            DeliveryNote.driver_id == driver_id, DeliveryNote.status == "draft"
        )
    )
    if not note:
        note = DeliveryNote(driver_id=driver_id, status="draft")
        session.add(note)
        await session.flush()
    return note


async def update_verification_from_order(
    session: AsyncSession, order_name: str, driver_id: str, ts: dt.datetime
) -> None:
    """Ensure verification rows for the order have driver/scan time set."""
    rows = await session.execute(
        select(VerificationOrder).where(VerificationOrder.order_name == order_name)
    )
    updated = False
    for v in rows.scalars():
        if not v.driver_id:
            v.driver_id = driver_id
            updated = True
        if not v.scan_time:
            v.scan_time = ts
            updated = True
    if updated:
        await session.commit()


async def add_to_payout(
    session: AsyncSession,
    driver_id: str,
    order_name: str,
    cash_amount: float,
    driver_fee: float,
) -> str:
    """Create (or extend) an open payout row and write payout ID back to order."""
    payout = await session.scalar(
        select(Payout)
        .where(Payout.driver_id == driver_id, Payout.status != "paid")
        .order_by(Payout.date_created.desc())
    )
    if not payout:
        payout_id = f"PO-{dt.datetime.now().strftime('%Y%m%d-%H%M')}"
        payout = Payout(
            driver_id=driver_id,
            payout_id=payout_id,
            orders=order_name,
            total_cash=cash_amount,
            total_fees=driver_fee,
            total_payout=cash_amount - driver_fee,
            status="pending",
        )
        session.add(payout)
    else:
        orders_list = [o.strip() for o in (payout.orders or "").split(",") if o.strip()]
        orders_list.append(order_name)
        payout.orders = ", ".join(orders_list)
        payout.total_cash = (payout.total_cash or 0) + cash_amount
        payout.total_fees = (payout.total_fees or 0) + driver_fee
        payout.total_payout = payout.total_cash - payout.total_fees
        payout_id = payout.payout_id

    await session.flush()
    return payout_id


async def remove_from_payout(
    session: AsyncSession,
    payout_id: str,
    order_name: str,
    cash_amount: float,
    driver_fee: float,
) -> None:
    payout = await session.scalar(select(Payout).where(Payout.payout_id == payout_id))
    if not payout:
        return

    orders_list = [o.strip() for o in (payout.orders or "").split(",") if o.strip()]
    if order_name not in orders_list:
        return

    orders_list.remove(order_name)
    payout.orders = ", ".join(orders_list)
    payout.total_cash = (payout.total_cash or 0) - cash_amount
    payout.total_fees = (payout.total_fees or 0) - driver_fee
    payout.total_payout = payout.total_cash - payout.total_fees

    await session.flush()


# ───────────────────────────────────────────────────────────────
# FastAPI ROUTES
# ───────────────────────────────────────────────────────────────


async def get_driver(session, driver_id: str) -> Driver:
    result = await session.get(Driver, driver_id)
    if not result:
        raise HTTPException(status_code=400, detail="Invalid driver")
    return result


@app.get("/health", tags=["meta"])
def health():
    return {"status": "ok", "time": dt.datetime.utcnow().isoformat()}


# -------------------------------  SCAN  -------------------------------
@app.post("/scan", response_model=ScanResult, tags=["orders"])
async def scan(
    payload: ScanIn, driver: str = Query(..., description="driver1 / driver2 / …")
):
    async for session in get_session():
        await get_driver(session, driver)
        scan_day = dt.datetime.now().strftime("%Y-%m-%d")
        try:
            await sync_verification_orders(scan_day, session)
        except Exception:
            logger.exception("sync_verification_orders failed")
        barcode = payload.barcode.strip()
        order_number = "#" + "".join(filter(str.isdigit, barcode))

        if len(order_number) <= 1:
            raise HTTPException(status_code=400, detail="Invalid barcode")

        if await order_exists(session, driver, order_number):
            existing = await get_order_row(session, driver, order_number)
            return ScanResult(
                result="⚠️ Already scanned",
                order=order_number,
                tag=get_primary_display_tag(existing.tags),
                deliveryStatus=existing.delivery_status,
            )

        # --- Shopify look-up (unchanged) ----------------------------------
        window_start = dt.datetime.now(timezone.utc) - dt.timedelta(days=50)
        chosen_order, chosen_store_name = None, ""
        for store in SHOPIFY_STORES:
            order = await get_order_from_store(order_number, store)
            if order:
                created_at = dt.datetime.fromisoformat(
                    order["created_at"].replace("Z", "+00:00")
                )
                if created_at >= window_start and (
                    not chosen_order
                    or created_at
                    > dt.datetime.fromisoformat(
                        chosen_order["created_at"].replace("Z", "+00:00")
                    )
                ):
                    chosen_order, chosen_store_name = order, store["name"]

        tags = chosen_order.get("tags", "") if chosen_order else ""
        fulfillment = (
            chosen_order.get("fulfillment_status", "unfulfilled")
            if chosen_order
            else ""
        )
        order_status = (
            "closed" if (chosen_order and chosen_order.get("cancelled_at")) else "open"
        )
        customer_name = phone = address = ""
        cash_amount = 0.0
        result_msg = "❌ Not found"

        if chosen_order:
            result_msg = (
                "⚠️ Cancelled"
                if chosen_order.get("cancelled_at")
                else "❌ Unfulfilled" if fulfillment != "fulfilled" else "✅ OK"
            )
            cash_amount = float(
                chosen_order.get("total_outstanding")
                or chosen_order.get("total_price")
                or 0
            )
            if chosen_order.get("shipping_address"):
                sa = chosen_order["shipping_address"]
                customer_name = sa.get("name", "")
                phone = sa.get("phone", "") or chosen_order.get("phone", "")
                address = ", ".join(
                    filter(
                        None,
                        [
                            sa.get("address1"),
                            sa.get("address2"),
                            sa.get("city"),
                            sa.get("province"),
                        ],
                    )
                )

        # Try to supplement missing details from the Google Sheet when
        # Shopify didn't return them
        if not customer_name or not phone or not address:
            try:
                sheet_data = await asyncio.to_thread(
                    get_order_from_sheet, order_number
                )
            except Exception:
                sheet_data = None
            if sheet_data:
                customer_name = customer_name or sheet_data.get("customer_name", "")
                phone = phone or sheet_data.get("customer_phone", "")
                address = address or sheet_data.get("address", "")

        # As a final fallback, look for the order in the verification table
        # to populate any missing details
        if not customer_name or not phone or not address:
            vo = await session.scalar(
                select(VerificationOrder)
                .where(VerificationOrder.order_name == order_number)
                .order_by(VerificationOrder.id.desc())
            )
            if vo:
                customer_name = customer_name or vo.customer_name or ""
                phone = phone or vo.customer_phone or ""
                address = address or vo.address or ""

        now_ts = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        driver_fee = calculate_driver_fee(tags)

        order = Order(
            driver_id=driver,
            timestamp=dt.datetime.strptime(now_ts, "%Y-%m-%d %H:%M:%S"),
            order_name=order_number,
            customer_name=customer_name,
            customer_phone=phone,
            address=address,
            tags=tags,
            fulfillment=fulfillment,
            order_status=order_status,
            store=chosen_store_name,
            delivery_status="Dispatched",
            notes="",
            scheduled_time="",
            scan_date=scan_day,
            cash_amount=cash_amount,
            driver_fee=driver_fee,
            follow_log="",
        )
        session.add(order)
        await session.flush()

        note = await get_open_delivery_note(session, driver)
        session.add(
            DeliveryNoteItem(
                note_id=note.id,
                order_id=order.id,
                scanned_at=dt.datetime.utcnow(),
            )
        )
        await session.commit()
        # Update verification table with driver/scan time
        await update_verification_from_order(
            session, order_number, driver, order.timestamp
        )

        await cache_delete("orders", driver)
        await manager.broadcast(
            {
                "type": "new_order",
                "driver": driver,
                "order": order_number,
            }
        )

        return ScanResult(
            result=result_msg,
            order=order_number,
            tag=get_primary_display_tag(tags),
            deliveryStatus="Dispatched",
            noteId=note.id,
        )


# -----------------------  DELIVERY NOTES  ------------------------


@app.get("/notes", tags=["notes"])
async def list_notes(driver: str = Query(...), history: bool = Query(False)):
    async for session in get_session():
        await get_driver(session, driver)
        q = select(DeliveryNote).where(DeliveryNote.driver_id == driver)
        q = (
            q.where(DeliveryNote.status == "approved")
            if history
            else q.where(DeliveryNote.status == "draft")
        )
        q = q.order_by(DeliveryNote.created_at.desc())
        result = await session.execute(q)
        notes: list[dict] = []
        for n in result.scalars():
            item_rows = await session.execute(
                select(DeliveryNoteItem).where(DeliveryNoteItem.note_id == n.id)
            )
            items = item_rows.scalars().all()
            total_cash = 0.0
            for it in items:
                o = await session.get(Order, it.order_id)
                if o:
                    total_cash += o.cash_amount or 0
            notes.append(
                {
                    "id": n.id,
                    "createdAt": n.created_at.strftime("%Y-%m-%d %H:%M:%S"),
                    "parcels": len(items),
                    "totalCod": total_cash,
                    "status": n.status,
                }
            )
        return notes


@app.get("/notes/{note_id}", tags=["notes"])
async def get_note(note_id: int, driver: str = Query(...)):
    async for session in get_session():
        note = await session.get(DeliveryNote, note_id)
        if not note or note.driver_id != driver:
            raise HTTPException(status_code=404, detail="Note not found")
        item_rows = await session.execute(
            select(DeliveryNoteItem).where(DeliveryNoteItem.note_id == note_id)
        )
        items: list[dict] = []
        for it in item_rows.scalars():
            o = await session.get(Order, it.order_id)
            if o:
                items.append(
                    {"orderName": o.order_name, "cashAmount": o.cash_amount or 0}
                )
        return {
            "id": note.id,
            "createdAt": note.created_at.strftime("%Y-%m-%d %H:%M:%S"),
            "status": note.status,
            "items": items,
        }


@app.delete("/notes/{note_id}/items/{order_name}", tags=["notes"])
async def remove_note_item(note_id: int, order_name: str, driver: str = Query(...)):
    async for session in get_session():
        note = await session.get(DeliveryNote, note_id)
        if not note or note.driver_id != driver:
            raise HTTPException(status_code=404, detail="Note not found")
        if note.status != "draft":
            raise HTTPException(status_code=400, detail="Cannot modify approved note")

        order = await session.scalar(
            select(Order).where(
                Order.driver_id == driver, Order.order_name == order_name
            )
        )
        if not order:
            raise HTTPException(status_code=404, detail="Order not found")
        item = await session.scalar(
            select(DeliveryNoteItem).where(
                DeliveryNoteItem.note_id == note_id,
                DeliveryNoteItem.order_id == order.id,
            )
        )
        if not item:
            raise HTTPException(status_code=404, detail="Item not in note")

        await session.delete(item)
        await session.commit()

        await cache_delete("orders", driver)
        await manager.broadcast(
            {"type": "note_update", "driver": driver, "noteId": note_id}
        )
        return {"success": True}


@app.post("/notes/{note_id}/approve", tags=["notes"])
async def approve_note(note_id: int, driver: str = Query(...)):
    async for session in get_session():
        note = await session.get(DeliveryNote, note_id)
        if not note or note.driver_id != driver:
            raise HTTPException(status_code=404, detail="Note not found")
        if note.status != "draft":
            raise HTTPException(status_code=400, detail="Already approved")
        item_count = await session.scalar(
            select(func.count(DeliveryNoteItem.id)).where(
                DeliveryNoteItem.note_id == note_id
            )
        )
        if item_count == 0:
            raise HTTPException(status_code=400, detail="Cannot approve empty note")
        note.status = "approved"
        note.approved_at = dt.datetime.utcnow()
        await session.commit()
        await cache_delete("orders", driver)
        await manager.broadcast(
            {"type": "note_approved", "driver": driver, "noteId": note_id}
        )
        return {"success": True}


# -----------------------------  ORDERS  -------------------------------
@app.get("/orders", tags=["orders"])
async def list_active_orders(driver: str = Query(...)):
    cached = await cache_get("orders", driver)
    if cached is not None:
        return cached

    async for session in get_session():
        await get_driver(session, driver)
        result = await session.execute(
            select(Order)
            .outerjoin(DeliveryNoteItem, DeliveryNoteItem.order_id == Order.id)
            .outerjoin(DeliveryNote, DeliveryNote.id == DeliveryNoteItem.note_id)
            .where(
                Order.driver_id == driver,
                Order.delivery_status.notin_(COMPLETED_STATUSES),
                or_(
                    DeliveryNote.status == "approved",
                    DeliveryNote.id == None,
                ),
            )
        )
        rows = result.scalars().all()

        active = []
        for o in rows:
            active.append(
                {
                    "timestamp": o.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                    "orderName": o.order_name,
                    "customerName": o.customer_name,
                    "customerPhone": o.customer_phone,
                    "address": o.address,
                    "tags": o.tags,
                    "deliveryStatus": o.delivery_status or "Dispatched",
                    "notes": o.notes,
                    "driverNotes": o.driver_notes,
                    "scheduledTime": o.scheduled_time,
                    "scanDate": o.scan_date,
                    "cashAmount": o.cash_amount or 0,
                    "driverFee": o.driver_fee or 0,
                    "payoutId": o.payout_id,
                    "statusLog": o.status_log,
                    "commLog": o.comm_log,
                    "followLog": o.follow_log,
                }
            )

    def sort_key(o):
        if o["scheduledTime"]:
            try:
                return parse_timestamp(o["scheduledTime"])
            except Exception:
                pass
        return parse_timestamp(o["timestamp"])

    active.sort(key=sort_key)

    now = dt.datetime.now()
    for o in active:
        if o["scheduledTime"]:
            try:
                st = parse_timestamp(o["scheduledTime"])
                o["urgent"] = (st - now).total_seconds() <= 3600
            except Exception:
                o["urgent"] = False
        else:
            o["urgent"] = False

    await cache_set("orders", driver, active)
    return active


@app.get("/orders/archive", tags=["orders"])
async def list_archived_orders(driver: str = Query(...)):
    cached = await cache_get("archive", driver)
    if cached is not None:
        return cached

    async for session in get_session():
        await get_driver(session, driver)
        result = await session.execute(
            select(Order)
            .outerjoin(DeliveryNoteItem, DeliveryNoteItem.order_id == Order.id)
            .outerjoin(DeliveryNote, DeliveryNote.id == DeliveryNoteItem.note_id)
            .where(
                Order.driver_id == driver,
                Order.delivery_status.in_(COMPLETED_STATUSES),
                or_(
                    DeliveryNote.status == "approved",
                    DeliveryNote.id == None,
                ),
            )
            .order_by(Order.timestamp.desc())
        )
        rows = result.scalars().all()

        archived = []
        for o in rows:
            archived.append(
                {
                    "timestamp": o.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                    "orderName": o.order_name,
                    "customerName": o.customer_name,
                    "customerPhone": o.customer_phone,
                    "address": o.address,
                    "tags": o.tags,
                    "deliveryStatus": o.delivery_status or "Dispatched",
                    "notes": o.notes,
                    "driverNotes": o.driver_notes,
                    "scheduledTime": o.scheduled_time,
                    "scanDate": o.scan_date,
                    "cashAmount": o.cash_amount or 0,
                    "driverFee": o.driver_fee or 0,
                    "payoutId": o.payout_id,
                    "statusLog": o.status_log,
                    "commLog": o.comm_log,
                    "followLog": o.follow_log,
                }
            )

    await cache_set("archive", driver, archived)
    return archived


@app.get("/orders/followups", tags=["orders"])
async def list_followup_orders(driver: str = Query(...)):
    cached = await cache_get("followups", driver)
    if cached is not None:
        return cached

    async for session in get_session():
        await get_driver(session, driver)
        result = await session.execute(
            select(Order)
            .outerjoin(DeliveryNoteItem, DeliveryNoteItem.order_id == Order.id)
            .outerjoin(DeliveryNote, DeliveryNote.id == DeliveryNoteItem.note_id)
            .where(
                Order.driver_id == driver,
                Order.delivery_status.notin_(COMPLETED_STATUSES),
                or_(
                    DeliveryNote.status == "approved",
                    DeliveryNote.id == None,
                ),
            )
        )
        rows = result.scalars().all()

        followups = []
        # Use a naive timestamp to match values loaded from SQLite/PG
        now = dt.datetime.utcnow()
        for o in rows:
            last_update = o.timestamp
            if o.status_log:
                try:
                    ts_str = o.status_log.strip().split("|")[-1].split("@")[-1].strip()
                    last_update = parse_timestamp(ts_str)
                except Exception:
                    pass

            overdue = False
            if o.scheduled_time:
                try:
                    st = parse_timestamp(o.scheduled_time)
                    overdue = st <= now
                except Exception:
                    overdue = False

            if (
                overdue
                or (now - last_update).total_seconds() > 8 * 3600
                or o.delivery_status in ["Pas de réponse 3", "Rescheduled"]
            ):
                followups.append(
                    {
                        "timestamp": o.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                        "orderName": o.order_name,
                        "customerName": o.customer_name,
                        "customerPhone": o.customer_phone,
                        "address": o.address,
                        "tags": o.tags,
                        "deliveryStatus": o.delivery_status or "Dispatched",
                        "notes": o.notes,
                        "driverNotes": o.driver_notes,
                        "scheduledTime": o.scheduled_time,
                        "scanDate": o.scan_date,
                        "cashAmount": o.cash_amount or 0,
                        "driverFee": o.driver_fee or 0,
                        "payoutId": o.payout_id,
                        "statusLog": o.status_log,
                        "commLog": o.comm_log,
                        "followLog": o.follow_log,
                        "urgent": overdue,
                    }
                )

    await cache_set("followups", driver, followups)
    return followups


@app.put("/order/status", tags=["orders"])
async def update_order_status(
    payload: StatusUpdate, bg: BackgroundTasks, driver: str = Query(...)
):
    if payload.new_status and payload.new_status not in DELIVERY_STATUSES:
        raise HTTPException(status_code=400, detail="Invalid status")

    async for session in get_session():
        await get_driver(session, driver)
        order = await get_order_row(session, driver, payload.order_name)
        if not order:
            raise HTTPException(status_code=404, detail="Order not found")

        prev_status = order.delivery_status

        if payload.new_status:
            order.delivery_status = payload.new_status
            ts = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            order.status_log = (
                (order.status_log or "") + f" | {payload.new_status} @ {ts}"
            ).strip(" |")
        if payload.note is not None:
            order.notes = payload.note
        if payload.driver_note is not None:
            ts = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
            order.driver_notes = (
                (order.driver_notes or "") + f"{ts} - {payload.driver_note}\n"
            ).lstrip()
        if payload.scheduled_time is not None:
            order.scheduled_time = payload.scheduled_time
        if payload.cash_amount is not None:
            order.cash_amount = payload.cash_amount
        if payload.comm_log is not None:
            order.comm_log = payload.comm_log
        if payload.follow_log is not None:
            order.follow_log = payload.follow_log

        if payload.new_status == "Livré" and prev_status != "Livré":
            note = await session.scalar(
                select(DeliveryNote)
                .join(DeliveryNoteItem)
                .where(DeliveryNoteItem.order_id == order.id)
            )
            if not note or note.status == "approved":
                driver_fee = calculate_driver_fee(order.tags)
                cash_amt = payload.cash_amount or (order.cash_amount or 0)
                payout_id = await add_to_payout(
                    session, driver, payload.order_name, cash_amt, driver_fee
                )
                order.payout_id = payout_id
                order.driver_fee = driver_fee
        elif (
            payload.new_status
            and payload.new_status != "Livré"
            and prev_status == "Livré"
        ):
            driver_fee = order.driver_fee or 0
            cash_amt = (
                payload.cash_amount
                if payload.cash_amount is not None
                else (order.cash_amount or 0)
            )
            await remove_from_payout(
                session, order.payout_id, payload.order_name, cash_amt, driver_fee
            )
            order.payout_id = None

        await session.commit()

        await cache_delete("orders", driver)
        await cache_delete("payouts", driver)
        await manager.broadcast(
            {
                "type": "status_update",
                "driver": driver,
                "order": payload.order_name,
                "status": payload.new_status,
            }
        )
        return {"success": True}


# ----------------------------  PAYOUTS  -------------------------------
@app.get("/payouts", tags=["payouts"])
async def get_payouts(driver: str = Query(...)):
    cached = await cache_get("payouts", driver)
    if cached is not None:
        return cached

    async for session in get_session():
        await get_driver(session, driver)
        result = await session.execute(
            select(Payout)
            .where(Payout.driver_id == driver)
            .order_by(Payout.date_created.desc())
        )
        rows = result.scalars().all()
        payouts = []
        for p in rows:
            orders_list = [o.strip() for o in (p.orders or "").split(",") if o.strip()]
            order_details = []
            for name in orders_list:
                order = await session.scalar(
                    select(Order).where(
                        Order.driver_id == driver, Order.order_name == name
                    )
                )
                if order:
                    order_details.append(
                        {
                            "name": name,
                            "cashAmount": order.cash_amount or 0,
                            "driverFee": order.driver_fee or 0,
                        }
                    )
                else:
                    order_details.append(
                        {"name": name, "cashAmount": 0.0, "driverFee": 0.0}
                    )

            payouts.append(
                {
                    "payoutId": p.payout_id,
                    "dateCreated": p.date_created.strftime("%Y-%m-%d %H:%M:%S"),
                    "orders": p.orders,
                    "totalCash": p.total_cash or 0,
                    "totalFees": p.total_fees or 0,
                    "totalPayout": p.total_payout or 0,
                    "status": p.status or "pending",
                    "datePaid": (
                        p.date_paid.strftime("%Y-%m-%d %H:%M:%S") if p.date_paid else ""
                    ),
                    "orderDetails": order_details,
                }
            )

        await cache_set("payouts", driver, payouts)
        return payouts


@app.post("/payout/mark-paid/{payout_id}", tags=["payouts"])
async def mark_payout_paid(payout_id: str, driver: str = Query(...)):
    async for session in get_session():
        await get_driver(session, driver)
        payout = await session.scalar(
            select(Payout).where(
                Payout.driver_id == driver, Payout.payout_id == payout_id
            )
        )
        if not payout:
            raise HTTPException(status_code=404, detail="Payout not found")

        payout.status = "paid"
        payout.date_paid = dt.datetime.utcnow()
        await session.commit()

        await cache_delete("payouts", driver)
        await cache_delete("orders", driver)
        return {"success": True}


@app.put("/payout/{payout_id}", tags=["payouts"])
async def update_payout(
    payout_id: str, payload: PayoutUpdate, driver: str = Query(...)
):
    async for session in get_session():
        await get_driver(session, driver)
        payout = await session.scalar(
            select(Payout).where(
                Payout.driver_id == driver, Payout.payout_id == payout_id
            )
        )
        if not payout:
            raise HTTPException(status_code=404, detail="Payout not found")

        if payload.orders is not None:
            payout.orders = payload.orders
        if payload.total_cash is not None:
            payout.total_cash = payload.total_cash
        if payload.total_fees is not None:
            payout.total_fees = payload.total_fees
        if payload.total_payout is not None:
            payout.total_payout = payload.total_payout
        if payload.date_created:
            try:
                payout.date_created = dt.datetime.fromisoformat(payload.date_created)
            except Exception:
                pass
        if payload.total_cash is not None or payload.total_fees is not None:
            payout.total_payout = (payout.total_cash or 0) - (payout.total_fees or 0)

        await session.commit()
        await cache_delete("payouts", driver)
        return {"success": True}


# ----------------------------  STATS  -------------------------------
async def _compute_stats(
    session: AsyncSession,
    driver: str,
    days: int | None = None,
    start: str | None = None,
    end: str | None = None,
) -> dict:
    await get_driver(session, driver)
    q = select(Order).where(Order.driver_id == driver)

    if start:
        try:
            start_date = dt.datetime.strptime(start, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid start date")
    elif days and days > 0:
        start_date = dt.datetime.now().date() - dt.timedelta(days=days - 1)
    else:
        start_date = None

    if end:
        try:
            end_date = dt.datetime.strptime(end, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid end date")
    else:
        end_date = None

    if start_date:
        q = q.where(Order.scan_date >= start_date.strftime("%Y-%m-%d"))
    if end_date:
        q = q.where(Order.scan_date <= end_date.strftime("%Y-%m-%d"))

    result = await session.execute(q)
    rows = result.scalars().all()

    total = delivered = returned = 0
    collect = fees = canceled_amount = 0.0
    for o in rows:
        sd = None
        try:
            sd = (
                dt.datetime.strptime(o.scan_date, "%Y-%m-%d").date()
                if o.scan_date
                else None
            )
        except Exception:
            sd = None
        if start_date and (not sd or sd < start_date):
            continue
        if end_date and (not sd or sd > end_date):
            continue
        total += 1
        status = o.delivery_status
        cash = o.cash_amount or 0
        fee = o.driver_fee or 0
        if status == "Livré":
            delivered += 1
            collect += cash
            fees += fee
        elif status in ("Returned", "Annulé", "Refusé"):
            returned += 1
            canceled_amount += cash

    rate = (delivered / total * 100) if total else 0
    return {
        "totalOrders": total,
        "delivered": delivered,
        "returned": returned,
        "totalCollect": collect,
        "totalFees": fees,
        "deliveryRate": rate,
        "canceledAmount": canceled_amount,
    }


@app.get("/stats", tags=["stats"])
async def get_stats(
    driver: str = Query(...),
    days: int | None = Query(None),
    start: str | None = Query(None),
    end: str | None = Query(None),
):
    async for session in get_session():
        stats = await _compute_stats(session, driver, days, start, end)
        return stats


@app.get("/admin/stats", tags=["admin"])
async def admin_stats(
    days: int | None = Query(None),
    start: str | None = Query(None),
    end: str | None = Query(None),
):
    async for session in get_session():
        drivers = await load_drivers(session)
        result = {}
        for d in drivers.keys():
            result[d] = await _compute_stats(session, d, days, start, end)
        return result


# -------------------------------------------------------------------
# Daily trend data for all drivers
# -------------------------------------------------------------------
@app.get("/admin/trends", tags=["admin"])
async def admin_trends(
    start: str | None = Query(None),
    end: str | None = Query(None),
    days: int | None = Query(None),
):
    """Return delivered count per day across all drivers."""
    if start:
        try:
            start_date = dt.datetime.strptime(start, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid start date")
    elif days and days > 0:
        start_date = dt.datetime.now().date() - dt.timedelta(days=days - 1)
    else:
        start_date = None

    if end:
        try:
            end_date = dt.datetime.strptime(end, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid end date")
    else:
        end_date = dt.datetime.now().date()

    async for session in get_session():
        drivers = await load_drivers(session)
        counts: dict[dt.date, int] = {}
        for driver in drivers.keys():
            q = select(Order).where(
                Order.driver_id == driver, Order.delivery_status == "Livré"
            )
            if start_date:
                q = q.where(Order.scan_date >= start_date.strftime("%Y-%m-%d"))
            if end_date:
                q = q.where(Order.scan_date <= end_date.strftime("%Y-%m-%d"))
            result = await session.execute(q)
            for o in result.scalars():
                try:
                    sd = (
                        dt.datetime.strptime(o.scan_date, "%Y-%m-%d").date()
                        if o.scan_date
                        else None
                    )
                except Exception:
                    continue
                if not sd:
                    continue
                counts[sd] = counts.get(sd, 0) + 1

        days_sorted = sorted(counts.keys())
        return [
            {"date": d.strftime("%Y-%m-%d"), "delivered": counts[d]}
            for d in days_sorted
        ]


@app.get("/admin/search", tags=["admin"])
async def admin_search(q: str = Query(...)):
    """Search orders across all drivers by order name or phone."""
    q_lower = q.lower()
    results: list[dict] = []
    async for session in get_session():
        drivers = await load_drivers(session)
        for driver in drivers.keys():
            result = await session.execute(
                select(Order).where(
                    Order.driver_id == driver,
                    or_(
                        Order.order_name.ilike(f"%{q_lower}%"),
                        Order.customer_phone.ilike(f"%{q_lower}%"),
                    ),
                )
            )
            for o in result.scalars():
                results.append(
                    {
                        "driver": driver,
                        "orderName": o.order_name,
                        "customerName": o.customer_name,
                        "customerPhone": o.customer_phone,
                        "deliveryStatus": o.delivery_status or "Dispatched",
                        "cashAmount": o.cash_amount or 0,
                        "address": o.address,
                        "scheduledTime": o.scheduled_time,
                        "notes": o.notes,
                        "followLog": o.follow_log,
                        "timestamp": o.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                    }
                )
        return results


@app.get("/admin/verify", tags=["admin"])
async def admin_verify(
    date: str | None = Query(None),
    start: str | None = Query(None),
    end: str | None = Query(None),
    q: str | None = Query(None),
):
    """Return verification orders for a single day or date range."""
    logger.info(
        "admin_verify params date=%s start=%s end=%s q=%s",
        date,
        start,
        end,
        q,
    )
    async for session in get_session():
        dates_to_sync: list[str] = []
        if start or end:
            s = dt.datetime.strptime(start or end or "", "%Y-%m-%d").date()
            e = dt.datetime.strptime(end or start or "", "%Y-%m-%d").date()
            if e < s:
                s, e = e, s
            current = s
            while current <= e:
                dates_to_sync.append(current.strftime("%Y-%m-%d"))
                current += dt.timedelta(days=1)
        elif date:
            dates_to_sync.append(date)
        else:
            raise HTTPException(status_code=400, detail="Date or range required")

        for d in dates_to_sync:
            await sync_verification_orders(d, session)

        q_filter = []
        if q:
            q_lower = f"%{q.lower()}%"
            q_filter.append(
                or_(
                    VerificationOrder.order_name.ilike(q_lower),
                    VerificationOrder.customer_name.ilike(q_lower),
                )
            )
        stmt = select(VerificationOrder)
        if start or end:
            stmt = stmt.where(
                VerificationOrder.order_date >= (start or end),
                VerificationOrder.order_date <= (end or start),
                *q_filter,
            ).order_by(VerificationOrder.id.desc())
        else:
            stmt = stmt.where(
                VerificationOrder.order_date == date,
                *q_filter,
            ).order_by(VerificationOrder.id.desc())
        result = await session.execute(stmt)
        rows_map: dict[str, dict] = {}
        for v in result.scalars():
            order = await session.scalar(
                select(Order)
                .where(Order.order_name == v.order_name)
                .order_by(Order.timestamp.desc())
            )
            if order:
                if not v.driver_id:
                    v.driver_id = order.driver_id
                if not v.scan_time:
                    v.scan_time = order.timestamp
            row = {
                "id": v.id,
                "orderName": v.order_name,
                "customerName": v.customer_name,
                "customerPhone": v.customer_phone,
                "address": v.address,
                "codTotal": v.cod_total,
                "city": v.city,
                "driver": v.driver_id or "",
                "scanTime": v.scan_time.strftime("%Y-%m-%d %H:%M:%S") if v.scan_time else "",
                "status": order.delivery_status if order else "",
                "verified": bool(v.driver_id and v.scan_time),
            }
            if v.order_name not in rows_map:
                rows_map[v.order_name] = row
        await session.commit()
        rows = list(rows_map.values())
        total = len(rows)
        verified = sum(1 for r in rows if r["verified"])
        missing = total - verified
        logger.info(
            "admin_verify result total=%d verified=%d missing=%d",
            total,
            verified,
            missing,
        )
        return {"rows": rows, "total": total, "verified": verified, "missing": missing}


@app.put("/admin/verify/{item_id}", tags=["admin"])
async def admin_verify_update(item_id: int, payload: VerificationUpdate):
    logger.info(
        "admin_verify_update id=%s driver_id=%s scan_time=%s",
        item_id,
        payload.driver_id,
        payload.scan_time,
    )
    async for session in get_session():
        item = await session.get(VerificationOrder, item_id)
        if not item:
            raise HTTPException(status_code=404, detail="Not found")
        if payload.driver_id is not None:
            item.driver_id = payload.driver_id or None
        if payload.scan_time is not None:
            item.scan_time = parse_timestamp(payload.scan_time) if payload.scan_time else None
        await session.commit()
        logger.info("admin_verify_update succeeded for id=%s", item_id)
        return {"success": True}


@app.post("/admin/verify/sync", tags=["admin"])
async def admin_verify_sync(date: str = Query(...)):
    """Manually import verification orders from the Google Sheet."""
    async for session in get_session():
        await sync_verification_orders(date, session)
        return {"success": True}


@app.get("/admin/notes", tags=["admin"])
async def admin_list_notes(driver: str | None = Query(None)):
    async for session in get_session():
        q = select(DeliveryNote).order_by(DeliveryNote.created_at.desc())
        if driver:
            q = q.where(DeliveryNote.driver_id == driver)
        result = await session.execute(q)
        notes = []
        for n in result.scalars():
            item_rows = await session.execute(
                select(DeliveryNoteItem).where(DeliveryNoteItem.note_id == n.id)
            )
            items = []
            summary = {"delivered": 0, "cancelled": 0, "returned": 0}
            for it in item_rows.scalars():
                o = await session.get(Order, it.order_id)
                if not o:
                    continue
                items.append({"orderName": o.order_name, "status": o.delivery_status})
                if o.delivery_status == "Livré":
                    summary["delivered"] += 1
                elif o.delivery_status in ("Annulé", "Refusé"):
                    summary["cancelled"] += 1
                elif o.delivery_status == "Returned":
                    summary["returned"] += 1
            notes.append(
                {
                    "id": n.id,
                    "driver": n.driver_id,
                    "createdAt": n.created_at.strftime("%Y-%m-%d %H:%M:%S"),
                    "status": n.status,
                    "summary": summary,
                    "items": items,
                }
            )
        return notes


# ---------------------------- EMPLOYEES -------------------------------
@app.post("/employee/log", tags=["employees"])
async def employee_log(entry: EmployeeLog):
    """Append an employee action row to the database."""
    async for session in get_session():
        log = EmployeeLog(
            timestamp=dt.datetime.utcnow(),
            employee=entry.employee,
            order=entry.order,
            amount=entry.amount,
        )
        session.add(log)
        await session.commit()
        return {"success": True}


@app.get("/employee/logs", tags=["employees"])
async def employee_logs():
    """Return all employee log rows as a list of dictionaries."""
    async for session in get_session():
        result = await session.execute(
            select(EmployeeLog).order_by(EmployeeLog.timestamp.desc())
        )
        logs = []
        for r in result.scalars():
            logs.append(
                {
                    "timestamp": r.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                    "employee": r.employee,
                    "order": r.order,
                    "amount": r.amount,
                }
            )
        return logs
