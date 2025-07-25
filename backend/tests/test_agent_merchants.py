import os, asyncio, importlib, sys
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

DB_FILE = 'agent_merchants_test.db'


def setup_app():
    old = os.environ.get('DATABASE_URL')
    if os.path.exists(DB_FILE):
        os.remove(DB_FILE)
    os.environ['DATABASE_URL'] = f'sqlite+aiosqlite:///{DB_FILE}'
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
    if old and old.startswith('sqlite'):
        os.environ['DATABASE_URL'] = old
    from app import main as app_main
    from app import db as app_db
    from app import models as app_models
    importlib.reload(app_db)
    importlib.reload(app_models)
    importlib.reload(app_main)
    asyncio.run(app_main.init_db())


async def create_drivers(db, models):
    async with db.AsyncSessionLocal() as session:
        session.add(models.Driver(id='d1'))
        await session.commit()


def test_agent_merchant_assignment():
    app_main, app_db, app_models, client, old_db = setup_app()
    asyncio.run(create_drivers(app_db, app_models))

    # create merchant
    resp = client.post('/admin/merchants', json={'name': 'Shop', 'drivers': ['d1']})
    assert resp.status_code == 201
    mid1 = resp.json()['id']

    # create agent with merchant
    resp = client.post('/admin/agents', json={'username': 'bob', 'password': 'pw', 'drivers': ['d1'], 'merchants': [mid1]})
    assert resp.status_code == 201

    agents = client.get('/admin/agents').json()
    assert agents[0]['merchants'] == [mid1]

    data = {m['id']: m for m in client.get('/admin/merchants').json()}
    assert data[mid1]['agents'] == ['bob']

    # create second merchant and update agent assignments
    resp = client.post('/admin/merchants', json={'name': 'Shop2', 'drivers': []})
    mid2 = resp.json()['id']

    resp = client.put(
        f'/admin/agents/bob',
        json={'username': 'bob', 'password': 'pw', 'drivers': ['d1'], 'merchants': [mid2]},
    )
    assert resp.status_code == 200

    agents = client.get('/admin/agents').json()
    assert agents[0]['merchants'] == [mid2]

    data = {m['id']: m for m in client.get('/admin/merchants').json()}
    assert data[mid1]['agents'] == []
    assert data[mid2]['agents'] == ['bob']

    reset_app(old_db)
