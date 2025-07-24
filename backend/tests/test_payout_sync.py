import os, asyncio, sys
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

db_path = 'payout.db'
if os.path.exists(db_path):
    os.remove(db_path)
os.environ['DATABASE_URL'] = f'sqlite+aiosqlite:///{db_path}'

from sqlalchemy import select

def setup_app():
    from app import main as app_main
    from app import db as app_db
    client = TestClient(app_main.app)
    asyncio.run(app_main.init_db())
    return app_main, app_db, client

async def setup_records(app_main, app_db):
    async with app_db.AsyncSessionLocal() as session:
        if not await session.get(app_db.Driver, 'abder'):
            session.add(app_db.Driver(id='abder'))
        order = app_db.Order(driver_id='abder', order_name='#1', delivery_status='Livré', cash_amount=100, payout_id='PO-1')
        payout = app_db.Payout(driver_id='abder', payout_id='PO-1', orders='#1', total_cash=100, total_fees=20, total_payout=80, status='pending')
        session.add_all([order, payout])
        await session.commit()

def get_order_status(app_main, app_db):
    async def inner():
        async with app_db.AsyncSessionLocal() as session:
            o = await session.scalar(select(app_db.Order).where(app_db.Order.order_name=='#1'))
            return o.delivery_status
    return asyncio.run(inner())

def test_mark_paid_and_unpaid():
    app_main, app_db, client = setup_app()
    asyncio.run(setup_records(app_main, app_db))

    resp = client.post('/payout/mark-paid/PO-1?driver=abder')
    assert resp.status_code == 200
    assert get_order_status(app_main, app_db) == 'Paid'

    resp = client.post('/payout/mark-unpaid/PO-1?driver=abder')
    assert resp.status_code == 200
    assert get_order_status(app_main, app_db) == 'Livré'
