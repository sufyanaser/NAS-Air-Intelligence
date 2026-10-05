from __future__ import annotations

from fastapi import FastAPI, HTTPException

from .db import Database
from .reporting import build_report


def create_app(db_path: str = "data/nas-air.db") -> FastAPI:
    db = Database(db_path)
    db.initialize()
    app = FastAPI(title="NAS Air Intelligence API", version="0.1.0")

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.get("/stations")
    def stations() -> list[dict]:
        return db.stations()

    @app.get("/sessions")
    def sessions() -> list[dict]:
        return db.sessions()

    @app.get("/sessions/{session_id}")
    def session(session_id: str) -> dict:
        row = db.session(session_id)
        if not row:
            raise HTTPException(status_code=404, detail="session not found")
        return row

    @app.get("/sessions/{session_id}/timeline")
    def timeline(session_id: str) -> dict:
        if not db.session(session_id):
            raise HTTPException(status_code=404, detail="session not found")
        return {
            "session_id": session_id,
            "chunks": db.chunks(session_id),
            "events": db.events(session_id),
            "incidents": db.incidents(session_id),
        }

    @app.get("/sessions/{session_id}/report")
    def report(session_id: str) -> dict:
        try:
            return build_report(db, session_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="session not found") from exc

    return app
