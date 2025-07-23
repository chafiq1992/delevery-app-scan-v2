import os
import httpx
import asyncio
import importlib
import sys
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

if os.path.exists("test.db"):
    os.remove("test.db")

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///test.db"

from app import main as app_main
from app.db import AsyncSessionLocal, VerificationOrder

importlib.reload(app_main)

async def dummy_sync(date, session):
    pass

class DummyResponse:
    def __init__(self, payload):
        self._payload = payload
    def raise_for_status(self):
        pass
    def json(self):
        return self._payload

async def fake_get(self, url, auth=None, params=None):
    # Return Shopify order without shipping address
    return DummyResponse({"orders": [{"id": 1, "name": params["name"], "created_at": "2024-01-01T00:00:00Z", "fulfillment_status": "fulfilled", "tags": ""}]})

def fake_sheet(order_name: str):
    return None

async def create_verification_row():
    async with AsyncSessionLocal() as session:
        vo = VerificationOrder(
            order_date="2024-01-01",
            order_name="#1111",
            customer_name="Verif Name",
            customer_phone="555-222",
            address="Verif Address",
        )
        session.add(vo)
        await session.commit()


def test_scan_uses_verification_table(monkeypatch):
    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    monkeypatch.setattr(app_main, "get_order_from_sheet", fake_sheet)
    monkeypatch.setattr(app_main, "sync_verification_orders", dummy_sync)

    if os.path.exists("test.db"):
        os.remove("test.db")

    client = TestClient(app_main.app)
    asyncio.run(app_main.init_db())
    asyncio.run(create_verification_row())

    resp = client.post("/scan?driver=abderrehman", json={"barcode": "#1111"})
    assert resp.status_code == 200

    resp = client.get("/orders?driver=abderrehman")
    assert resp.status_code == 200
    assert resp.json() == []

    notes = client.get("/notes?driver=abderrehman").json()
    assert len(notes) == 1
    note_id = notes[0]["id"]
    note = client.get(f"/notes/{note_id}?driver=abderrehman").json()
    assert note["items"][0]["orderName"] == "#1111"

