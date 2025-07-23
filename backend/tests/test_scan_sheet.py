import os
import pytest
from fastapi.testclient import TestClient
import asyncio
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import importlib

if os.path.exists("test.db"):
    os.remove("test.db")

# Ensure an in-memory database for tests
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///test.db"

from app import main as app_main
importlib.reload(app_main)

async def dummy_sync(date, session):
    pass


def fake_sheet(order_name: str):
    return {
        "customer_name": "Sheet Name",
        "customer_phone": "555-123",
        "address": "Sheet Address",
    }


def test_scan_uses_sheet_data(monkeypatch):
    monkeypatch.setattr(app_main, "get_order_from_sheet", fake_sheet)
    monkeypatch.setattr(app_main, "sync_verification_orders", dummy_sync)
    client = TestClient(app_main.app)
    asyncio.run(app_main.init_db())

    resp = client.post("/scan?driver=abderrehman", json={"barcode": "#1111"})
    assert resp.status_code == 200

    resp = client.get("/orders?driver=abderrehman")
    assert resp.status_code == 200
    assert resp.json() == []  # order hidden until note approved

    notes = client.get("/notes?driver=abderrehman").json()
    assert len(notes) == 1
    note_id = notes[0]["id"]
    note = client.get(f"/notes/{note_id}?driver=abderrehman").json()
    assert note["items"][0]["orderName"] == "#1111"
    assert note["items"][0]["cashAmount"] >= 0
