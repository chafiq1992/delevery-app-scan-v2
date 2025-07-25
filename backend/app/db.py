import os
import logging
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from .models import (
    Base,
    Driver,
    Order,
    Payout,
    DeliveryNote,
    DeliveryNoteItem,
    EmployeeLog,
    VerificationOrder,
    Merchant,
)

logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL environment variable not set")

engine = create_async_engine(DATABASE_URL, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)

async def get_session() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session


async def init_db() -> None:
    """Create tables and ensure default drivers exist."""
    logger.info("Initializing database")
    async with engine.begin() as conn:
        logger.info("Creating tables if not exist")
        await conn.run_sync(Base.metadata.create_all)
        logger.info("Tables created")

        if not engine.url.drivername.startswith("sqlite"):
            # Ensure new columns exist when upgrading without migrations
            result = await conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name='orders' AND column_name='follow_log'"
                )
            )
            if not result.first():
                await conn.execute(text("ALTER TABLE orders ADD COLUMN follow_log TEXT"))

            result = await conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name='orders' AND column_name='driver_notes'"
                )
            )
            if not result.first():
                await conn.execute(text("ALTER TABLE orders ADD COLUMN driver_notes TEXT"))

            result = await conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name='orders' AND column_name='return_pending'"
                )
            )
            if not result.first():
                await conn.execute(text("ALTER TABLE orders ADD COLUMN return_pending INTEGER DEFAULT 0"))

            result = await conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name='orders' AND column_name='return_agent'"
                )
            )
            if not result.first():
                await conn.execute(text("ALTER TABLE orders ADD COLUMN return_agent VARCHAR(255)"))

            result = await conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name='orders' AND column_name='return_time'"
                )
            )
            if not result.first():
                await conn.execute(text("ALTER TABLE orders ADD COLUMN return_time TIMESTAMP"))

    default_drivers = ["abderrehman", "anouar", "mohammed", "nizar"]
    async with AsyncSessionLocal() as session:
        for d_id in default_drivers:
            if not await session.get(Driver, d_id):
                logger.info("Inserting default driver %s", d_id)
                session.add(Driver(id=d_id))
        await session.commit()

        await session.execute(
            text(
                "UPDATE orders SET return_pending=1 "
                "WHERE delivery_status IN ('Returned','Annulé','Refusé') "
                "AND (return_pending IS NULL OR return_pending=0) "
                "AND return_time IS NULL"
            )
        )
        await session.commit()
    logger.info("Database initialization complete")

