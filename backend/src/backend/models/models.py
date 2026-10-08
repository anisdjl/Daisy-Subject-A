# models.py
from sqlalchemy import Column, Integer, String, DateTime, JSON
from sqlalchemy.sql import func
from ..database import Base

class Session(Base):
    __tablename__ = "sessions"
    
    id = Column(String, primary_key=True, index=True)
    total_capacity = Column(Integer, nullable=False)
    daisy_booked = Column(Integer, default=0, nullable=False)
    partner_booked = Column(Integer, default=0, nullable=False)

class WebhookEvent(Base):
    __tablename__ = "webhook_events"
    
    event_id = Column(String, primary_key=True, index=True)
    processed_at = Column(DateTime(timezone=True), server_default=func.now())

class OutboxTask(Base):
    __tablename__ = "outbox_tasks"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String, nullable=False)
    payload = Column(JSON, nullable=False)
    status = Column(String, default="pending")

class Booking(Base):
    __tablename__ = "bookings"
    
    id = Column(String, primary_key=True, index=True)
    session_id = Column(String, nullable=False)
    source = Column(String, nullable=False)
    external_id = Column(String, nullable=True)
    seats = Column(Integer, default=1, nullable=False)
    status = Column(String, default="confirmed")