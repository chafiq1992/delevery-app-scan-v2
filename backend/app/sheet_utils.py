import os
import gspread
from typing import Optional, Dict


def get_order_from_sheet(order_name: str) -> Optional[Dict[str, str]]:
    """Lookup order details in the Google Sheet."""
    creds = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    sheet_id = os.getenv("SHEET_ID")
    if not creds or not sheet_id:
        return None
    try:
        gc = gspread.service_account(filename=creds)
        sh = gc.open_by_key(sheet_id)
        ws = sh.sheet1
        rows = ws.get_all_values()
    except Exception:
        return None
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
    for row in rows[1:]:
        if len(row) <= order_idx:
            continue
        if row[order_idx].strip() == order_name:
            def get_cell(idx):
                return row[idx].strip() if idx is not None and idx < len(row) else ""
            return {
                "customer_name": get_cell(name_idx),
                "customer_phone": get_cell(phone_idx),
                "address": get_cell(address_idx),
            }
    return None
