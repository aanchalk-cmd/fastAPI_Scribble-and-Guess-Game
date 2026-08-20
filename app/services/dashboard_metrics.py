"""
Dashboard metrics — read-only aggregation queries over existing game tables.

Uses SQLAlchemy ORM aggregations against Player / Room / RoomPlayer.
Does not invent statuses or scoring rules beyond what the schema stores.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import case, cast, extract, func
from sqlalchemy.orm import Session, joinedload
from sqlalchemy.types import Integer

from models import Player, Room, RoomPlayer

# Room.status values used by the game (see db_helpers / GameRoom)
STATUS_LOBBY = "LOBBY"
STATUS_PLAYING = "PLAYING"
STATUS_ENDED = "ENDED"

DAY_NAMES = [
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_aware(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _duration_seconds(start: Optional[datetime], end: Optional[datetime]) -> Optional[float]:
    start = _as_aware(start)
    end = _as_aware(end)
    if not start or not end:
        return None
    delta = (end - start).total_seconds()
    return delta if delta >= 0 else None


def _completed_games_filter():
    """Games that actually started and later ended."""
    return (
        Room.game_started.is_(True),
        Room.status == STATUS_ENDED,
    )


def get_summary(db: Session) -> dict[str, Any]:
    total_players = db.query(func.count(Player.id)).scalar() or 0
    total_rooms = db.query(func.count(Room.id)).scalar() or 0
    active_games = (
        db.query(func.count(Room.id)).filter(Room.status == STATUS_PLAYING).scalar() or 0
    )
    total_games = (
        db.query(func.count(Room.id)).filter(*_completed_games_filter()).scalar() or 0
    )
    return {
        "total_players": int(total_players),
        "total_games": int(total_games),
        "active_games": int(active_games),
        "total_rooms": int(total_rooms),
    }


def get_leaderboard(db: Session, limit: int = 10) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit or 10), 100))
    players = (
        db.query(Player)
        .order_by(
            Player.total_wins.desc().nullslast(),
            Player.total_score.desc().nullslast(),
            Player.total_games_played.desc().nullslast(),
            Player.username.asc(),
        )
        .limit(limit)
        .all()
    )

    rows: list[dict[str, Any]] = []
    for rank, player in enumerate(players, start=1):
        games = int(player.total_games_played or 0)
        wins = int(player.total_wins or 0)
        score = int(player.total_score or 0)
        win_rate = round((wins / games) * 100, 1) if games > 0 else 0.0
        rows.append(
            {
                "rank": rank,
                "player_id": player.id,
                "username": player.username,
                "games_played": games,
                "wins": wins,
                "total_score": score,
                "highest_score": int(player.highest_score or 0),
                "win_rate": win_rate,
            }
        )
    return rows


def get_game_activity(db: Session, range_key: str = "7d") -> dict[str, Any]:
    """
    Daily game activity for Chart.js.

    - games_started: rooms with started_at in the window
    - games_played: completed rooms (ENDED + game_started) with ended_at in the window;
      falls back to started_at when ended_at is missing
    """
    days = 30 if str(range_key).lower() in {"30d", "30", "month"} else 7
    now = _utcnow()
    start = (now - timedelta(days=days - 1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )

    # Build continuous day labels (UTC calendar dates)
    labels: list[str] = []
    for i in range(days):
        day = start + timedelta(days=i)
        labels.append(day.strftime("%Y-%m-%d"))

    started_counts = {label: 0 for label in labels}
    played_counts = {label: 0 for label in labels}

    started_rows = (
        db.query(
            func.date_trunc("day", Room.started_at).label("day"),
            func.count(Room.id),
        )
        .filter(Room.started_at.isnot(None), Room.started_at >= start)
        .group_by("day")
        .all()
    )
    for day_val, count in started_rows:
        if day_val is None:
            continue
        key = _as_aware(day_val).strftime("%Y-%m-%d")
        if key in started_counts:
            started_counts[key] = int(count)

    # Completed games attributed to ended_at (or started_at fallback)
    activity_ts = func.coalesce(Room.ended_at, Room.started_at)
    played_rows = (
        db.query(
            func.date_trunc("day", activity_ts).label("day"),
            func.count(Room.id),
        )
        .filter(*_completed_games_filter(), activity_ts.isnot(None), activity_ts >= start)
        .group_by("day")
        .all()
    )
    for day_val, count in played_rows:
        if day_val is None:
            continue
        key = _as_aware(day_val).strftime("%Y-%m-%d")
        if key in played_counts:
            played_counts[key] = int(count)

    return {
        "range": f"{days}d",
        "labels": labels,
        "games_started": [started_counts[k] for k in labels],
        "games_played": [played_counts[k] for k in labels],
    }


def get_peak_activity(db: Session) -> dict[str, Any]:
    """
    Heatmap of player join activity: day-of-week × hour-of-day (UTC).

    Uses room_players.joined_at — real join timestamps, not fabricated.
    PostgreSQL DOW: 0=Sunday … 6=Saturday; we remap to Monday-first index 0–6.
    """
    # matrix[day_index][hour] where day_index 0=Monday … 6=Sunday
    matrix = [[0 for _ in range(24)] for _ in range(7)]

    dow = cast(extract("dow", RoomPlayer.joined_at), Integer)  # 0=Sun … 6=Sat
    hour = cast(extract("hour", RoomPlayer.joined_at), Integer)

    # Remap Sunday(0)->6, Monday(1)->0, … Saturday(6)->5
    monday_index = case((dow == 0, 6), else_=dow - 1)

    rows = (
        db.query(
            monday_index.label("day_idx"),
            hour.label("hour_idx"),
            func.count(RoomPlayer.id),
        )
        .filter(RoomPlayer.joined_at.isnot(None))
        .group_by("day_idx", "hour_idx")
        .all()
    )

    max_count = 0
    for day_idx, hour_idx, count in rows:
        if day_idx is None or hour_idx is None:
            continue
        d, h = int(day_idx), int(hour_idx)
        if 0 <= d <= 6 and 0 <= h <= 23:
            matrix[d][h] = int(count)
            max_count = max(max_count, int(count))

    return {
        "days": DAY_NAMES,
        "hours": list(range(24)),
        "matrix": matrix,
        "max": max_count,
        "timezone": "UTC",
        "source": "room_players.joined_at",
    }


def get_game_health(db: Session) -> dict[str, Any]:
    completed_count = (
        db.query(func.count(Room.id)).filter(*_completed_games_filter()).scalar() or 0
    )
    completed_count = int(completed_count)

    # AVG(ended_at - started_at) in seconds via PostgreSQL epoch extract
    avg_duration_raw = (
        db.query(func.avg(extract("epoch", Room.ended_at - Room.started_at)))
        .filter(
            *_completed_games_filter(),
            Room.started_at.isnot(None),
            Room.ended_at.isnot(None),
        )
        .scalar()
    )
    avg_duration_seconds: Optional[float] = (
        round(float(avg_duration_raw), 1) if avg_duration_raw is not None else None
    )

    # Average players per completed game (all memberships, including those who left)
    per_room = (
        db.query(func.count(RoomPlayer.id).label("pc"))
        .select_from(Room)
        .outerjoin(RoomPlayer, RoomPlayer.room_id == Room.id)
        .filter(*_completed_games_filter())
        .group_by(Room.id)
        .subquery()
    )
    avg_players_raw = db.query(func.avg(per_room.c.pc)).scalar()
    avg_players: Optional[float] = (
        round(float(avg_players_raw), 2) if avg_players_raw is not None else None
    )

    # Lobby rooms that ended without ever starting (closest available "abandoned" signal)
    abandoned_before_start = (
        db.query(func.count(Room.id))
        .filter(
            Room.status == STATUS_ENDED,
            Room.game_started.is_(False),
        )
        .scalar()
        or 0
    )
    abandoned_before_start = int(abandoned_before_start)

    ended_total = (
        db.query(func.count(Room.id)).filter(Room.status == STATUS_ENDED).scalar() or 0
    )
    ended_total = int(ended_total)

    abandoned_rate: Optional[float]
    if ended_total > 0:
        abandoned_rate = round((abandoned_before_start / ended_total) * 100, 1)
    else:
        abandoned_rate = None

    return {
        "average_game_duration_seconds": avg_duration_seconds,
        "average_players_per_game": avg_players,
        "completed_games": completed_count,
        "abandoned_before_start": abandoned_before_start,
        "abandoned_rate": abandoned_rate,
        # Mid-game abandon vs clean complete cannot be distinguished — both end as ENDED.
        "mid_game_abandon_available": False,
        "notes": (
            "Abandoned count = rooms that ended while still in lobby "
            "(ENDED + game_started=false). Mid-game abandon vs completed "
            "cannot be separated with the current schema."
        ),
    }


def get_recent_games(db: Session, limit: int = 20) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit or 20), 50))

    rooms = (
        db.query(Room)
        .options(joinedload(Room.host))
        .order_by(Room.created_at.desc().nullslast(), Room.id.desc())
        .limit(limit)
        .all()
    )
    if not rooms:
        return []

    room_ids = [room.id for room in rooms]
    count_rows = (
        db.query(RoomPlayer.room_id, func.count(RoomPlayer.id))
        .filter(RoomPlayer.room_id.in_(room_ids))
        .group_by(RoomPlayer.room_id)
        .all()
    )
    counts = {room_id: int(count) for room_id, count in count_rows}

    rows: list[dict[str, Any]] = []
    for room in rooms:
        duration = _duration_seconds(room.started_at, room.ended_at)
        host_name = room.host.username if room.host else None
        rows.append(
            {
                "id": room.id,
                "room_code": room.room_code,
                "room_type": room.room_type,
                "host": host_name,
                "players": counts.get(room.id, 0),
                "current_players": int(room.current_players or 0),
                "status": room.status,
                "game_started": bool(room.game_started),
                "created_at": room.created_at.isoformat() if room.created_at else None,
                "started_at": room.started_at.isoformat() if room.started_at else None,
                "ended_at": room.ended_at.isoformat() if room.ended_at else None,
                "duration_seconds": round(duration, 1) if duration is not None else None,
            }
        )
    return rows
