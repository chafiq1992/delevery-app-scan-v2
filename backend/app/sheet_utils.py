import os
import json
import base64
import tempfile
import gspread
from typing import Optional, Dict, List, Any
import logging

logger = logging.getLogger(__name__)


def _get_gspread_client() -> Optional[Any]:
    """Return a gspread client using either base64 credentials or a file."""
    creds_b64 = os.getenv("GOOGLE_CREDENTIALS_B64")
    creds_file = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    if creds_b64:
        try:
            decoded = base64.b64decode(creds_b64).decode()
            info = json.loads(decoded)
            try:
                return gspread.service_account_from_dict(info)
            except Exception:
                with tempfile.NamedTemporaryFile(delete=False, mode="w") as tmp:
                    tmp.write(decoded)
                    tmp.flush()
                    return gspread.service_account(filename=tmp.name)
        except Exception:
            return None
    if creds_file:
        try:
            return gspread.service_account(filename=creds_file)
        except Exception:
            return None
    return None


def get_order_from_sheet(order_name: str) -> Optional[Dict[str, str]]:
    """Lookup order details in the Google Sheet."""
    sheet_id = os.getenv("SHEET_ID")
    gc = _get_gspread_client()
    if not gc or not sheet_id:
        logger.warning("Missing Google credentials or sheet ID")
        return None
    try:
        sh = gc.open_by_key(sheet_id)
        ws = sh.sheet1
        rows = ws.get_all_values()
    except Exception as e:
        logger.exception("Error reading Google Sheet: %s", e)
        return None
    logger.info("Fetched %d rows from sheet", len(rows))
    if not rows:
        return None
    header = [h.strip().lower() for h in rows[0]]
    def find_idx(names):
        for idx, h in enumerate(header):
            norm = h.replace(" ", "")
            for name in names:
                if norm == name.replace(" ", ""):
                    return idx
            if any(n in norm for n in names):
                return idx
        return None
    order_idx = find_idx(["ordername", "ordernumber"])
    name_idx = find_idx(["customername", "name"])
    phone_idx = find_idx(["customerphone", "phone", "telephone"])
    address_idx = find_idx(["address", "customeraddress"])
    if order_idx is None:
        return None
    query_clean = order_name.lstrip("#").strip()
    for row in rows[1:]:
        if len(row) <= order_idx:
            continue
        row_value = row[order_idx].lstrip("#").strip()
        if row_value == query_clean:
            def get_cell(idx):
                return row[idx].strip() if idx is not None and idx < len(row) else ""
            return {
                "customer_name": get_cell(name_idx),
                "customer_phone": get_cell(phone_idx),
                "address": get_cell(address_idx),
            }
    return None


def load_sheet_orders() -> List[Dict[str, str]]:
    """Return all orders from the verification Google Sheet."""
    sheet_id = os.getenv("VERIFICATION_SHEET_ID") or os.getenv("SHEET_ID")
    gc = _get_gspread_client()
    if not gc or not sheet_id:
        logger.warning("Missing Google credentials or sheet ID")
        return []
    try:
        sh = gc.open_by_key(sheet_id)
        ws = sh.sheet1
        rows = ws.get_all_values()
    except Exception as e:
        logger.exception("Error reading Google Sheet: %s", e)
        return []
    logger.info("Fetched %d rows from sheet", len(rows))
    if not rows:
        return []
    header = [h.strip().lower() for h in rows[0]]
    def idx(name:str):
        for i,h in enumerate(header):
            if name in h.replace(" ",""):
                return i
        return None
    indices = {
        "date": idx("date"),
        "order": idx("order"),
        "name": idx("customer"),
        "phone": idx("phone"),
        "address": idx("address"),
        "city": idx("city"),
        "cod": idx("cod"),
    }
    orders = []
    for row in rows[1:]:
        order = {}
        if indices["order"] is None or len(row) <= indices["order"]:
            continue
        order["order_name"] = row[indices["order"]].strip()
        if indices["date"] is not None and len(row) > indices["date"]:
            value = row[indices["date"]].strip()
            order["order_date"] = value or None
        else:
            order["order_date"] = None
        order["customer_name"] = row[indices["name"]].strip() if indices["name"] is not None and len(row)>indices["name"] else ""
        order["customer_phone"] = row[indices["phone"]].strip() if indices["phone"] is not None and len(row)>indices["phone"] else ""
        order["address"] = row[indices["address"]].strip() if indices["address"] is not None and len(row)>indices["address"] else ""
        order["city"] = row[indices["city"]].strip() if indices["city"] is not None and len(row)>indices["city"] else ""
        order["cod_total"] = row[indices["cod"]].strip() if indices["cod"] is not None and len(row)>indices["cod"] else ""
        orders.append(order)
    return orders
