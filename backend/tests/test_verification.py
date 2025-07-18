import os
import sys
import types
from fastapi.testclient import TestClient
import asyncio

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///test.db"


class DummyWorksheet:
    def __init__(self, rows):
        self._rows = rows
    def get_all_values(self):
        return self._rows
class DummySheet:
    def __init__(self, rows):
        self.sheet1 = DummyWorksheet(rows)
class DummyClient:
    def __init__(self, rows):
        self._rows = rows
    def open_by_key(self, key):
        return DummySheet(self._rows)

def make_gspread_stub(rows):
    def service_account(filename):
        return DummyClient(rows)
    return types.SimpleNamespace(service_account=service_account)


def test_verify_endpoint(monkeypatch):
    rows = [
        ["Date","Order","Customer","Phone","Address","City","COD"],
        ["2024-01-01","#111","Alice","555","Addr","Town","100"],
    ]
    sys.modules['gspread'] = make_gspread_stub(rows)
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "creds.json")
    monkeypatch.setenv("VERIFICATION_SHEET_ID", "dummy")

    from app import main as app_main
    client = TestClient(app_main.app)
    asyncio.run(app_main.init_db())

    resp = client.get("/admin/verify?date=2024-01-01")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    row = data["rows"][0]
    assert row["orderName"] == "#111"
    assert row["verified"] is False

    resp = client.put(f"/admin/verify/{row['id']}", json={"driver_id": "abderrehman"})
    assert resp.status_code == 200

    resp = client.get("/admin/verify?date=2024-01-01")
    assert resp.json()["rows"][0]["driver"] == "abderrehman"
