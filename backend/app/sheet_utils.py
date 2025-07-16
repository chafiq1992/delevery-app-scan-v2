import os
import gspread
import base64
import tempfile
from typing import Optional, Dict, Tuple


def _resolve_credentials() -> Tuple[Optional[str], Optional[str]]:
    """Return path to credentials file and a temporary file to clean up."""
    creds = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    if creds:
        return creds, None
    b64 = os.getenv("GOOGLE_CREDENTIALS_B64")
    if not b64:
        return None, None
    try:
        data = base64.b64decode(b64)
    except Exception:
        return None, None
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".json")
    tmp.write(data)
    tmp.flush()
    tmp.close()
    return tmp.name, tmp.name


def get_order_from_sheet(order_name: str) -> Optional[Dict[str, str]]:
    """Lookup order details in the Google Sheet."""
    creds, tmp = _resolve_credentials()
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
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except Exception:
                pass
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
