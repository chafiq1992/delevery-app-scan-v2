import os
import asyncio
import importlib
import sys
import datetime as dt
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

if os.path.exists("test.db"):
    os.remove("test.db")

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///test.db"

from app import main as app_main
importlib.reload(app_main)


def test_scan_triggers_sheet_sync(monkeypatch):
    calls = []
    async def dummy_sync(date, session):
        calls.append(date)
    monkeypatch.setattr(app_main, "get_order_from_sheet", lambda x: None)
    monkeypatch.setattr(app_main, "sync_verification_orders", dummy_sync)
    client = TestClient(app_main.app)
    asyncio.run(app_main.init_db())

    resp = client.post("/scan?driver=abderrehman", json={"barcode": "#1111"})
    assert resp.status_code == 200
    assert calls and calls[0] == dt.datetime.now().strftime("%Y-%m-%d")
