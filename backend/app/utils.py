import datetime as dt
from typing import Optional
import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import (
    Order,
    Payout,
    DeliveryNote,
    DeliveryNoteItem,
    VerificationOrder,
)

NORMAL_DELIVERY_FEE = 20
EXCHANGE_DELIVERY_FEE = 10


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


def parse_timestamp(val: str) -> dt.datetime:
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


def serialize_order(order: Order) -> dict:
    status = order.delivery_status or "Dispatched"
    pending = bool(order.return_pending) and status in ("Returned", "Annulé", "Refusé")
    display_status = "Pending Return" if pending else status
    return {
        "timestamp": order.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
        "orderName": order.order_name,
        "customerName": order.customer_name,
        "customerPhone": order.customer_phone,
        "address": order.address,
        "tags": order.tags,
        "deliveryStatus": display_status,
        "notes": order.notes,
        "driverNotes": order.driver_notes,
        "scheduledTime": order.scheduled_time,
        "scanDate": order.scan_date,
        "cashAmount": order.cash_amount or 0,
        "driverFee": order.driver_fee or 0,
        "payoutId": order.payout_id,
        "statusLog": order.status_log,
        "commLog": order.comm_log,
        "followLog": order.follow_log,
        "returnPending": pending,
    }


async def get_order_from_store(order_name: str, store_cfg: dict) -> Optional[dict]:
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


async def order_exists(session: AsyncSession, driver_id: str, order_name: str) -> bool:
    result = await session.scalar(
        select(Order).where(Order.driver_id == driver_id, Order.order_name == order_name)
    )
    return result is not None


async def get_order_row(
    session: AsyncSession, driver_id: str, order_name: str
) -> Optional[Order]:
    return await session.scalar(
        select(Order).where(Order.driver_id == driver_id, Order.order_name == order_name)
    )


async def get_open_delivery_note(session: AsyncSession, driver_id: str) -> DeliveryNote:
    note = await session.scalar(
        select(DeliveryNote).where(DeliveryNote.driver_id == driver_id, DeliveryNote.status == "draft")
    )
    if not note:
        note = DeliveryNote(driver_id=driver_id, status="draft")
        session.add(note)
        await session.flush()
    return note


async def update_verification_from_order(
    session: AsyncSession, order_name: str, driver_id: str, ts: dt.datetime
) -> None:
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


async def sync_order_paid_status(session: AsyncSession, order: Order) -> None:
    if order.delivery_status == "Paid":
        return

    payout = None
    if order.payout_id:
        payout = await session.scalar(select(Payout).where(Payout.payout_id == order.payout_id))
    if not payout:
        payout = await session.scalar(
            select(Payout)
            .where(
                Payout.status == "paid",
                Payout.driver_id == order.driver_id,
                Payout.orders.ilike(f"%{order.order_name}%"),
            )
        )
        if payout:
            order.payout_id = order.payout_id or payout.payout_id

    if payout and payout.status == "paid":
        order.delivery_status = "Paid"
        ts = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        order.status_log = ((order.status_log or "") + f" | Paid @ {ts}").strip(" |")
        await session.flush()
