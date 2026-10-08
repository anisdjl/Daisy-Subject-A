from fastapi import FastAPI, Depends
from sqlalchemy.orm import Session as DbSession
from .database import engine, get_db
from .models import models

# C'est cette ligne qui crée physiquement les tables sur Supabase au lancement
models.Base.metadata.create_all(bind=engine)

app = FastAPI(title="Daisy API - Test")

@app.get("/")
def read_root(db: DbSession = Depends(get_db)):
    return {"status": "ok", "message": "L'API tourne et Supabase est connecté."}

