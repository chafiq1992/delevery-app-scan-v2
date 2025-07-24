import os
import asyncio
import importlib
import sys
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

DB_FILE = 'tag_summary.db'
if os.path.exists(DB_FILE):
    os.remove(DB_FILE)

os.environ['DATABASE_URL'] = f'sqlite+aiosqlite:///{DB_FILE}'

from app import main as app_main
from app import db as app_db

importlib.reload(app_main)

async def create_orders():
    async with app_db.AsyncSessionLocal() as session:
        session.add(app_db.Order(driver_id='abderrehman', order_name='#1', store='irrakids', tags='big', delivery_status='Dispatched'))
        session.add(app_db.Order(driver_id='abderrehman', order_name='#2', store='irrakids', tags='fast', delivery_status='Dispatched'))
        session.add(app_db.Order(driver_id='abderrehman', order_name='#3', store='irranova', tags='fast', delivery_status='Dispatched'))
        session.add(app_db.Order(driver_id='abderrehman', order_name='#4', store='irranova', tags='big fast', delivery_status='Dispatched'))
        session.add(app_db.Order(driver_id='abderrehman', order_name='#5', store='irrakids', tags='oscario', delivery_status='Dispatched'))
        session.add(app_db.Order(driver_id='abderrehman', order_name='#6', store='irranova', tags='', delivery_status='Dispatched'))
        await session.commit()

def setup_app():
    client = TestClient(app_main.app)
    asyncio.run(app_main.init_db())
    asyncio.run(create_orders())
    return client

def test_tag_summary():
    client = setup_app()
    resp = client.get('/tag-summary')
    assert resp.status_code == 200
    data = resp.json()
    assert data['big']['irrakids'] == 1
    assert data['big']['irranova'] == 1
    assert data['fast']['irrakids'] == 1
    assert data['fast']['irranova'] == 1
    assert data['oscario']['irrakids'] == 1
    assert 'irranova' not in data['oscario']
