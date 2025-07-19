import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import types
import importlib
import base64

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


def test_lookup_strips_hash(monkeypatch):
    rows = [
        ["Order Number", "Customer Name"],
        ["#1234", "Alice"],
        ["5678", "Bob"],
    ]
    calls = []
    sys.modules['gspread'] = make_gspread_stub(rows, calls)
    import app.sheet_utils as sheet_utils
    importlib.reload(sheet_utils)
    cred_json = '{"dummy": "yes"}'
    b64 = base64.b64encode(cred_json.encode()).decode()
    monkeypatch.setenv("GOOGLE_CREDENTIALS_B64", b64)
    monkeypatch.setenv("SHEET_ID", "dummy")

    res1 = sheet_utils.get_order_from_sheet("1234")
    assert res1 == {"customer_name": "Alice", "customer_phone": "", "address": ""}

    res2 = sheet_utils.get_order_from_sheet("#5678")
    assert res2["customer_name"] == "Bob"
    assert calls[0] == ("dict", {"dummy": "yes"})


def test_load_sheet_orders_missing_date(monkeypatch):
    rows = [
        ["Order", "Customer"],
        ["#111", "Alice"],
    ]
    calls = []
    sys.modules['gspread'] = make_gspread_stub(rows, calls)
    import app.sheet_utils as sheet_utils
    importlib.reload(sheet_utils)
    cred_json = '{"dummy": "yes"}'
    b64 = base64.b64encode(cred_json.encode()).decode()
    monkeypatch.setenv("GOOGLE_CREDENTIALS_B64", b64)
    monkeypatch.setenv("VERIFICATION_SHEET_ID", "dummy")

    orders = sheet_utils.load_sheet_orders()
    assert orders == [
        {
            "order_name": "#111",
            "order_date": None,
            "customer_name": "Alice",
            "customer_phone": "",
            "address": "",
            "city": "",
            "cod_total": "",
        }
    ]
