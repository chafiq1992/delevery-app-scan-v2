import os, asyncio, sys
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

DB_FILE = 'notes_test.db'

from sqlalchemy import select

def setup_app():
    if os.path.exists(DB_FILE):
        os.remove(DB_FILE)
    os.environ['DATABASE_URL'] = f'sqlite+aiosqlite:///{DB_FILE}'
    from app import main as app_main
    from app import db as app_db
    client = TestClient(app_main.app)
    asyncio.run(app_main.init_db())
    return app_main, app_db, client

def setup_records(app_main, app_db):
    async def inner():
        async with app_db.AsyncSessionLocal() as session:
            if not await session.get(app_db.Driver, 'd1'):
                session.add(app_db.Driver(id='d1'))
            order = app_db.Order(driver_id='d1', order_name='#1', delivery_status='Livré', cash_amount=50)
            session.add(order)
            await session.flush()
            note = app_db.DeliveryNote(driver_id='d1', status='approved')
            session.add(note)
            await session.flush()
            session.add(app_db.DeliveryNoteItem(note_id=note.id, order_id=order.id))
            payout = app_db.Payout(driver_id='d1', payout_id='PO-1', orders='#1', total_cash=50, total_fees=10, total_payout=40, status='paid')
            session.add(payout)
            await session.commit()
    asyncio.run(inner())

def get_order_status(app_db):
    async def inner():
        async with app_db.AsyncSessionLocal() as session:
            o = await session.scalar(select(app_db.Order).where(app_db.Order.order_name=='#1'))
            return o.delivery_status
    return asyncio.run(inner())

def test_notes_sync_sets_paid():
    app_main, app_db, client = setup_app()
    setup_records(app_main, app_db)
    resp = client.get('/admin/notes?driver=d1')
    assert resp.status_code == 200
    data = resp.json()
    assert data and data[0]['items'][0]['status'] == 'Paid'
    assert get_order_status(app_db) == 'Paid'
