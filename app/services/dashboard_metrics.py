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

from models import GameRound, Player, Room, RoomPlayer, Score

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
    """All-time totals from every stored row. active_games is the only live count."""
    total_players = db.query(func.count(Player.id)).scalar() or 0
    total_rooms = db.query(func.count(Room.id)).scalar() or 0
    games_started = (
        db.query(func.count(Room.id)).filter(Room.game_started.is_(True)).scalar() or 0
    )
    games_finished = (
        db.query(func.count(Room.id)).filter(*_completed_games_filter()).scalar() or 0
    )
    active_games = (
        db.query(func.count(Room.id)).filter(Room.status == STATUS_PLAYING).scalar() or 0
    )
    total_rounds = db.query(func.count(GameRound.id)).scalar() or 0
    total_points = db.query(func.coalesce(func.sum(Score.points_earned), 0)).scalar() or 0
    total_joins = db.query(func.count(RoomPlayer.id)).scalar() or 0
    return {
        "total_players": int(total_players),
        "total_rooms": int(total_rooms),
        "games_started": int(games_started),
        "games_finished": int(games_finished),
        "total_games": int(games_started),
        "active_games": int(active_games),
        "total_rounds": int(total_rounds),
        "total_points": int(total_points),
        "total_joins": int(total_joins),
    }


def get_leaderboard(db: Session, limit: int = 100) -> list[dict[str, Any]]:
    """
    Rank every player from stored rows.

    Lifetime counters on players are unused by the game, so games, wins,
    and points are counted from room memberships, round winners, and scores.
    """
    limit = max(1, min(int(limit or 100), 200))

    games_sq = (
        db.query(
            RoomPlayer.player_id.label("player_id"),
            func.count(func.distinct(RoomPlayer.room_id)).label("games"),
        )
        .join(Room, Room.id == RoomPlayer.room_id)
        .filter(Room.game_started.is_(True))
        .group_by(RoomPlayer.player_id)
        .subquery()
    )
    wins_sq = (
        db.query(
            GameRound.winner_player_id.label("player_id"),
            func.count(GameRound.id).label("wins"),
        )
        .filter(GameRound.winner_player_id.isnot(None))
        .group_by(GameRound.winner_player_id)
        .subquery()
    )
    points_sq = (
        db.query(
            Score.player_id.label("player_id"),
            func.coalesce(func.sum(Score.points_earned), 0).label("points"),
        )
        .group_by(Score.player_id)
        .subquery()
    )
    high_sq = (
        db.query(
            RoomPlayer.player_id.label("player_id"),
            func.coalesce(func.max(RoomPlayer.final_score), 0).label("high"),
        )
        .group_by(RoomPlayer.player_id)
        .subquery()
    )

    games = func.coalesce(games_sq.c.games, 0)
    wins = func.coalesce(wins_sq.c.wins, 0)
    points = func.coalesce(points_sq.c.points, 0)
    high = func.coalesce(high_sq.c.high, 0)

    ranked = (
        db.query(Player.id, Player.username, games, wins, points, high)
        .outerjoin(games_sq, games_sq.c.player_id == Player.id)
        .outerjoin(wins_sq, wins_sq.c.player_id == Player.id)
        .outerjoin(points_sq, points_sq.c.player_id == Player.id)
        .outerjoin(high_sq, high_sq.c.player_id == Player.id)
        .order_by(points.desc(), wins.desc(), games.desc(), Player.username.asc())
        .limit(limit)
        .all()
    )

    rows: list[dict[str, Any]] = []
    for rank, (player_id, username, game_count, win_count, score, highest) in enumerate(
        ranked, start=1
    ):
        game_count = int(game_count or 0)
        win_count = int(win_count or 0)
        win_rate = round((win_count / game_count) * 100, 1) if game_count > 0 else 0.0
        rows.append(
            {
                "rank": rank,
                "player_id": player_id,
                "username": username,
                "games_played": game_count,
                "wins": win_count,
                "total_score": int(score or 0),
                "highest_score": int(highest or 0),
                "win_rate": win_rate,
            }
        )
    return rows


def _activity_window(db: Session, range_key: str) -> tuple[str, datetime, str]:
    """Return (range label, inclusive UTC start, bucket unit)."""
    now = _utcnow()
    key = str(range_key or "all").lower()
    if key in {"30d", "30", "month"}:
        start = (now - timedelta(days=29)).replace(hour=0, minute=0, second=0, microsecond=0)
        return "30d", start, "day"
    if key in {"7d", "7"}:
        start = (now - timedelta(days=6)).replace(hour=0, minute=0, second=0, microsecond=0)
        return "7d", start, "day"

    first = db.query(func.min(Room.created_at)).scalar()
    start = _as_aware(first) or now
    start = start.replace(hour=0, minute=0, second=0, microsecond=0)
    span_days = max(1, (now.date() - start.date()).days + 1)
    if span_days <= 90:
        unit = "day"
    elif span_days <= 730:
        unit = "week"
    else:
        unit = "month"
    return "all", start, unit


def _bucket_key(dt: datetime, unit: str) -> str:
    dt = _as_aware(dt)
    if unit == "month":
        return dt.strftime("%Y-%m")
    if unit == "week":
        start = dt - timedelta(days=dt.weekday())
        return start.strftime("%Y-%m-%d")
    return dt.strftime("%Y-%m-%d")


def _bucket_labels(start: datetime, unit: str) -> list[str]:
    now = _utcnow()
    labels: list[str] = []
    cursor = _as_aware(start)
    if unit == "month":
        cursor = cursor.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        while cursor.date() <= now.date():
            labels.append(cursor.strftime("%Y-%m"))
            month = 1 if cursor.month == 12 else cursor.month + 1
            year = cursor.year + 1 if cursor.month == 12 else cursor.year
            cursor = cursor.replace(year=year, month=month)
        return labels
    if unit == "week":
        cursor = cursor - timedelta(days=cursor.weekday())
        cursor = cursor.replace(hour=0, minute=0, second=0, microsecond=0)
        step = timedelta(days=7)
    else:
        cursor = cursor.replace(hour=0, minute=0, second=0, microsecond=0)
        step = timedelta(days=1)
    while cursor.date() <= now.date():
        labels.append(cursor.strftime("%Y-%m-%d"))
        cursor = cursor + step
    return labels


def _count_by_bucket(timestamps: list, labels: list[str], unit: str) -> list[int]:
    counts = {label: 0 for label in labels}
    for (ts,) in timestamps:
        if ts is None:
            continue
        key = _bucket_key(ts, unit)
        if key in counts:
            counts[key] += 1
    return [counts[label] for label in labels]


def get_game_activity(db: Session, range_key: str = "all") -> dict[str, Any]:
    """
    Activity across stored history.

    rooms_created: every room row
    games_started: every room that actually started
    rounds_played: every game_rounds row
    """
    range_label, start, unit = _activity_window(db, range_key)
    labels = _bucket_labels(start, unit)

    created_rows = (
        db.query(Room.created_at)
        .filter(Room.created_at.isnot(None), Room.created_at >= start)
        .all()
    )
    started_rows = (
        db.query(Room.started_at)
        .filter(Room.started_at.isnot(None), Room.started_at >= start)
        .all()
    )
    round_rows = (
        db.query(GameRound.started_at)
        .filter(GameRound.started_at.isnot(None), GameRound.started_at >= start)
        .all()
    )

    rooms_created = _count_by_bucket(created_rows, labels, unit)
    games_started = _count_by_bucket(started_rows, labels, unit)
    rounds_played = _count_by_bucket(round_rows, labels, unit)

    return {
        "range": range_label,
        "bucket": unit,
        "labels": labels,
        "rooms_created": rooms_created,
        "games_started": games_started,
        "rounds_played": rounds_played,
        "games_played": rounds_played,
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
    total_rooms = int(db.query(func.count(Room.id)).scalar() or 0)
    games_started = int(
        db.query(func.count(Room.id)).filter(Room.game_started.is_(True)).scalar() or 0
    )
    completed_count = int(
        db.query(func.count(Room.id)).filter(*_completed_games_filter()).scalar() or 0
    )
    total_rounds = int(db.query(func.count(GameRound.id)).scalar() or 0)
    total_points = int(db.query(func.coalesce(func.sum(Score.points_earned), 0)).scalar() or 0)

    avg_duration_raw = (
        db.query(func.avg(extract("epoch", Room.ended_at - Room.started_at)))
        .filter(Room.started_at.isnot(None), Room.ended_at.isnot(None))
        .scalar()
    )
    avg_duration_seconds: Optional[float] = (
        round(float(avg_duration_raw), 1) if avg_duration_raw is not None else None
    )

    per_room = (
        db.query(func.count(RoomPlayer.id).label("pc"))
        .select_from(Room)
        .outerjoin(RoomPlayer, RoomPlayer.room_id == Room.id)
        .filter(Room.game_started.is_(True))
        .group_by(Room.id)
        .subquery()
    )
    avg_players_raw = db.query(func.avg(per_room.c.pc)).scalar()
    avg_players: Optional[float] = (
        round(float(avg_players_raw), 2) if avg_players_raw is not None else None
    )

    never_started = int(
        db.query(func.count(Room.id)).filter(Room.game_started.is_(False)).scalar() or 0
    )
    never_started_rate: Optional[float] = (
        round((never_started / total_rooms) * 100, 1) if total_rooms > 0 else None
    )

    return {
        "average_game_duration_seconds": avg_duration_seconds,
        "average_players_per_game": avg_players,
        "completed_games": completed_count,
        "games_started": games_started,
        "total_rounds": total_rounds,
        "total_points": total_points,
        "abandoned_before_start": never_started,
        "abandoned_rate": never_started_rate,
        "mid_game_abandon_available": False,
        "notes": (
            "Counts use every stored room, round, and score. "
            "Duration averages every room that has a start and an end. "
            "Never started is rooms that were created and never left the lobby."
        ),
    }


def get_recent_games(db: Session, limit: int = 100) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit or 100), 200))

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

    round_rows = (
        db.query(GameRound.room_id, func.count(GameRound.id))
        .filter(GameRound.room_id.in_(room_ids))
        .group_by(GameRound.room_id)
        .all()
    )
    round_counts = {room_id: int(count) for room_id, count in round_rows}

    point_rows = (
        db.query(Score.room_id, func.coalesce(func.sum(Score.points_earned), 0))
        .filter(Score.room_id.in_(room_ids))
        .group_by(Score.room_id)
        .all()
    )
    point_totals = {room_id: int(total) for room_id, total in point_rows}

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
                "rounds": round_counts.get(room.id, 0),
                "points": point_totals.get(room.id, 0),
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
