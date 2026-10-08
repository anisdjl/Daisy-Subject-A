# main.py
import hmac
import hashlib
import os
import json
from fastapi import FastAPI, Depends, Request, HTTPException, Header, status
from sqlalchemy.orm import Session as DbSession
from sqlalchemy.exc import IntegrityError
from .database import engine, get_db
from .models import models
from .schemas import schemas

app = FastAPI(
    title="Daisy Channel Manager - Sujet A",
    description="Synchronisation bidirectionnelle Daisy <-> Artisia avec verrouillage pessimiste et idempotence"
)

ARTISIA_WEBHOOK_SECRET = os.getenv("ARTISIA_WEBHOOK_SECRET", "artisia_shared_secret_demo")


def verify_hmac_signature(raw_body: bytes, signature_header: str | None) -> bool:
    """
    Vérifie l'en-tête X-Artisia-Signature: sha256=<hmac>.
    En mode test/Swagger, on autorise si le header est absent, vide ou vaut 'dev'.
    """
    if not signature_header or signature_header.strip() in ("", "dev"):
        return True
    
    expected_prefix = "sha256="
    if not signature_header.startswith(expected_prefix):
        return False
    
    received_hash = signature_header[len(expected_prefix):]
    computed_hash = hmac.new(
        ARTISIA_WEBHOOK_SECRET.encode(),
        raw_body,
        hashlib.sha256
    ).hexdigest()
    
    return hmac.compare_digest(computed_hash, received_hash)


@app.post("/sessions", status_code=status.HTTP_201_CREATED, tags=["Admin / Setup"])
def create_session(data: schemas.CreateSessionRequest, db: DbSession = Depends(get_db)):
    """Crée un créneau dans Daisy pour les tests."""
    existing = db.query(models.Session).filter(models.Session.id == data.id).first()
    if existing:
        raise HTTPException(status_code=400, detail="Ce créneau existe déjà.")
    
    session = models.Session(
        id=data.id,
        total_capacity=data.total_capacity,
        daisy_booked=0,
        partner_booked=0
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


@app.get("/sessions/{session_id}", tags=["Admin / Setup"])
def get_session(session_id: str, db: DbSession = Depends(get_db)):
    """Inspecte les places et réservations réelles."""
    session = db.query(models.Session).filter(models.Session.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session non trouvée.")
    
    available = session.total_capacity - (session.daisy_booked + session.partner_booked)
    return {
        "session_id": session.id,
        "total_capacity": session.total_capacity,
        "daisy_booked": session.daisy_booked,
        "partner_booked": session.partner_booked,
        "available_seats": available
    }


@app.post("/webhook/artisia", tags=["Partenaires"])
async def receive_artisia_webhook(
    payload: schemas.ArtisiaWebhookPayload,
    request: Request,
    x_artisia_signature: str | None = Header(default=None),
    db: DbSession = Depends(get_db)
):
    raw_body = await request.body()
    
    # 1. Vérification de sécurité HMAC
    if not verify_hmac_signature(raw_body, x_artisia_signature):
        raise HTTPException(status_code=401, detail="Signature HMAC invalide.")

    # 2. Barrière d'idempotence : enregistrement de l'event_id
    webhook_event = models.WebhookEvent(event_id=payload.event_id)
    db.add(webhook_event)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        return {
            "status": "ignored",
            "reason": "already_processed",
            "event_id": payload.event_id
        }

    # 3. Traitement métier sous verrou pessimiste
    if payload.type == "booking.created":
        booking_data = payload.data
        
        session_obj = (
            db.query(models.Session)
            .filter(models.Session.id == booking_data.session_id)
            .with_for_update()
            .first()
        )
        
        if not session_obj:
            db.rollback()
            raise HTTPException(status_code=404, detail="Créneau inconnu dans Daisy.")

        available = session_obj.total_capacity - (session_obj.daisy_booked + session_obj.partner_booked)
        
        if available >= booking_data.seats:
            session_obj.partner_booked += booking_data.seats
            new_booking = models.Booking(
                id=f"bk_{payload.event_id}",
                session_id=session_obj.id,
                source="artisia",
                external_id=booking_data.booking_id,
                seats=booking_data.seats,
                status="confirmed"
            )
            db.add(new_booking)
            db.commit()
            return {"status": "processed", "result": "booking_confirmed"}
        else:
            overbooking = models.Booking(
                id=f"bk_{payload.event_id}",
                session_id=session_obj.id,
                source="artisia",
                external_id=booking_data.booking_id,
                seats=booking_data.seats,
                status="conflict_overbooked"
            )
            db.add(overbooking)
            db.commit()
            return {
                "status": "processed_with_conflict",
                "alert": "OVERBOOKING_DETECTED",
                "message": "Réservation partenaire enregistrée en conflit pour arbitrage artisan."
            }

    elif payload.type == "booking.cancelled":
        booking_data = payload.data
        session_obj = (
            db.query(models.Session)
            .filter(models.Session.id == booking_data.session_id)
            .with_for_update()
            .first()
        )
        
        if session_obj:
            session_obj.partner_booked = max(0, session_obj.partner_booked - booking_data.seats)
            existing_booking = (
                db.query(models.Booking)
                .filter(models.Booking.external_id == booking_data.booking_id)
                .first()
            )
            if existing_booking:
                existing_booking.status = "cancelled"
            
            db.commit()
            return {"status": "processed", "result": "booking_cancelled"}

    db.commit()
    return {"status": "acknowledged", "type": payload.type}