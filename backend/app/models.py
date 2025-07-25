from sqlalchemy import (
    Column,
    Integer,
    String,
    Float,
    DateTime,
    Text,
    ForeignKey,
)
from sqlalchemy.orm import declarative_base, relationship
from datetime import datetime

from sqlalchemy import Table

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
    note_id = Column(Integer, ForeignKey("delivery_notes.id",), index=True)
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

class VerificationOrder(Base):
    """Orders imported from the Google Sheet for admin verification."""

    __tablename__ = "verification_orders"

    id = Column(Integer, primary_key=True, autoincrement=True)
    order_date = Column(String, index=True)
    order_name = Column(String, index=True)
    customer_name = Column(String)
    customer_phone = Column(String)
    address = Column(Text)
    cod_total = Column(String)
    city = Column(String)
    driver_id = Column(String, ForeignKey("drivers.id"))
    scan_time = Column(DateTime)

    driver = relationship("Driver")


# ---------------------------------------------------------------------------
# Follow Agents and assignments
# ---------------------------------------------------------------------------

# Association table linking follow agents to drivers they manage
agent_driver_table = Table(
    "agent_drivers",
    Base.metadata,
    Column("agent_id", Integer, ForeignKey("agents.id"), primary_key=True),
    Column("driver_id", String, ForeignKey("drivers.id"), primary_key=True),
)


class Agent(Base):
    """Follow agent able to access specific drivers."""

    __tablename__ = "agents"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String, unique=True, nullable=False)
    password = Column(String, nullable=False)

    drivers = relationship("Driver", secondary=agent_driver_table, backref="agents")


# ---------------------------------------------------------------------------
# Merchants and their assignments
# ---------------------------------------------------------------------------

# Association table linking merchants to drivers
merchant_driver_table = Table(
    "merchant_drivers",
    Base.metadata,
    Column("merchant_id", Integer, ForeignKey("merchants.id"), primary_key=True),
    Column("driver_id", String, ForeignKey("drivers.id"), primary_key=True),
)

# Association table linking merchants to follow agents
merchant_agent_table = Table(
    "merchant_agents",
    Base.metadata,
    Column("merchant_id", Integer, ForeignKey("merchants.id"), primary_key=True),
    Column("agent_id", Integer, ForeignKey("agents.id"), primary_key=True),
)


class Merchant(Base):
    """Merchant entity accessible to multiple agents and drivers."""

    __tablename__ = "merchants"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, unique=True, nullable=False)

    drivers = relationship(
        "Driver", secondary=merchant_driver_table, backref="merchants"
    )
    agents = relationship(
        "Agent", secondary=merchant_agent_table, backref="merchants"
    )
