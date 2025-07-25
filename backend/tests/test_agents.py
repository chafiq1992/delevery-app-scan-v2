import os, asyncio, sys
from fastapi.testclient import TestClient
from sqlalchemy import select

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

db_file = 'agents_test.db'
os.environ['DATABASE_URL'] = f'sqlite+aiosqlite:///{db_file}'

def setup_app():
    if os.path.exists(db_file):
        os.remove(db_file)
    from app import main as app_main
    from app import db as app_db
    from app import models as app_models
    client = TestClient(app_main.app)
    asyncio.run(app_main.init_db())
    return app_main, app_db, app_models, client

async def create_agent(app_db, models, username='alice'):
    async with app_db.AsyncSessionLocal() as session:
        driver1 = await session.get(models.Driver, 'd1')
        if not driver1:
            driver1 = models.Driver(id='d1')
            session.add(driver1)
        driver2 = await session.get(models.Driver, 'd2')
        if not driver2:
            driver2 = models.Driver(id='d2')
            session.add(driver2)
        agent = await session.scalar(select(models.Agent).where(models.Agent.username == username))
        if not agent:
            agent = models.Agent(username=username, password='secret', drivers=[driver1])
            session.add(agent)
        await session.commit()


def test_follow_login_and_drivers():
    app_main, app_db, app_models, client = setup_app()
    asyncio.run(create_agent(app_db, app_models))

    resp = client.post('/follow/login', data={'username':'alice','password':'secret'})
    assert resp.status_code == 200
    cookies = resp.cookies
    resp = client.get('/drivers', cookies={'agent': cookies.get('agent')})
    assert resp.status_code == 200
    assert resp.json() == ['d1']


def test_drivers_all_query():
    app_main, app_db, app_models, client = setup_app()
    asyncio.run(create_agent(app_db, app_models, username='bob'))

    resp = client.post('/follow/login', data={'username':'bob','password':'secret'})
    assert resp.status_code == 200
    cookies = resp.cookies

    # With agent cookie only assigned driver should be returned
    resp = client.get('/drivers', cookies={'agent': cookies.get('agent')})
    assert resp.status_code == 200
    assert resp.json() == ['d1']

    # Query param all=true bypasses agent filtering
    resp = client.get('/drivers?all=true', cookies={'agent': cookies.get('agent')})
    assert resp.status_code == 200
    data = resp.json()
    assert 'd1' in data and 'd2' in data

    # Clearing cookies should also return all drivers
    client.cookies.clear()
    resp = client.get('/drivers')
    assert resp.status_code == 200
    data = resp.json()
    assert 'd1' in data and 'd2' in data


def test_create_agent_without_password_fails():
    app_main, app_db, app_models, client = setup_app()
    resp = client.post('/admin/agents', json={'username': 'nopass'})
    assert resp.status_code == 400


def test_update_agent_without_password():
    app_main, app_db, app_models, client = setup_app()
    asyncio.run(create_agent(app_db, app_models, username='editme'))
    resp = client.put('/admin/agents/editme', json={'username':'editme','drivers': ['d1', 'd2']})
    assert resp.status_code == 200


