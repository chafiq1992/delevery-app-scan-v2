import os
import pytest
from fastapi.testclient import TestClient

# Use in-memory SQLite for tests
os.environ.setdefault('DATABASE_URL', 'sqlite+aiosqlite:///:memory:')

from app.main import app

client = TestClient(app)


def test_websocket_connection():
    with client.websocket_connect('/ws') as ws:
        ws.send_text('ping')

