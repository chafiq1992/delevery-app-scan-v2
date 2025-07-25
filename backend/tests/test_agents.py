import os, asyncio, sys
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

db_file = 'agents_test.db'
if os.path.exists(db_file):
    os.remove(db_file)
os.environ['DATABASE_URL'] = f'sqlite+aiosqlite:///{db_file}'

def setup_app():
    from app import main as app_main
    from app import db as app_db
    from app import models as app_models
    client = TestClient(app_main.app)
    asyncio.run(app_main.init_db())
    return app_main, app_db, app_models, client

async def create_agent(app_db, models):
    async with app_db.AsyncSessionLocal() as session:
        driver = models.Driver(id='d1')
        agent = models.Agent(username='alice', password='secret', drivers=[driver])
        session.add_all([driver, agent])
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
