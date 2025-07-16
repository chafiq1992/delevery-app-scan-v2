import os
import types
import sys
import importlib

# Create a dummy gspread module with minimal functionality
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
    mod = types.SimpleNamespace(service_account=service_account)
    return mod


def test_lookup_strips_hash(monkeypatch):
    rows = [
        ["Order Number", "Customer Name"],
        ["#1234", "Alice"],
        ["5678", "Bob"],
    ]
    sys.modules['gspread'] = make_gspread_stub(rows)
    import app.sheet_utils as sheet_utils
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "creds.json")
    monkeypatch.setenv("SHEET_ID", "dummy")

    res1 = sheet_utils.get_order_from_sheet("1234")
    assert res1 == {"customer_name": "Alice", "customer_phone": "", "address": ""}

    res2 = sheet_utils.get_order_from_sheet("#5678")
    assert res2["customer_name"] == "Bob"


def test_b64_credentials(monkeypatch):
    rows = [["Order Number", "Customer Name"], ["1", "Alice"]]
    sys.modules['gspread'] = make_gspread_stub(rows)
    import importlib
    import app.sheet_utils as sheet_utils
    importlib.reload(sheet_utils)
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    import base64
    creds = base64.b64encode(b"{}" ).decode()
    monkeypatch.setenv("GOOGLE_CREDENTIALS_B64", creds)
    monkeypatch.setenv("SHEET_ID", "dummy")

    res = sheet_utils.get_order_from_sheet("1")
    assert res == {"customer_name": "Alice", "customer_phone": "", "address": ""}
