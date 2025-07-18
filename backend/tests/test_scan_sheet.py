import os
import httpx
import pytest
from fastapi.testclient import TestClient
import asyncio

# Ensure an in-memory database for tests
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///test.db"

from app import main as app_main

class DummyResponse:
    def __init__(self, payload):
        self._payload = payload
    def raise_for_status(self):
        pass
    def json(self):
        return self._payload

async def fake_get(self, url, auth=None, params=None):
    # Return Shopify order without shipping address to trigger sheet fallback
    return DummyResponse({"orders": [{"id": 1, "name": params["name"], "created_at": "2024-01-01T00:00:00Z", "fulfillment_status": "fulfilled", "tags": ""}]})

def fake_sheet(order_name: str):
    return {
        "customer_name": "Sheet Name",
        "customer_phone": "555-123",
        "address": "Sheet Address",
    }


def test_scan_uses_sheet_when_shopify_incomplete(monkeypatch):
    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    monkeypatch.setattr(app_main, "get_order_from_sheet", fake_sheet)
    client = TestClient(app_main.app)
    asyncio.run(app_main.init_db())

    resp = client.post("/scan?driver=abderrehman", json={"barcode": "#1111"})
    assert resp.status_code == 200

    resp = client.get("/orders?driver=abderrehman")
    assert resp.status_code == 200
    order = resp.json()[0]
    assert order["customerName"] == "Sheet Name"
    assert order["customerPhone"] == "555-123"
    assert order["address"] == "Sheet Address"
