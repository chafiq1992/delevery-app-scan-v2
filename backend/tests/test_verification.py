import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import types
import base64
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

def make_gspread_stub(rows, calls):
    def service_account(filename):
        calls.append(("file", filename))
        return DummyClient(rows)

    def service_account_from_dict(info):
        calls.append(("dict", info))
        return DummyClient(rows)

    return types.SimpleNamespace(
        service_account=service_account,
        service_account_from_dict=service_account_from_dict,
    )


def test_verify_endpoint(monkeypatch):
    rows = [
        ["Date","Order","Customer","Phone","Address","City","COD"],
        ["2024-01-01","#111","Alice","555","Addr","Town","100"],
    ]
    calls = []
    sys.modules['gspread'] = make_gspread_stub(rows, calls)
    import app.sheet_utils as sheet_utils
    import importlib
    importlib.reload(sheet_utils)
    cred_json = '{"dummy": "yes"}'
    b64 = base64.b64encode(cred_json.encode()).decode()
    monkeypatch.setenv("GOOGLE_CREDENTIALS_B64", b64)
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
    assert calls[0] == ("dict", {"dummy": "yes"})


def test_verify_endpoint_defaults_date(monkeypatch):
    rows = [
        ["Order", "Customer", "Phone", "Address", "City", "COD"],
        ["#222", "Bob", "555", "Addr", "Town", "200"],
    ]
    calls = []
    sys.modules['gspread'] = make_gspread_stub(rows, calls)
    import app.sheet_utils as sheet_utils
    import importlib
    importlib.reload(sheet_utils)
    cred_json = '{"dummy": "yes"}'
    b64 = base64.b64encode(cred_json.encode()).decode()
    monkeypatch.setenv("GOOGLE_CREDENTIALS_B64", b64)
    monkeypatch.setenv("VERIFICATION_SHEET_ID", "dummy")

    from app import main as app_main
    client = TestClient(app_main.app)
    asyncio.run(app_main.init_db())

    resp = client.get("/admin/verify?date=2024-01-02")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["rows"][0]["orderName"] == "#222"
