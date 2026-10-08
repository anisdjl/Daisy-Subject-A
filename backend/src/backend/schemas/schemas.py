from pydantic import BaseModel
from typing import Optional, Dict, Any

class ArtisiaCustomer(BaseModel):
    name: str
    email: str

class ArtisiaBookingData(BaseModel):
    booking_id: str
    session_id: str
    seats: int = 1
    customer: Optional[ArtisiaCustomer] = None

class ArtisiaWebhookPayload(BaseModel):
    event_id: str
    type: str
    occurred_at: str
    data: ArtisiaBookingData

class CreateSessionRequest(BaseModel):
    id: str
    total_capacity: int

class CreateDaisyBookingRequest(BaseModel):
    session_id: str
    customer_name: str
    customer_email: str
    seats: int = 1