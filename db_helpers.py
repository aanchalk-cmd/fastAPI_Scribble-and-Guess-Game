from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from database import SessionLocal
from models import (
    GameRound,
    HostTransfer,
    Player,
    Room,
    RoomBan,
    RoomPlayer,
    Score,
    VoteKick,
    VoteKickVote,
    Word,
)


@contextmanager
def get_db_session():
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def get_or_create_player(db: Session, username: str) -> Player:
    player = db.query(Player).filter(Player.username == username).first()
    if player:
        player.last_seen_at = datetime.now(timezone.utc)
        return player

    player = Player(username=username)
    db.add(player)
    db.flush()
    return player


def create_room_record(
    db: Session,
    room_code: str,
    host_username: str,
    room_type: str,
    max_players: int,
    total_rounds: int,
    guest_id: Optional[str] = None,
) -> tuple[Room, Player]:
    host = get_or_create_player(db, host_username)
    room = Room(
        room_code=room_code,
        room_type=room_type,
        status="LOBBY",
        host_player_id=host.id,
        max_players=max_players,
        current_players=1,
        total_rounds=total_rounds,
        current_round_number=0,
        game_started=False,
    )
    db.add(room)
    db.flush()

    db.add(
        RoomPlayer(
            room_id=room.id,
            player_id=host.id,
            was_host=True,
            is_online=False,
            guest_id=guest_id,
        )
    )
    db.flush()
    return room, host


def join_room_record(
    db: Session,
    room_code: str,
    username: str,
    guest_id: Optional[str] = None,
) -> tuple[Optional[Room], Optional[Player]]:
    room = db.query(Room).filter(Room.room_code == room_code).first()
    if not room:
        return None, None

    player = get_or_create_player(db, username)
    existing = (
        db.query(RoomPlayer)
        .filter(RoomPlayer.room_id == room.id, RoomPlayer.player_id == player.id, RoomPlayer.left_at.is_(None))
        .first()
    )
    if not existing:
        db.add(
            RoomPlayer(
                room_id=room.id,
                player_id=player.id,
                was_host=False,
                is_online=False,
                guest_id=guest_id,
            )
        )
        db.flush()
    elif guest_id and existing.guest_id != guest_id:
        existing.guest_id = guest_id
        db.flush()

    room.current_players = (
        db.query(RoomPlayer)
        .filter(RoomPlayer.room_id == room.id, RoomPlayer.left_at.is_(None))
        .count()
    )
    db.flush()
    return room, player


def set_player_online(db: Session, room_db_id: int, player_db_id: int, online: bool):
    membership = (
        db.query(RoomPlayer)
        .filter(
            RoomPlayer.room_id == room_db_id,
            RoomPlayer.player_id == player_db_id,
            RoomPlayer.left_at.is_(None),
        )
        .first()
    )
    if membership:
        membership.is_online = online


def leave_room_record(db: Session, room_db_id: int, player_db_id: int, was_kicked: bool = False):
    membership = (
        db.query(RoomPlayer)
        .filter(
            RoomPlayer.room_id == room_db_id,
            RoomPlayer.player_id == player_db_id,
            RoomPlayer.left_at.is_(None),
        )
        .first()
    )
    if not membership:
        return

    membership.left_at = datetime.now(timezone.utc)
    membership.is_online = False
    membership.was_kicked = was_kicked

    room = db.query(Room).filter(Room.id == room_db_id).first()
    if room:
        room.current_players = (
            db.query(RoomPlayer)
            .filter(RoomPlayer.room_id == room_db_id, RoomPlayer.left_at.is_(None))
            .count()
        )


def start_game_record(db: Session, room_db_id: int):
    room = db.query(Room).filter(Room.id == room_db_id).first()
    if not room:
        return
    room.status = "PLAYING"
    room.game_started = True
    room.started_at = datetime.now(timezone.utc)


def end_room_record(db: Session, room_db_id: int):
    room = db.query(Room).filter(Room.id == room_db_id).first()
    if not room:
        return
    room.status = "ENDED"
    room.ended_at = datetime.now(timezone.utc)


def create_game_round(
    db: Session,
    room_db_id: int,
    round_number: int,
    drawer_player_id: int,
    round_duration_seconds: int,
) -> Optional[GameRound]:
    room = db.query(Room).filter(Room.id == room_db_id).first()
    if not room:
        return None

    game_round = GameRound(
        room_id=room_db_id,
        round_number=round_number,
        drawer_player_id=drawer_player_id,
        round_duration_seconds=round_duration_seconds,
    )
    db.add(game_round)
    room.current_round_number = round_number
    db.flush()
    return game_round


def get_or_create_word(db: Session, word_text: str) -> Word:
    normalized = word_text.strip().upper()
    word = db.query(Word).filter(Word.word == normalized).first()
    if word:
        return word

    word = Word(word=normalized, category="movie", difficulty="medium")
    db.add(word)
    db.flush()
    return word


def set_round_word(db: Session, round_db_id: int, word_text: str):
    game_round = db.query(GameRound).filter(GameRound.id == round_db_id).first()
    if not game_round:
        return
    word = get_or_create_word(db, word_text)
    game_round.word_id = word.id


def finish_game_round(
    db: Session,
    round_db_id: int,
    winner_player_id: Optional[int] = None,
):
    game_round = db.query(GameRound).filter(GameRound.id == round_db_id).first()
    if not game_round:
        return
    game_round.ended_at = datetime.now(timezone.utc)
    if winner_player_id:
        game_round.winner_player_id = winner_player_id


def record_score(
    db: Session,
    room_db_id: int,
    round_db_id: int,
    player_db_id: int,
    points: int,
):
    db.add(
        Score(
            room_id=room_db_id,
            round_id=round_db_id,
            player_id=player_db_id,
            points_earned=points,
        )
    )

    membership = (
        db.query(RoomPlayer)
        .filter(
            RoomPlayer.room_id == room_db_id,
            RoomPlayer.player_id == player_db_id,
            RoomPlayer.left_at.is_(None),
        )
        .first()
    )
    if membership:
        membership.final_score = (membership.final_score or 0) + points

    player = db.query(Player).filter(Player.id == player_db_id).first()
    if player:
        player.total_score = (player.total_score or 0) + points
        if membership:
            player.highest_score = max(player.highest_score or 0, membership.final_score)


def record_host_transfer(db: Session, room_db_id: int, old_host_id: int, new_host_id: int):
    room = db.query(Room).filter(Room.id == room_db_id).first()
    if room:
        room.host_player_id = new_host_id

    db.add(
        HostTransfer(
            room_id=room_db_id,
            old_host_id=old_host_id,
            new_host_id=new_host_id,
        )
    )


def start_vote_kick_record(
    db: Session,
    room_db_id: int,
    target_player_id: int,
    initiator_player_id: int,
) -> VoteKick:
    vote_kick = VoteKick(
        room_id=room_db_id,
        target_player_id=target_player_id,
        initiated_by=initiator_player_id,
        status="ACTIVE",
    )
    db.add(vote_kick)
    db.flush()
    return vote_kick


def record_vote_kick_vote(db: Session, vote_kick_db_id: int, player_db_id: int, vote_yes: bool):
    db.add(
        VoteKickVote(
            vote_kick_id=vote_kick_db_id,
            player_id=player_db_id,
            vote=vote_yes,
        )
    )


def resolve_vote_kick_record(db: Session, vote_kick_db_id: int, status: str):
    vote_kick = db.query(VoteKick).filter(VoteKick.id == vote_kick_db_id).first()
    if vote_kick:
        vote_kick.status = status


def is_guest_banned_from_room(db: Session, room_db_id: int, guest_id: str) -> bool:
    return (
        db.query(RoomBan)
        .filter(RoomBan.room_id == room_db_id, RoomBan.guest_id == guest_id)
        .first()
        is not None
    )


def load_room_bans(db: Session, room_db_id: int) -> set[str]:
    rows = db.query(RoomBan.guest_id).filter(RoomBan.room_id == room_db_id).all()
    return {row[0] for row in rows}


def ban_guest_from_room(
    db: Session,
    room_db_id: int,
    guest_id: str,
    banned_by_player_id: Optional[int] = None,
    reason: Optional[str] = None,
) -> RoomBan:
    existing = (
        db.query(RoomBan)
        .filter(RoomBan.room_id == room_db_id, RoomBan.guest_id == guest_id)
        .first()
    )
    if existing:
        return existing

    room_ban = RoomBan(
        room_id=room_db_id,
        guest_id=guest_id,
        banned_by_player_id=banned_by_player_id,
        reason=reason,
    )
    db.add(room_ban)
    db.flush()
    return room_ban


def get_guest_id_for_player(db: Session, room_db_id: int, player_db_id: int) -> Optional[str]:
    membership = (
        db.query(RoomPlayer)
        .filter(
            RoomPlayer.room_id == room_db_id,
            RoomPlayer.player_id == player_db_id,
            RoomPlayer.left_at.is_(None),
        )
        .first()
    )
    if membership and membership.guest_id:
        return membership.guest_id

    membership = (
        db.query(RoomPlayer)
        .filter(RoomPlayer.room_id == room_db_id, RoomPlayer.player_id == player_db_id)
        .order_by(RoomPlayer.joined_at.desc())
        .first()
    )
    return membership.guest_id if membership else None


def finalize_player_game_stats(db: Session, room_db_id: int, winner_player_id: Optional[int] = None):
    room = db.query(Room).filter(Room.id == room_db_id).first()
    if not room:
        return

    memberships = (
        db.query(RoomPlayer)
        .filter(RoomPlayer.room_id == room_db_id)
        .all()
    )
    for membership in memberships:
        player = db.query(Player).filter(Player.id == membership.player_id).first()
        if player:
            player.total_games_played += 1
            if winner_player_id and player.id == winner_player_id:
                player.total_wins += 1
