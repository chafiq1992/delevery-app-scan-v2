import os
import asyncio
import base64
import json
from datetime import datetime

import gspread
from backend.app.db import Order, AsyncSessionLocal


async def insert_orders(rows: list[dict], driver_id: str) -> None:
    async with AsyncSessionLocal() as session:
        for data in rows:
            kwargs = {}
            for column in Order.__table__.columns.keys():
                if column == "id":
                    continue
                if column == "driver_id":
                    kwargs[column] = data.get(column, driver_id)
                elif column == "timestamp":
                    value = data.get(column)
                    if value:
                        try:
                            value = datetime.fromisoformat(value)
                        except Exception:
                            try:
                                value = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
                            except Exception:
                                value = datetime.utcnow()
                    else:
                        value = datetime.utcnow()
                    kwargs[column] = value
                elif column in {"cash_amount", "driver_fee"}:
                    value = data.get(column)
                    kwargs[column] = float(value) if value not in (None, "") else None
                else:
                    kwargs[column] = data.get(column)
            order = Order(**kwargs)
            session.add(order)
        await session.commit()


def validate_columns(header: list[str]) -> None:
    expected = [c for c in Order.__table__.columns.keys() if c != "id"]
    missing = [c for c in expected if c not in header]
    extra = [c for c in header if c not in expected]
    if missing or extra:
        raise ValueError(
            f"Column mismatch. Missing: {missing or 'none'}, Extra: {extra or 'none'}"
        )


def load_sheet() -> list[dict]:
    creds_b64 = os.environ["GOOGLE_CREDENTIALS_B64"]
    identifier = os.environ["SHEET_ID"]
    creds_json = base64.b64decode(creds_b64).decode()
    creds_info = json.loads(creds_json)
    gc = gspread.service_account_from_dict(creds_info)
    try:
        sheet = gc.open_by_key(identifier)
    except Exception:
        sheet = gc.open(identifier)
    ws = sheet.sheet1
    values = ws.get_all_values()
    if not values:
        return []
    header = [h.strip() for h in values[0]]
    validate_columns(header)
    rows = []
    for row in values[1:]:
        rows.append(dict(zip(header, row)))
    return rows


async def main() -> None:
    driver_id = os.getenv("DRIVER_ID", "abderrehman")
    rows = load_sheet()
    await insert_orders(rows, driver_id)


if __name__ == "__main__":
    asyncio.run(main())
