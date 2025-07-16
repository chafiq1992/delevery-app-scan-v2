from sqlalchemy import (
    Column,
    Integer,
    String,
    Float,
    DateTime,
    Text,
    ForeignKey,
    select,
    text,
)
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import declarative_base, relationship
import os
from datetime import datetime

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL environment variable not set")

engine = create_async_engine(DATABASE_URL, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)

Base = declarative_base()

class Driver(Base):
    __tablename__ = "drivers"
    id = Column(String, primary_key=True)
    order_tab = Column(String)
    payouts_tab = Column(String)

class Order(Base):
    __tablename__ = "orders"
    id = Column(Integer, primary_key=True, autoincrement=True)
    driver_id = Column(String, ForeignKey("drivers.id"), index=True)
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)
    order_name = Column(String, index=True)
    customer_name = Column(String)
    customer_phone = Column(String)
    address = Column(Text)
    tags = Column(String)
    fulfillment = Column(String)
    order_status = Column(String)
    store = Column(String)
    delivery_status = Column(String, index=True)
    notes = Column(Text)
    driver_notes = Column(Text)
    scheduled_time = Column(String)
    scan_date = Column(String, index=True)
    cash_amount = Column(Float)
    driver_fee = Column(Float)
    payout_id = Column(String)
    status_log = Column(Text)
    comm_log = Column(Text)
    follow_log = Column(Text)

    driver = relationship("Driver")

class Payout(Base):
    __tablename__ = "payouts"
    id = Column(Integer, primary_key=True, autoincrement=True)
    driver_id = Column(String, ForeignKey("drivers.id"), index=True)
    payout_id = Column(String, index=True)
    date_created = Column(DateTime, default=datetime.utcnow, index=True)
    orders = Column(Text)
    total_cash = Column(Float)
    total_fees = Column(Float)
    total_payout = Column(Float)
    status = Column(String)
    date_paid = Column(DateTime)

    driver = relationship("Driver")


class DeliveryNote(Base):
    __tablename__ = "delivery_notes"
    id = Column(Integer, primary_key=True, autoincrement=True)
    driver_id = Column(String, ForeignKey("drivers.id"), index=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    status = Column(String, default="draft", index=True)
    approved_at = Column(DateTime)

    driver = relationship("Driver")
    items = relationship("DeliveryNoteItem", back_populates="note")


class DeliveryNoteItem(Base):
    __tablename__ = "delivery_note_items"
    id = Column(Integer, primary_key=True, autoincrement=True)
    note_id = Column(Integer, ForeignKey("delivery_notes.id"), index=True)
    order_id = Column(Integer, ForeignKey("orders.id"), index=True)
    scanned_at = Column(DateTime, default=datetime.utcnow)

    note = relationship("DeliveryNote", back_populates="items")
    order = relationship("Order")

class EmployeeLog(Base):
    __tablename__ = "employee_logs"
    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)
    employee = Column(String, index=True)
    order = Column(String, index=True)
    amount = Column(Float)

async def get_session() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session
        

async def init_db() -> None:
    """Create tables and ensure default drivers exist."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

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

    default_drivers = ["abderrehman", "anouar", "mohammed", "nizar"]
    async with AsyncSessionLocal() as session:
        for d_id in default_drivers:
            if not await session.get(Driver, d_id):
                session.add(Driver(id=d_id))
        await session.commit()
