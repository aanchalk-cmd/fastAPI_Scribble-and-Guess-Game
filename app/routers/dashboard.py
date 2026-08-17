"""
Admin dashboard routes.

Authorization: this project has no auth system yet. These endpoints are open.
Add an admin dependency here when authentication is introduced.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.services import dashboard_metrics as metrics
from database import get_db

router = APIRouter(tags=["dashboard"])
templates = Jinja2Templates(directory="templates")


@router.get("/dashboard")
async def dashboard_page(request: Request):
    """Render the admin dashboard shell; data is loaded via API."""
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            # Hook point: set True once admin auth exists.
            "auth_required_note": True,
        },
    )


@router.get("/api/dashboard/summary")
def dashboard_summary(db: Session = Depends(get_db)):
    return metrics.get_summary(db)


@router.get("/api/dashboard/leaderboard")
def dashboard_leaderboard(
    limit: int = Query(10, ge=1, le=100),
    db: Session = Depends(get_db),
):
    return {"items": metrics.get_leaderboard(db, limit=limit)}


@router.get("/api/dashboard/activity")
def dashboard_activity(
    range: str = Query("7d", pattern="^(7d|30d|7|30|month)$"),
    db: Session = Depends(get_db),
):
    return metrics.get_game_activity(db, range_key=range)


@router.get("/api/dashboard/peak-activity")
def dashboard_peak_activity(db: Session = Depends(get_db)):
    return metrics.get_peak_activity(db)


@router.get("/api/dashboard/game-health")
def dashboard_game_health(db: Session = Depends(get_db)):
    return metrics.get_game_health(db)


@router.get("/api/dashboard/recent-games")
def dashboard_recent_games(
    limit: int = Query(20, ge=1, le=50),
    db: Session = Depends(get_db),
):
    return {"items": metrics.get_recent_games(db, limit=limit)}
