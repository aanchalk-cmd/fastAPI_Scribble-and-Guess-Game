"""
Admin dashboard routes.

The page and every /api/dashboard route require HTTP basic auth. The username
and password come from DASHBOARD_USERNAME and DASHBOARD_PASSWORD. If either is
missing, the dashboard stays closed. Numbers are read from the same database
as the game (DATABASE_URL).
"""

from __future__ import annotations

import os
import secrets

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.services import dashboard_metrics as metrics
from database import get_db

_basic = HTTPBasic(realm="Dashboard")
templates = Jinja2Templates(directory="templates")


def require_dashboard_admin(
    credentials: HTTPBasicCredentials = Depends(_basic),
) -> None:
    """One shared password for the person who owns the dashboard. Players have no account here."""
    username = os.getenv("DASHBOARD_USERNAME", "")
    password = os.getenv("DASHBOARD_PASSWORD", "")
    if not username or not password:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Dashboard is not configured",
        )
    user_ok = secrets.compare_digest(credentials.username.encode(), username.encode())
    pass_ok = secrets.compare_digest(credentials.password.encode(), password.encode())
    if not (user_ok and pass_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
            headers={"WWW-Authenticate": 'Basic realm="Dashboard"'},
        )


router = APIRouter(tags=["dashboard"], dependencies=[Depends(require_dashboard_admin)])


@router.get("/dashboard")
async def dashboard_page(request: Request):
    """Render the admin dashboard shell; data is loaded via API."""
    return templates.TemplateResponse(
        "dashboard.html",
        {"request": request},
    )


@router.get("/api/dashboard/summary")
def dashboard_summary(db: Session = Depends(get_db)):
    return metrics.get_summary(db)


@router.get("/api/dashboard/leaderboard")
def dashboard_leaderboard(
    limit: int = Query(100, ge=1, le=200),
    db: Session = Depends(get_db),
):
    return {"items": metrics.get_leaderboard(db, limit=limit)}


@router.get("/api/dashboard/activity")
def dashboard_activity(
    range: str = Query("all", pattern="^(7d|30d|7|30|month|all|alltime)$"),
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
    limit: int = Query(100, ge=1, le=200),
    db: Session = Depends(get_db),
):
    return {"items": metrics.get_recent_games(db, limit=limit)}
