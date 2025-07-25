import os, asyncio, sys, importlib
from fastapi.testclient import TestClient
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

DB_FILE = 'admin_agents.db'


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
    if old:
        os.environ['DATABASE_URL'] = old
    from app import main as app_main
    from app import db as app_db
    from app import models as app_models
    importlib.reload(app_db)
    importlib.reload(app_models)
    importlib.reload(app_main)


async def create_records(db, models):
    async with db.AsyncSessionLocal() as session:
        d1 = models.Driver(id='d1')
        agent = models.Agent(username='bob', password='pw', drivers=[d1])
        merchant = models.Merchant(name='Shop', drivers=[d1], agents=[agent])
        session.add_all([d1, agent, merchant])
        await session.commit()

async def create_base(db, models):
    async with db.AsyncSessionLocal() as session:
        d1 = models.Driver(id='d1')
        d2 = models.Driver(id='d2')
        m = models.Merchant(name='Shop')
        session.add_all([d1, d2, m])
        await session.commit()
        return m.id


def test_admin_agents_include_merchants():
    app_main, app_db, app_models, client, old_db = setup_app()
    asyncio.run(create_records(app_db, app_models))

    resp = client.get('/admin/agents')
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    agent = data[0]
    assert agent['username'] == 'bob'
    assert agent['drivers'] == ['d1']
    assert agent['merchants'] == ['Shop']

    reset_app(old_db)


def test_agent_create_update_merchants():
    app_main, app_db, app_models, client, old_db = setup_app()
    mid = asyncio.run(create_base(app_db, app_models))

    resp = client.post(
        '/admin/agents',
        json={
            'username': 'alice',
            'password': 'pw',
            'drivers': ['d1'],
            'merchants': [mid],
        },
    )
    assert resp.status_code == 201

    resp = client.get('/admin/agents')
    data = resp.json()[0]
    assert data['merchants'] == ['Shop']

    resp = client.put('/admin/agents/alice', json={'username': 'alice', 'merchants': []})
    assert resp.status_code == 200

    resp = client.get('/admin/agents')
    assert resp.json()[0]['merchants'] == []

    reset_app(old_db)
