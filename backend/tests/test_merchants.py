import os, asyncio, sys, importlib
from fastapi.testclient import TestClient
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

DB_FILE = 'merchants_test.db'

def setup_app():
    old = os.environ.get('DATABASE_URL')
    if os.path.exists(DB_FILE):
        os.remove(DB_FILE)
    os.environ['DATABASE_URL'] = f'sqlite+aiosqlite:///{DB_FILE}'
    import importlib
    from app import main as app_main
    from app import db as app_db
    from app import models as app_models
    importlib.reload(app_db)
    importlib.reload(app_models)
    importlib.reload(app_main)
    client = TestClient(app_main.app)
    asyncio.run(app_main.init_db())
    return app_main, app_db, app_models, client, old

def reset_app(old):
    if old:
        os.environ['DATABASE_URL'] = old
    import importlib
    from app import main as app_main
    from app import db as app_db
    from app import models as app_models
    importlib.reload(app_db)
    importlib.reload(app_models)
    importlib.reload(app_main)
    asyncio.run(app_main.init_db())

async def create_records(db, models):
    async with db.AsyncSessionLocal() as session:
        d1 = models.Driver(id='d1')
        d2 = models.Driver(id='d2')
        a1 = models.Agent(username='alice', password='pw')
        session.add_all([d1, d2, a1])
        await session.commit()


def test_merchant_crud():
    app_main, app_db, app_models, client, old_db = setup_app()
    asyncio.run(create_records(app_db, app_models))

    resp = client.post('/admin/merchants', json={'name':'Shop','drivers':['d1'],'agents':['alice']})
    assert resp.status_code == 201
    merchant_id = resp.json()['id']

    resp = client.get('/admin/merchants')
    data = resp.json()
    assert len(data) == 1
    assert data[0]['name'] == 'Shop'
    assert data[0]['drivers'] == ['d1']
    assert data[0]['agents'] == ['alice']

    resp = client.put(f'/admin/merchants/{merchant_id}', json={'name':'Shop2','drivers':['d1','d2'],'agents':[]})
    assert resp.status_code == 200

    resp = client.get('/admin/merchants')
    data = resp.json()[0]
    assert data['name'] == 'Shop2'
    assert sorted(data['drivers']) == ['d1','d2']
    assert data['agents'] == []

    resp = client.delete(f'/admin/merchants/{merchant_id}')
    assert resp.status_code == 200
    assert client.get('/admin/merchants').json() == []
    reset_app(old_db)
