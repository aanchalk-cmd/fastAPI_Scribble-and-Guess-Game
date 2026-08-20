import random
import asyncio
import time
import string
import uuid
import fakeredis
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, Form, Cookie
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from typing import List, Dict, Optional, Set
from fastapi.responses import RedirectResponse

from database import init_db
from app.services.word_manager import (
    CategoryNotFoundError,
    word_manager,
)
from db_helpers import (
    ban_guest_from_room,
    create_game_round,
    create_room_record,
    end_room_record,
    finalize_player_game_stats,
    finish_game_round,
    get_db_session,
    get_guest_id_for_player,
    get_or_create_player,
    is_guest_banned_from_room,
    join_room_record,
    leave_room_record,
    load_room_bans,
    record_host_transfer,
    record_score,
    record_vote_kick_vote,
    resolve_vote_kick_record,
    set_player_online,
    set_round_word,
    start_game_record,
    start_vote_kick_record,
)

app = FastAPI()
r = fakeredis.FakeRedis(decode_responses=True)
templates = Jinja2Templates(directory="templates")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Updated Room Model in main.py
class GameRoom:
    def __init__(
        self,
        room_id: str,
        host: str,
        room_type: str,
        max_players: int,
        duration: int = 5,
        total_rounds: int = 3,
        category: str = "pictionary",
        db_id: Optional[int] = None,
    ):
        self.room_id = room_id
        self.host = host
        self.room_type = room_type
        self.max_players = max_players
        self.duration = duration
        self.db_id = db_id
        self.player_db_ids: Dict[str, int] = {}
        self.player_guest_ids: Dict[str, str] = {}
        self.banned_guest_ids: Set[str] = set()
        self.players: List[str] = []
        self.status = "LOBBY"  # Status: LOBBY, PLAYING
        self.game_started = False
        self.lobby_auto_start_deadline: Optional[float] = None
        # Word category for this room (from words.json via WordManager)
        self.category = word_manager.normalize_category(category)
        # Validate: only 1, 3, or 5 rounds allowed
        if total_rounds not in [1, 3, 5]:
            total_rounds = 3  # Default to 3 if invalid
        self.total_rounds = total_rounds
        self.current_round = 0
        # Pass the duration and rounds to the manager!
        self.manager = ConnectionManager(duration_mins=duration, room=self, room_id=room_id, total_rounds=self.total_rounds)

    def register_player(self, name: str, player_db_id: int):
        self.player_db_ids[name] = player_db_id

    def register_player_guest(self, name: str, guest_id: str):
        self.player_guest_ids[name] = guest_id

    def get_player_db_id(self, name: str) -> Optional[int]:
        return self.player_db_ids.get(name)

    def get_player_guest_id(self, name: str) -> Optional[str]:
        return self.player_guest_ids.get(name)

    def ban_guest(self, guest_id: str):
        self.banned_guest_ids.add(guest_id)

    def is_guest_banned(self, guest_id: str) -> bool:
        return guest_id in self.banned_guest_ids

    def load_bans_from_db(self):
        if not self.db_id:
            return
        with get_db_session() as db:
            self.banned_guest_ids = load_room_bans(db, self.db_id)

    def is_full(self):
        return len(self.players) >= self.max_players

    def available_slots(self) -> int:
        return max(0, self.max_players - len(self.players))

    def add_player(self, name: str):
        if not self.is_full():
            self.players.append(name)
            return True
        return False

    def remove_player(self, name: str):
        if name in self.players:
            self.players.remove(name)

    def should_start_game(self):
        return self.room_type == "public" and self.is_full()
    
    def transfer_host(self):
        """Transfers host role to another player. Returns new host name or None if no players left."""
        if not self.players:
            return None
        
        if self.host in self.players:
            self.players.remove(self.host)
        
        if not self.players:
            return None
        
        new_host = self.players[0]
        self.host = new_host
        return new_host



public_rooms: Dict[str, GameRoom] = {} 
lobby_connections: List[WebSocket] = [] 
public_room_timers: Dict[str, asyncio.Task] = {}
private_room_timers: Dict[str, asyncio.Task] = {}

LOBBY_AUTO_START_SECONDS = 300  # 5 minutes


def is_wait_lobby_eligible(room: GameRoom) -> bool:
    """
    Public wait lobby eligibility:
    - Public LOBBY rooms (waiting to start) with at least 1 player
    - Public PLAYING rooms that still have vacant slots (mid-game join)
    Private rooms never appear.
    """
    if room.room_type != "public":
        return False
    if len(room.players) <= 0:
        return False
    if room.status == "LOBBY":
        return True
    if room.status == "PLAYING" and not room.is_full():
        return True
    return False


def wait_lobby_skip_reason(room: GameRoom) -> Optional[str]:
    """Return why a room is skipped from the public wait lobby, or None if eligible."""
    if room.room_type != "public":
        return "private room"
    if len(room.players) <= 0:
        return "Room destroyed"
    if room.status == "PLAYING" and room.is_full():
        return "already full"
    if room.status not in ("LOBBY", "PLAYING"):
        return "Game already finished"
    if not is_wait_lobby_eligible(room):
        return "not eligible"
    return None


def log_room_state(room: GameRoom, in_wait_lobby: Optional[bool] = None):
    if in_wait_lobby is None:
        in_wait_lobby = is_wait_lobby_eligible(room)
    print("[STATE]")
    print(f"Room: {room.room_id}")
    print(f"Game Started: {room.game_started}")
    print(f"Players: {room.players}")
    print(f"Max Players: {room.max_players}")
    print(f"Available Slots: {room.available_slots()}")
    print(f"Public: {room.room_type == 'public'}")
    print(f"In Wait Lobby: {in_wait_lobby}")
    print(f"Status: {room.status}")


def log_wait_lobby_eligibility(room: GameRoom):
    if is_wait_lobby_eligible(room):
        print(f"[WAIT_LOBBY] Room eligible for public join")
        if room.game_started and room.status == "PLAYING":
            print(f"[WAIT_LOBBY] Re-added running room {room.room_id}")
            print(f"Available Slots={room.available_slots()}")
            print(f"Current Players={len(room.players)}")
            print(f"Max Players={room.max_players}")
            print(f"Game State=RUNNING")
    else:
        reason = wait_lobby_skip_reason(room) or "unknown"
        print(f"[WAIT_LOBBY] Room NOT eligible")
        print(f"Reason: {reason}")
        if reason == "private room":
            print("[SKIP] Room is private")
        elif reason == "already full":
            print("[SKIP] Room already full")
        elif reason == "Game already finished":
            print("[SKIP] Game already finished")
        elif reason == "Room destroyed":
            print("[SKIP] Room destroyed")
        else:
            print(f"[SKIP] {reason}")


@app.on_event("startup")
def startup_event():
    init_db()
    print("[DB] Tables initialized")
    # Reload words.json into memory once at startup (WordManager is thread-safe).
    word_manager.load()
    print(f"[WORD_MANAGER] Categories ready: {word_manager.get_categories()}")


def validate_guest_id(guest_id: Optional[str]) -> Optional[str]:
    if not guest_id:
        return None
    try:
        return str(uuid.UUID(guest_id.strip()))
    except (ValueError, AttributeError):
        return None


def ensure_guest_id(guest_id: Optional[str]) -> str:
    validated = validate_guest_id(guest_id)
    return validated if validated else str(uuid.uuid4())


def is_guest_banned_in_room(room: GameRoom, guest_id: str) -> bool:
    if room.is_guest_banned(guest_id):
        return True
    if room.db_id:
        with get_db_session() as db:
            if is_guest_banned_from_room(db, room.db_id, guest_id):
                room.ban_guest(guest_id)
                return True
    return False


@app.post("/join")
async def join(
    name: str = Form(...),
    room_code: str = Form(None),
    action: str = Form(...),
    room_type: str = Form("private"),  
    max_players: int = Form(6),
    duration: int = Form(5),
    rounds: int = Form(3),  # New: rounds selection
    category: str = Form("pictionary"),  # Word category from words.json
    guest_id: str = Form(None),
):
    guest_id = ensure_guest_id(guest_id)
    print(f"[MATCHMAKING] Action={action} user={name} room_type={room_type} rounds={rounds} category={category} guest={guest_id}")
    if action == "create":
        max_players = max(2, min(10, max_players))
        # Validate: only 1, 3, or 5 rounds allowed
        if rounds not in [1, 3, 5]:
            rounds = 3  # Default to 3 if invalid

        # Normalize / fall back if client sent an unknown category
        category = word_manager.normalize_category(category)
        
        room_code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))

        room = GameRoom(
            room_id=room_code,
            host=name,
            room_type=room_type,
            max_players=max_players,
            duration=duration,
            total_rounds=rounds,  # Pass rounds to GameRoom
            category=category,
        )

        room.add_player(name)

        with get_db_session() as db:
            db_room, host_player = create_room_record(
                db,
                room_code=room_code,
                host_username=name,
                room_type=room_type,
                max_players=max_players,
                total_rounds=rounds,
                guest_id=guest_id,
            )
            room.db_id = db_room.id
            room.register_player(name, host_player.id)
            room.register_player_guest(name, guest_id)

        rooms[room_code] = room
        print(f"[ROOM] Created room={room_code}")
        print(f"[ROOM] Public={room_type == 'public'}")
        print(f"[ROOM] Max Players={max_players}")
        print(f"[ROOM] Category={room.category}")
        print(f"[ROOM] Current players={room.players}")
        log_room_state(room)

        if room_type == "public":
            print(f"[WAIT_LOBBY] Added room {room_code}")

            # Start auto-discard timer
            timer_task = asyncio.create_task(start_public_room_timer(room_code))
            public_room_timers[room_code] = timer_task

            print(f"[AUTO-DISCARD] 5-minute timer initialized for room: {room_code}")

            await broadcast_lobby_update()
        elif room_type == "private":
            print(f"[SKIP] Room is private")
            print(f"[WAIT_LOBBY] Room NOT eligible")
            print(f"Reason: private room")
            room.lobby_auto_start_deadline = time.time() + LOBBY_AUTO_START_SECONDS
            timer_task = asyncio.create_task(start_private_room_auto_start_timer(room_code))
            private_room_timers[room_code] = timer_task
            print(f"[AUTO-START] 5-minute auto-start timer initialized for private room: {room_code}")

    elif action == "join":
        if not room_code:
            return RedirectResponse(url="/?error=missing_code", status_code=303)

        room_code = room_code.upper().strip()
        print(f"[PLAYER_JOIN] Attempting join room={room_code} user={name}")

        if room_code not in rooms:
            print(f"[SKIP] Room destroyed")
            print(f"[PLAYER_JOIN] Failed: room {room_code} not found")
            return RedirectResponse(url=f"/?error=not_found&code={room_code}", status_code=303)

        room = rooms[room_code]
        players_before = len(room.players)
        print(f"[PLAYER_JOIN] Target room={room_code} type={room.room_type} status={room.status} game_started={room.game_started}")
        print(f"[PLAYER_JOIN] Players before={players_before}/{room.max_players} slots={room.available_slots()}")

        if is_guest_banned_in_room(room, guest_id):
            print(f"[PLAYER_JOIN] Failed: guest {guest_id} banned from room {room_code}")
            return RedirectResponse(
                url=f"/?error=banned&code={room_code}",
                status_code=303,
            )

        # Capacity check for both private and public (including mid-game public joins)
        if room.is_full():
            print(f"[SKIP] Room already full")
            print(f"[PLAYER_JOIN] Failed: room {room_code} is full")
            return RedirectResponse(url=f"/?error=full&code={room_code}", status_code=303)

        joining_running = room.game_started and room.status == "PLAYING"
        name = get_unique_name(name, room.players)
        added = room.add_player(name)
        if not added:
            print(f"[SKIP] Room already full")
            print(f"[PLAYER_JOIN] Failed: add_player rejected for {room_code}")
            return RedirectResponse(url=f"/?error=full&code={room_code}", status_code=303)

        with get_db_session() as db:
            db_room, player = join_room_record(db, room_code, name, guest_id)
            if db_room and player:
                if room.db_id is None:
                    room.db_id = db_room.id
                    room.load_bans_from_db()
                room.register_player(name, player.id)
                room.register_player_guest(name, guest_id)

        players_after = len(room.players)
        if joining_running:
            print(f"[PLAYER_JOIN] {name} joined running room {room_code}")
        else:
            print(f"[PLAYER_JOIN] {name} joined room {room_code}")
        print(f"[PLAYER_JOIN] Players: {players_before} -> {players_after}")
        print(f"[PLAYER_JOIN] Available slots={room.available_slots()}")

        # Cancel auto-discard timer if another player joined (pre-start public only)
        if (
            room.room_type == "public"
            and not room.game_started
            and len(room.players) >= 2
            and room_code in public_room_timers
        ):
            print(f"[AUTO-DISCARD] Player joined. Cancelling timer for room: {room_code}")

            public_room_timers[room_code].cancel()
            del public_room_timers[room_code]

        if room.room_type == "public" and room.is_full():
            print(f"[WAIT_LOBBY] Room full again")
            print(f"[WAIT_LOBBY] Removing from wait lobby")
            print(f"[WAIT_LOBBY] Removed room {room_code}")

        log_room_state(room)
        await broadcast_lobby_update()

    response = RedirectResponse(url="/game", status_code=303)
    response.set_cookie("username", name)
    response.set_cookie("room_id", room_code)
    response.set_cookie("guest_id", guest_id, max_age=31536000)
    return response

@app.get("/leave")
async def leave(username: str = Cookie(None), room_id: str = Cookie(None)):
    if room_id in rooms and username:
        room = rooms[room_id]
        manager = room.manager
        print(f"[PLAYER_LEAVE] Player {username} leaving room {room_id} via /leave")
        print(f"[PLAYER_LEAVE] Remaining players before remove={len(room.players)}")
        print(f"[PLAYER_LEAVE] Max players={room.max_players}")

        await manager.handle_voluntary_leave(username)

        player_db_id = room.get_player_db_id(username)
        if room.db_id and player_db_id:
            with get_db_session() as db:
                leave_room_record(db, room.db_id, player_db_id)

        room.remove_player(username)
        print(f"[PLAYER_LEAVE] Player {username} left room {room_id}")
        print(f"[PLAYER_LEAVE] Remaining players={len(room.players)}")
        print(f"[PLAYER_LEAVE] Max players={room.max_players}")
        log_wait_lobby_eligibility(room)
        log_room_state(room)
        
        if not manager.active_connections:
            print(f"[SKIP] Room destroyed")
            print(f"[WAIT_LOBBY] Removed room {room_id}")
            cancel_private_room_timer(room_id)
            if room.db_id:
                with get_db_session() as db:
                    end_room_record(db, room.db_id)
            del rooms[room_id]
            if room_id in public_rooms:
                del public_rooms[room_id]
            await broadcast_lobby_update()
        else:
            # Running public rooms with a free slot reappear in wait lobby via broadcast
            await broadcast_lobby_update()

    response = RedirectResponse(url="/", status_code=303)
    response.delete_cookie("room_id")
    return response

@app.on_event("shutdown")
def shutdown_event():
    r.delete("round_end_time")


# CSV movie loader removed — words now come from app/data/words.json via WordManager.


class ConnectionManager:
    def __init__(self, duration_mins=5, room=None, room_id=None, total_rounds=3): 
        self.active_connections: Dict[str, WebSocket] = {}
        self.ws_to_name: Dict[int, str] = {}
        self.draw_history: List[dict] = []
        self.round_duration = duration_mins * 60
        self.movie_history: List[str] = []
        self.history_recorded_for_round = False
        
        # Room reference for vote kick operations
        self.room = room
        self.room_id = room_id
        
        # Rounds tracking - validate: only 1, 3, or 5 rounds allowed
        if total_rounds not in [1, 3, 5]:
            total_rounds = 3  # Default to 3 if invalid
        self.total_rounds = total_rounds
        self.current_round = 0
        self.game_complete = False  # True after all configured rounds finish
        self.drawer_queue = []  # Fair rotation queue
        self.drawer_queue_index = 0
        
        self.round_timer_task = None
        self.selection_timer_task = None
        
        # Vote Kick State
        self.active_vote_kick = None  # Will store: {target_player, initiator, votes_yes, votes_no, voters, timeout_task}
        self.vote_kick_timeout = 15  # seconds
        self.active_vote_kick_db_id: Optional[int] = None
        self.current_db_round_id: Optional[int] = None

        self.game_state = {
            "movie": "",
            "display_name": "",
            "drawer_assigned": False,
            "drawer_name": None,
            "is_round_active": False,
            "is_selecting": False,
            "selection_active": False,
            "selection_end_time": None,
            "winner_announcement": None,
            "revealed_movie": None,
            "show_vowels": True   
        }

    def get_player_score(self, name: str):
        score = r.get(f"score:{self.room_id}:{name}")
        return int(score) if score else 0
    def get_round(self):
        round_no = r.get(f"round:{id(self)}")
        return int(round_no) if round_no is not None else 0

    def increment_round(self):
        current = self.get_round()
        r.set(f"round:{id(self)}", current + 1)

    def reset_round(self):
        r.set(f"round:{id(self)}", 0)

    def initialize_drawer_queue(self, player_names: List[str]):
        """Initialize or rebuild the drawer rotation queue for fair rotation."""
        self.drawer_queue = player_names.copy()
        self.drawer_queue_index = 0
        print(f"[DEBUG-ROUNDS] Initialized drawer queue: {self.drawer_queue}")

    def remove_from_drawer_queue(self, player_name: str):
        """Remove a disconnected or kicked player from the rotation queue."""
        if player_name not in self.drawer_queue:
            return

        removed_index = self.drawer_queue.index(player_name)
        self.drawer_queue.remove(player_name)

        if removed_index < self.drawer_queue_index:
            self.drawer_queue_index = max(0, self.drawer_queue_index - 1)
        if self.drawer_queue and self.drawer_queue_index >= len(self.drawer_queue):
            self.drawer_queue_index = 0

        print(
            f"[DEBUG-ROUNDS] Removed {player_name} from drawer queue. "
            f"Queue: {self.drawer_queue}"
        )

    def get_next_drawer(self):
        """Get the next drawer in fair rotation order, skipping inactive players."""
        if not self.drawer_queue:
            return None

        active_players = set(self.active_connections.keys())
        attempts = len(self.drawer_queue)

        while attempts > 0:
            if self.drawer_queue_index >= len(self.drawer_queue):
                self.drawer_queue_index = 0

            next_drawer = self.drawer_queue[self.drawer_queue_index]
            self.drawer_queue_index += 1
            attempts -= 1

            if next_drawer in active_players:
                print(
                    f"[DEBUG-ROUNDS] Next drawer: {next_drawer} "
                    f"(index {self.drawer_queue_index - 1}/{len(self.drawer_queue)})"
                )
                return next_drawer

            print(f"[DEBUG-ROUNDS] Skipping inactive drawer: {next_drawer}")

        if active_players:
            fallback = random.choice(list(active_players))
            print(f"[DEBUG-ROUNDS] No valid drawer in queue, fallback: {fallback}")
            return fallback

        return None

    def set_player_score(self, name: str, points: int):
        current_score = self.get_player_score(name)
        new_score = current_score + points
        r.set(f"score:{self.room_id}:{name}", new_score)

        if not self.room or not self.room.db_id:
            return

        player_db_id = self.room.get_player_db_id(name)
        if player_db_id and self.current_db_round_id:
            with get_db_session() as db:
                record_score(
                    db,
                    self.room.db_id,
                    self.current_db_round_id,
                    player_db_id,
                    points,
                )

    def persist_round_word(self, movie: str):
        if self.current_db_round_id and movie:
            with get_db_session() as db:
                set_round_word(db, self.current_db_round_id, movie)

    async def record_current_movie_history(self):
        movie = self.game_state.get("movie")
        if not movie or self.history_recorded_for_round:
            return

        self.movie_history.append(movie)
        self.history_recorded_for_round = True
        await self.broadcast({
            "type": "history_update",
            "history": self.movie_history,
        })

    def finish_current_round(self, winner_name: Optional[str] = None):
        if not self.current_db_round_id or not self.room:
            return

        winner_id = self.room.get_player_db_id(winner_name) if winner_name else None
        with get_db_session() as db:
            finish_game_round(db, self.current_db_round_id, winner_id)
        self.current_db_round_id = None

    def get_remaining_time(self):
        """Calculates remaining seconds based on the end_time stored in Redis"""
        end_time = r.get(f"round_end_time:{id(self)}") or r.get("round_end_time")
        if end_time:
            remaining = int(float(end_time) - time.time())
            return max(0, remaining)
        return 0

    def get_selection_time_left(self):
        selection_end = r.get(f"selection_end_time:{id(self)}")
        if selection_end:
            remaining = int(float(selection_end) - time.time())
            return max(0, remaining)
        return 0

    def cancel_selection_timer(self):
        if self.selection_timer_task:
            self.selection_timer_task.cancel()
            self.selection_timer_task = None
        self.game_state["selection_active"] = False
        self.game_state["selection_end_time"] = None
        r.delete(f"selection_end_time:{id(self)}")
        r.delete(f"selection_drawer:{id(self)}")

    async def reassign_drawer_after_removal(self):
        """Pick a new drawer from active players without advancing the round."""
        player_names = list(self.active_connections.keys())
        if not player_names:
            self.game_state["drawer_assigned"] = False
            self.game_state["drawer_name"] = None
            return

        self.cancel_selection_timer()
        if self.round_timer_task:
            self.round_timer_task.cancel()
            self.round_timer_task = None

        r.delete("round_end_time")
        self.game_state.update({
            "movie": "",
            "display_name": "",
            "is_round_active": False,
            "winner_announcement": None,
            "revealed_movie": None,
        })
        self.draw_history = []

        new_drawer_name = self.get_next_drawer()
        if not new_drawer_name or new_drawer_name not in player_names:
            new_drawer_name = random.choice(player_names)

        self.game_state["drawer_name"] = new_drawer_name
        self.game_state["drawer_assigned"] = True
        self.game_state["is_selecting"] = True

        print(f"[DEBUG-ROUNDS] Reassigned drawer after removal: {new_drawer_name}")

        await self.start_selection_timer()

        for name, ws in self.active_connections.items():
            role = "drawer" if name == new_drawer_name else "guesser"
            await ws.send_json({
                "type": "init",
                "role": role,
                "round_number": self.current_round,
                "total_rounds": self.total_rounds,
                "movie_set": False,
                "drawer_name": new_drawer_name,
                "selection_active": True,
                "selection_time_left": self.get_selection_time_left(),
            })

        await self.broadcast({
            "type": "new_drawer",
            "drawer_name": new_drawer_name,
            "message": f"Drawer changed. New drawer: {new_drawer_name}.",
        })

        # Auto-send 3 word options to the new drawer from the room category
        await self.send_word_options_to_drawer()

    async def handle_selection_expiry(self):
        
        if self.game_state.get("movie"):
            return

        old_drawer = self.game_state.get("drawer_name")

        player_names = list(self.active_connections.keys())
        if not player_names:
            return

        
        if len(player_names) > 1 and old_drawer in player_names:
            idx = player_names.index(old_drawer)
            new_drawer = player_names[(idx + 1) % len(player_names)]
        else:
            new_drawer = random.choice(player_names)

        
        self.game_state.update({
            "drawer_name": new_drawer,
            "drawer_assigned": True,
            "movie": "",
            "display_name": "",
            "is_round_active": False
        })

        
        await self.broadcast({
            "type": "new_drawer",
            "drawer_name": new_drawer,
            "message": f"⏱️ Time's up for {old_drawer}. New drawer: {new_drawer}."
        })

        await self.broadcast({
            "type": "player_list",
            "players": self.get_player_data()
        })

        
        await self.start_selection_timer()
        # New drawer's turn — offer three words from the room category
        await self.send_word_options_to_drawer()

    async def start_selection_timer(self):
        if self.selection_timer_task:
            self.selection_timer_task.cancel()

        self.game_state["selection_active"] = True
        end_timestamp = time.time() + 60
        self.game_state["selection_end_time"] = end_timestamp
        r.set(f"selection_end_time:{id(self)}", end_timestamp)
        r.set(f"selection_drawer:{id(self)}", self.game_state.get("drawer_name", ""))

        
        async def selection_timer():
            try:
                while True:
                    remaining = self.get_selection_time_left()
                    
                    await self.broadcast({
                        "type": "timer_update",
                        "timer_type": "selection",
                        "time_left": remaining,
                        "drawer_name": self.game_state.get("drawer_name")
                    })

                    if remaining <= 0:
                        break
                    await asyncio.sleep(1)

                
                self.game_state["selection_end_time"] = None
                r.delete(f"selection_end_time:{id(self)}")
                r.delete(f"selection_drawer:{id(self)}")

                await self.handle_selection_expiry()
            except asyncio.CancelledError:
                
                pass

        self.selection_timer_task = asyncio.create_task(selection_timer())

    def get_room_category(self) -> str:
        """Return the word category selected for this room."""
        if self.room and getattr(self.room, "category", None):
            return self.room.category
        return "pictionary"

    async def send_word_options_to_drawer(self, count: int = 3):
        """
        Fetch `count` random words from the room category and send them
        only to the current drawer (not broadcast to guessers).
        """
        drawer_name = self.game_state.get("drawer_name")
        if not drawer_name or drawer_name not in self.active_connections:
            print("[WORD_MANAGER] No active drawer to send word options")
            return

        category = self.get_room_category()
        try:
            options = word_manager.get_random_words(category, count=count)
        except CategoryNotFoundError as e:
            print(f"[WORD_MANAGER] {e}")
            options = word_manager.get_random_words(
                word_manager.normalize_category(None), count=count
            )

        print(
            f"[WORD_MANAGER] Sending {len(options)} options to drawer={drawer_name} "
            f"category={category}: {options}"
        )

        payload = {
            "type": "movie_options",  # keep existing frontend event name
            "options": options,
            "category": category,
        }
        try:
            await self.active_connections[drawer_name].send_json(payload)
        except Exception as e:
            print(f"[WORD_MANAGER] Failed to send options to {drawer_name}: {e}")

    def get_player_data(self):
        players = [{"name": name, "score": self.get_player_score(name)} 
                   for name in self.active_connections.keys()]
        return sorted(players, key=lambda x: x['score'], reverse=True)

    async def connect(self, websocket: WebSocket, name: str, guest_id: Optional[str] = None):
        original_name = name
        name = get_unique_name(name, self.active_connections.keys())

        await websocket.accept()
        ws_id = id(websocket)
        self.active_connections[name] = websocket
        self.ws_to_name[ws_id] = name
        print(f"[WEBSOCKET] Player {name} connected. Total connections: {len(self.active_connections)}")

        if name != original_name:
            await websocket.send_json({
                "type": "name_updated",
                "new_name": name
            })
        
        if r.get(f"score:{self.room_id}:{name}") is None:
            r.set(f"score:{self.room_id}:{name}", 0)

        if guest_id and self.room:
            self.room.register_player_guest(name, guest_id)

        if self.room and self.room.db_id:
            with get_db_session() as db:
                player_db_id = self.room.get_player_db_id(name)
                if not player_db_id:
                    player = get_or_create_player(db, name)
                    player_db_id = player.id
                    self.room.register_player(name, player_db_id)
                    join_room_record(db, self.room.room_id, name, guest_id)
                elif guest_id:
                    join_room_record(db, self.room.room_id, name, guest_id)
                set_player_online(db, self.room.db_id, player_db_id, True)

        # Mid-game joiners: append to fair drawer rotation without resetting the game
        if self.room and self.room.game_started and self.drawer_queue:
            if name not in self.drawer_queue:
                self.drawer_queue.append(name)
                print(f"[GAME] Mid-game joiner {name} appended to drawer queue: {self.drawer_queue}")
        
        # Only assign drawer if game has already started
        role = "guesser"
        if self.game_state["drawer_assigned"] and name == self.game_state["drawer_name"]:
            role = "drawer"
            print(f"[WEBSOCKET] {name} assigned drawer role (already assigned)")
        else:
            print(f"[WEBSOCKET] {name} assigned guesser role (game in lobby or not their turn)")

        await self.broadcast({"type": "player_list", "players": self.get_player_data()})
        return role

    async def disconnect(self, websocket: WebSocket):
        ws_id = id(websocket)
        name = self.ws_to_name.get(ws_id)
        if name:
            if name in self.active_connections:
                del self.active_connections[name]
            del self.ws_to_name[ws_id]
            self.remove_from_drawer_queue(name)
            is_drawer = (name == self.game_state["drawer_name"])
            await self.broadcast({"type": "player_list", "players": self.get_player_data()})
            if is_drawer:
                if self.room and self.room.game_started and self.active_connections:
                    await self.reassign_drawer_after_removal()
                return False
        
        # Cancel active vote kick if disconnected player was involved
        if self.active_vote_kick:
            if name == self.active_vote_kick["target_player"] or name == self.active_vote_kick["initiator"]:
                print(f"[DEBUG-VOTE] {name} disconnected during vote kick. Cancelling vote session.")
                await self.cancel_vote_kick(reason="disconnect")

    async def initiate_vote_kick(self, initiator: str, target: str):
        """Initiate a vote kick session. Returns True if successful, False otherwise."""
        print(f"[DEBUG-VOTE] Vote kick initiated: {initiator} -> {target}")
        
        # Validate
        if initiator == target:
            print(f"[DEBUG-VOTE] INVALID: Player cannot kick themselves")
            return False
        
        if target not in self.active_connections:
            print(f"[DEBUG-VOTE] INVALID: Target player {target} not found")
            return False
        
        # Only one vote kick at a time
        if self.active_vote_kick:
            print(f"[DEBUG-VOTE] INVALID: Vote kick already in progress for {self.active_vote_kick['target_player']}")
            return False
        
        # Get eligible voters (all except initiator and target)
        eligible_voters = [name for name in self.active_connections.keys() 
                          if name != initiator and name != target]
        
        if not eligible_voters:
            print(f"[DEBUG-VOTE] INVALID: Not enough players to vote")
            return False
        
        # Create vote session
        self.active_vote_kick = {
            "target_player": target,
            "initiator": initiator,
            "votes_yes": 0,
            "votes_no": 0,
            "voters": set(),  # Track who has voted
            "eligible_voters": set(eligible_voters),
            "timeout_task": None
        }

        if self.room and self.room.db_id:
            target_id = self.room.get_player_db_id(target)
            initiator_id = self.room.get_player_db_id(initiator)
            if target_id and initiator_id:
                with get_db_session() as db:
                    vote_kick = start_vote_kick_record(
                        db, self.room.db_id, target_id, initiator_id
                    )
                    self.active_vote_kick_db_id = vote_kick.id
        
        print(f"[DEBUG-VOTE] Vote kick started for {target}. Eligible voters: {eligible_voters}")
        
        # Broadcast vote kick started to eligible voters only
        vote_message = {
            "type": "vote_kick_started",
            "target_player": target,
            "initiator": initiator,
            "timeout_seconds": self.vote_kick_timeout,
            "eligible_voters": eligible_voters
        }
        
        for name, ws in self.active_connections.items():
            # Skip initiator and target
            if name not in [initiator, target]:
                try:
                    await ws.send_json(vote_message)
                except:
                    continue
        
        # Start timeout timer
        await self._start_vote_kick_timer()
        return True

    async def _start_vote_kick_timer(self):
        """Start the 15-second timeout for vote kick."""
        if not self.active_vote_kick:
            return
        
        async def vote_timeout():
            try:
                await asyncio.sleep(self.vote_kick_timeout)
                await self._resolve_vote_kick()
            except asyncio.CancelledError:
                print("[DEBUG-VOTE] Vote kick timer cancelled")
        
        self.active_vote_kick["timeout_task"] = asyncio.create_task(vote_timeout())

    async def cast_vote_kick(self, voter: str, vote: str):
        """Cast a vote (yes/no). Returns True if vote counted, False otherwise."""
        if not self.active_vote_kick:
            print(f"[DEBUG-VOTE] No active vote kick to vote on")
            return False
        
        target = self.active_vote_kick["target_player"]
        initiator = self.active_vote_kick["initiator"]
        
        # Validate voter is eligible
        if voter not in self.active_vote_kick["eligible_voters"]:
            print(f"[DEBUG-VOTE] {voter} is not eligible to vote")
            return False
        
        # Prevent duplicate votes
        if voter in self.active_vote_kick["voters"]:
            print(f"[DEBUG-VOTE] {voter} already voted")
            return False
        
        # Record vote
        self.active_vote_kick["voters"].add(voter)
        if vote.lower() == "yes":
            self.active_vote_kick["votes_yes"] += 1
            print(f"[DEBUG-VOTE] {voter} voted YES. Current: YES={self.active_vote_kick['votes_yes']} NO={self.active_vote_kick['votes_no']}")
        else:
            self.active_vote_kick["votes_no"] += 1
            print(f"[DEBUG-VOTE] {voter} voted NO. Current: YES={self.active_vote_kick['votes_yes']} NO={self.active_vote_kick['votes_no']}")

        if self.active_vote_kick_db_id and self.room:
            voter_db_id = self.room.get_player_db_id(voter)
            if voter_db_id:
                with get_db_session() as db:
                    record_vote_kick_vote(
                        db,
                        self.active_vote_kick_db_id,
                        voter_db_id,
                        vote.lower() == "yes",
                    )
        
        # Broadcast vote update to all players
        await self.broadcast({
            "type": "vote_kick_update",
            "yes_votes": self.active_vote_kick["votes_yes"],
            "no_votes": self.active_vote_kick["votes_no"],
            "total_votes": len(self.active_vote_kick["voters"]),
            "eligible_voters": len(self.active_vote_kick["eligible_voters"])
        })
        
        # Check if all eligible voters have voted
        if len(self.active_vote_kick["voters"]) == len(self.active_vote_kick["eligible_voters"]):
            print(f"[DEBUG-VOTE] All eligible voters have voted. Resolving immediately.")
            if self.active_vote_kick["timeout_task"]:
                self.active_vote_kick["timeout_task"].cancel()
            await self._resolve_vote_kick()
        
        return True

    async def _resolve_vote_kick(self):
        """Resolve the vote kick and apply result."""
        if not self.active_vote_kick:
            return
        
        target = self.active_vote_kick["target_player"]
        initiator = self.active_vote_kick["initiator"]
        yes_votes = self.active_vote_kick["votes_yes"]
        no_votes = self.active_vote_kick["votes_no"]
        
        print(f"[DEBUG-VOTE] Resolving vote kick for {target}. YES={yes_votes} NO={no_votes}")
        
        # Vote result logic:
        # - If yes >= 1 and no == 0: KICK
        # - If yes >= 1 and no >= 1: TIE (no kick)
        # - If yes == 0 and no >= 1: NO KICK
        # - If yes == 0 and no == 0: NO KICK (timeout, no votes)
        
        result = None
        if yes_votes >= 1 and no_votes == 0:
            result = "KICK"
            print(f"[DEBUG-VOTE] RESULT: KICK - {target} has been removed")
        elif yes_votes >= 1 and no_votes >= 1:
            result = "TIE"
            print(f"[DEBUG-VOTE] RESULT: TIE - Conflicting votes, no action")
        else:
            result = "NO_KICK"
            print(f"[DEBUG-VOTE] RESULT: NO_KICK - Insufficient yes votes")
        
        # Broadcast result to all
        await self.broadcast({
            "type": "vote_kick_result",
            "target_player": target,
            "result": result,
            "yes_votes": yes_votes,
            "no_votes": no_votes
        })
        
        # Execute kick if result is KICK
        if result == "KICK":
            await self._execute_player_kick(target)

        if self.active_vote_kick_db_id:
            status_map = {"KICK": "PASSED", "TIE": "FAILED", "NO_KICK": "FAILED"}
            with get_db_session() as db:
                resolve_vote_kick_record(
                    db,
                    self.active_vote_kick_db_id,
                    status_map.get(result, "CANCELLED"),
                )
        
        # Clear vote session
        self.active_vote_kick = None
        self.active_vote_kick_db_id = None

    async def _execute_player_kick(self, player_name: str):
        """Remove player from room and close their connection. Handles host transfer if needed."""
        print(f"[DEBUG-VOTE] Executing kick for {player_name}")

        guest_id = None
        if self.room:
            guest_id = self.room.get_player_guest_id(player_name)
            if not guest_id and self.room.db_id:
                player_db_id = self.room.get_player_db_id(player_name)
                if player_db_id:
                    with get_db_session() as db:
                        guest_id = get_guest_id_for_player(db, self.room.db_id, player_db_id)

        if guest_id and self.room and self.room.db_id:
            with get_db_session() as db:
                ban_guest_from_room(
                    db,
                    self.room.db_id,
                    guest_id,
                    reason="vote_kick",
                )
            self.room.ban_guest(guest_id)
            print(f"[DEBUG-VOTE] Banned guest {guest_id} from room {self.room.room_id}")

        if self.room and self.room.db_id:
            player_db_id = self.room.get_player_db_id(player_name)
            if player_db_id:
                with get_db_session() as db:
                    leave_room_record(db, self.room.db_id, player_db_id, was_kicked=True)

        if player_name in self.active_connections:
            ws = self.active_connections[player_name]

            try:
                await ws.send_json({
                    "type": "player_kicked",
                    "message": "You have been kicked from the room by vote."
                })
                await ws.close()
            except Exception:
                pass

            del self.active_connections[player_name]
            ws_id = None
            for wid, name in self.ws_to_name.items():
                if name == player_name:
                    ws_id = wid
                    break
            if ws_id:
                del self.ws_to_name[ws_id]

            print(f"[DEBUG-VOTE] {player_name} has been disconnected")

        if self.room and player_name in self.room.players:
            self.room.players.remove(player_name)

        self.remove_from_drawer_queue(player_name)

        was_drawer = player_name == self.game_state.get("drawer_name")
        if was_drawer:
            self.game_state["drawer_name"] = None
            self.game_state["drawer_assigned"] = False

        if self.room and player_name == self.room.host and not self.room.game_started:
            print(f"[DEBUG-VOTE] Kicked player {player_name} was the host. Transferring host role.")
            if self.room.players:
                new_host = self.room.players[0]
                self.room.host = new_host
                print(f"[DEBUG-VOTE] New host assigned: {new_host}")

                old_host_id = self.room.get_player_db_id(player_name)
                new_host_id = self.room.get_player_db_id(new_host)
                if self.room.db_id and old_host_id and new_host_id:
                    with get_db_session() as db:
                        record_host_transfer(
                            db, self.room.db_id, old_host_id, new_host_id
                        )

                await self.broadcast({
                    "type": "host_transferred",
                    "new_host": new_host
                })
            else:
                print(f"[DEBUG-VOTE] No players left after kick. Deleting room.")

        await self.broadcast({
            "type": "player_list",
            "players": self.get_player_data()
        })

        await self.broadcast({
            "type": "player_kicked_notification",
            "kicked_player": player_name,
            "message": f"💥 {player_name} has been kicked by vote!"
        })

        print(f"[PLAYER_LEAVE] Player {player_name} left room {self.room.room_id if self.room else 'N/A'} (vote kick)")
        if self.room:
            print(f"[PLAYER_LEAVE] Remaining players={len(self.room.players)}")
            print(f"[PLAYER_LEAVE] Max players={self.room.max_players}")
            log_wait_lobby_eligibility(self.room)
            log_room_state(self.room)
            await broadcast_lobby_update()

        if was_drawer and self.room and self.room.game_started and self.active_connections:
            await self.reassign_drawer_after_removal()

    async def cancel_vote_kick(self, reason: str = "unknown"):
        """Cancel the current vote kick session."""
        if not self.active_vote_kick:
            return
        
        if self.active_vote_kick["timeout_task"]:
            self.active_vote_kick["timeout_task"].cancel()
        
        print(f"[DEBUG-VOTE] Vote kick cancelled. Reason: {reason}")
        
        await self.broadcast({
            "type": "vote_kick_cancelled",
            "reason": reason
        })
        
        if self.active_vote_kick_db_id:
            with get_db_session() as db:
                resolve_vote_kick_record(db, self.active_vote_kick_db_id, "CANCELLED")

        self.active_vote_kick = None
        self.active_vote_kick_db_id = None

    async def start_round_timer(self, duration=None):
        if duration is None:
            duration = self.round_duration

        
        self.cancel_selection_timer()
        
        if self.round_timer_task:
            self.round_timer_task.cancel()
        
        end_timestamp = time.time() + duration

        r.set(f"round_end_time:{id(self)}", end_timestamp)
        r.set("round_end_time", end_timestamp)
        
        self.game_state["is_round_active"] = True

        

        async def timer():
            try:
                while True:
                    remaining = self.get_remaining_time()
                    await self.broadcast({
                        "type": "timer_update",
                        "timer_type": "round",
                        "time_left": remaining,
                        "drawer_name": self.game_state.get("drawer_name")
                    })

                    if remaining <= 0:
                        break
                    await asyncio.sleep(1)

                if self.game_state["is_round_active"]:
                    self.game_state["is_round_active"] = False
                    self.game_state["winner_announcement"] = "⏰ Time's up!"
                    self.game_state["revealed_movie"] = self.game_state["movie"]
                    await self.record_current_movie_history()
                    self.finish_current_round()
                    is_final_round = self.current_round >= self.total_rounds
                    await self.broadcast({
                        "type": "announcement",
                        "message": self.game_state["winner_announcement"],
                        "reveal": self.game_state["revealed_movie"],
                        "is_final_round": is_final_round,
                        "round_number": self.current_round,
                        "total_rounds": self.total_rounds,
                    })
                    if is_final_round:
                        # Brief pause to show the reveal, then Quit / Continue UI
                        await asyncio.sleep(2)
                        await self.end_game()
                    else:
                        await asyncio.sleep(5)
                        await self.restart_game()
            except asyncio.CancelledError:
                
                pass

        self.round_timer_task = asyncio.create_task(timer())

    async def restart_game(self):
        """Start a new round. Handles both initial game start (from lobby) and between-round transitions."""
        print(f"[DEBUG-BACKEND] restart_game() called. drawer_assigned={self.game_state['drawer_assigned']}, active_connections={len(self.active_connections)}, current_round={self.current_round}, total_rounds={self.total_rounds}")
        
        # Check if all rounds are completed
        if self.current_round >= self.total_rounds:
            print(f"[DEBUG-ROUNDS] All {self.total_rounds} rounds completed!")
            await self.end_game()
            return
        
        await self.record_current_movie_history()

        if self.current_db_round_id:
            self.finish_current_round()

        self.increment_round() 
        new_round = self.get_round()
        self.current_round = new_round
        
        if self.round_timer_task:
            self.round_timer_task.cancel()
            self.round_timer_task = None
        
        r.delete("round_end_time")
        self.game_state.update({
            "movie": "", "display_name": "", "is_round_active": False,
            "winner_announcement": None, "revealed_movie": None
        })
        self.history_recorded_for_round = False
        self.draw_history = []
        if not self.active_connections:
            print(f"[DEBUG-BACKEND] No active connections, returning")
            return
        
        # Initialize drawer queue on first round
        player_names = list(self.active_connections.keys())
        if not self.drawer_queue:
            self.initialize_drawer_queue(player_names)
        
        # Get next drawer using fair rotation
        new_drawer_name = self.get_next_drawer()

        if not new_drawer_name or new_drawer_name not in player_names:
            new_drawer_name = random.choice(player_names)
        
        print(f"[DEBUG-ROUNDS] Round {new_round}/{self.total_rounds} - Drawer: {new_drawer_name}")
        
        if self.room and self.room.db_id:
            drawer_db_id = self.room.get_player_db_id(new_drawer_name)
            if drawer_db_id:
                with get_db_session() as db:
                    game_round = create_game_round(
                        db,
                        self.room.db_id,
                        new_round,
                        drawer_db_id,
                        self.round_duration,
                    )
                    if game_round:
                        self.current_db_round_id = game_round.id

        self.game_state["drawer_name"] = new_drawer_name
        self.game_state["drawer_assigned"] = True
        self.game_state["is_selecting"] = True

        await self.start_selection_timer()

        for name, ws in self.active_connections.items():
            role = "drawer" if name == new_drawer_name else "guesser"
            print(f"[DEBUG-BACKEND] Sending init to {name} with role={role}")
            await ws.send_json({
                "type": "init",
                "role": role,
                "round_number": new_round,
                "total_rounds": self.total_rounds,
                "movie_set": False,
                "drawer_name": new_drawer_name,
                "selection_active": True,
                "selection_time_left": self.get_selection_time_left(),
                "category": self.get_room_category(),
            })

        # Drawer's turn begins — send three random words from the room category
        await self.send_word_options_to_drawer()

    async def continue_game(self):
        """
        After all configured rounds finish, start another series of the same
        number of rounds without kicking players or resetting scores.
        """
        print(
            f"[GAME] continue_game() room={self.room_id} "
            f"resetting rounds (was {self.current_round}/{self.total_rounds})"
        )
        self.game_complete = False
        self.reset_round()
        self.current_round = 0
        self.cancel_selection_timer()
        if self.round_timer_task:
            self.round_timer_task.cancel()
            self.round_timer_task = None
        r.delete("round_end_time")
        self.game_state.update({
            "movie": "",
            "display_name": "",
            "is_round_active": False,
            "winner_announcement": None,
            "revealed_movie": None,
            "drawer_assigned": False,
            "drawer_name": None,
            "is_selecting": False,
            "selection_active": False,
        })
        self.draw_history = []
        # Keep drawer_queue order; rebuild if empty so newcomers are included
        if not self.drawer_queue:
            self.initialize_drawer_queue(list(self.active_connections.keys()))

        await self.broadcast({
            "type": "game_continued",
            "message": f"Continuing! Starting another {self.total_rounds} round(s).",
            "total_rounds": self.total_rounds,
        })
        await self.restart_game()

    async def broadcast(self, message: dict):
        for ws in list(self.active_connections.values()):
            try:
                await ws.send_json(message)
            except:
                continue

    async def end_game(self):
        """All configured rounds finished — show final options (quit / continue)."""
        print(f"[DEBUG-ROUNDS] Game ended after {self.total_rounds} rounds")
        self.game_complete = True
        final_scores = self.get_player_data()

        if self.current_db_round_id:
            self.finish_current_round()

        # Do not finalize/end the DB room here so players can choose Continue.
        # Stats/room end still happen when players leave via /leave.

        await self.broadcast({
            "type": "game_ended",
            "final_scores": final_scores,
            "total_rounds": self.total_rounds,
            "can_continue": True,
        })

    async def handle_voluntary_leave(self, name: str):
        await self.record_current_movie_history()

        if name in self.active_connections:
            ws = self.active_connections.pop(name)
            if id(ws) in self.ws_to_name:
                del self.ws_to_name[id(ws)]

            self.remove_from_drawer_queue(name)
            
            if name == self.game_state["drawer_name"]:
                self.cancel_selection_timer()

                await self.broadcast({
                    "type": "drawer_disconnected", 
                    "name": name
                })
                
                if self.active_connections:
                    await self.restart_game()
                else:
                    self.game_state["drawer_assigned"] = False
                    self.game_state["drawer_name"] = None
            
            await self.broadcast({"type": "player_list", "players": self.get_player_data()})
manager = ConnectionManager()

def process_movie(movie: str, show_vowels: bool = True):
    if show_vowels:
        vowels = "AEIOUaeiou "
        return "".join([char if (char in vowels or not char.isalnum()) else "_" for char in movie])
    else:
        return "".join(["_" if char.isalnum() else char for char in movie])

rooms: Dict[str, GameRoom] = {}

def persist_game_start(room: GameRoom):
    if room.db_id:
        with get_db_session() as db:
            start_game_record(db, room.db_id)

def get_unique_name(name, existing_names):
    if name not in existing_names:
        return name
    
    count = 1
    while f"{name}({count})" in existing_names:
        count += 1
    
    return f"{name}({count})"

@app.get("/")
async def get(request: Request):
    # Pass available word categories to the create-room UI
    return templates.TemplateResponse(
        "front_page.html",
        {
            "request": request,
            "categories": word_manager.get_categories(),
        },
    )

@app.get("/game")
async def get_game(request: Request, room_id: str = Cookie(None), username: str = Cookie(None)):
    
    if not room_id:
        return RedirectResponse(url="/", status_code=303)
    
    return templates.TemplateResponse("index.html", {
        "request": request, 
        "room_code": room_id,
        "username": username or "Guest"
    })


async def broadcast_lobby_update():
    """
    Sends current public rooms to all users in the lobby.
    Includes:
      - public rooms still in LOBBY (waiting to start)
      - public PLAYING rooms that still have vacant slots (mid-game join)
    """
    public_list = []
    for r in rooms.values():
        eligible = is_wait_lobby_eligible(r)
        if not eligible:
            reason = wait_lobby_skip_reason(r)
            if r.room_type == "public":
                # Only log public rooms that are skipped (avoid noise for every private room)
                if reason == "already full":
                    print(f"[SKIP] Room already full ({r.room_id})")
                elif reason == "Game already finished":
                    print(f"[SKIP] Game already finished ({r.room_id})")
                elif reason == "Room destroyed":
                    print(f"[SKIP] Room destroyed ({r.room_id})")
            continue

        entry = {
            "room_id": r.room_id,
            "host": r.host,
            "count": len(r.players),
            "max": r.max_players,
            "status": r.status,
            "available_slots": r.available_slots(),
            "game_started": r.game_started,
            "in_progress": r.status == "PLAYING" and r.game_started,
        }
        public_list.append(entry)
        print(
            f"[WAIT_LOBBY] Refreshed room {r.room_id} "
            f"players={entry['count']}/{entry['max']} "
            f"slots={entry['available_slots']} status={r.status}"
        )

    print("[WAIT_LOBBY] Rooms:")
    print("[")
    for entry in public_list:
        print(
            f"   {{\n"
            f"      room:\"{entry['room_id']}\",\n"
            f"      players:{entry['count']},\n"
            f"      max:{entry['max']},\n"
            f"      available_slots:{entry['available_slots']},\n"
            f"      status:\"{entry['status']}\"\n"
            f"   }}"
        )
    print("]")
    print(f"[WAIT_LOBBY] Total rooms in wait lobby={len(public_list)} subscribers={len(lobby_connections)}")

    for ws in lobby_connections:
        try:
            await ws.send_json({"type": "lobby_update", "rooms": public_list})
        except Exception as e:
            print(f"[WAIT_LOBBY] Error sending lobby update: {e}")
            continue

async def cleanup_public_room(room_id: str):
    """
    Completely removes a public room and all related references.
    """
    print(f"[AUTO-DISCARD] Cleaning public room: {room_id}")

    if room_id not in rooms:
        print(f"[AUTO-DISCARD] Room already removed: {room_id}")
        return

    room = rooms[room_id]

    # Cancel timer if exists
    if room_id in public_room_timers:
        public_room_timers[room_id].cancel()
        del public_room_timers[room_id]
        print(f"[AUTO-DISCARD] Timer removed for room: {room_id}")

    # Disconnect active websocket references
    try:
        for name, ws in list(room.manager.active_connections.items()):
            try:
                await ws.close()
            except:
                pass
    except Exception as e:
        print(f"[AUTO-DISCARD] Error closing sockets: {e}")

    # Clear manager state
    room.manager.active_connections.clear()
    room.manager.ws_to_name.clear()
    room.players.clear()

    # Remove room references
    if room_id in rooms:
        room = rooms[room_id]
        if room.db_id:
            with get_db_session() as db:
                end_room_record(db, room.db_id)
        del rooms[room_id]

    if room_id in public_rooms:
        del public_rooms[room_id]

    print(f"[AUTO-DISCARD] Public room deleted successfully: {room_id}")

    # Update lobby
    await broadcast_lobby_update()

def get_lobby_time_left(room: GameRoom) -> Optional[int]:
    if (
        room.room_type != "private"
        or room.game_started
        or room.lobby_auto_start_deadline is None
    ):
        return None
    return max(0, int(room.lobby_auto_start_deadline - time.time()))


def cancel_private_room_timer(room_id: str):
    if room_id in private_room_timers:
        private_room_timers[room_id].cancel()
        del private_room_timers[room_id]
        print(f"[AUTO-START] Timer cancelled for private room: {room_id}")


async def start_private_room_auto_start_timer(room_id: str):
    """
    Starts 5-minute auto-start timer for private rooms.
    Broadcasts countdown updates and starts the game when time expires.
    """
    try:
        print(f"[AUTO-START] Timer started for private room: {room_id}")

        while True:
            if room_id not in rooms:
                print(f"[AUTO-START] Room already gone: {room_id}")
                return

            room = rooms[room_id]
            if room.game_started or room.room_type != "private":
                return

            remaining = get_lobby_time_left(room)
            if remaining is None:
                return

            await room.manager.broadcast({
                "type": "timer_update",
                "timer_type": "lobby",
                "time_left": remaining,
            })

            if remaining <= 0:
                break
            await asyncio.sleep(1)

        if room_id not in rooms:
            return

        room = rooms[room_id]
        if room.game_started or room.room_type != "private":
            return

        if len(room.players) >= 2:
            print(
                f"[AUTO-START] Auto-starting private room {room_id} "
                f"with {len(room.players)} players"
            )
            room.status = "PLAYING"
            room.game_started = True
            persist_game_start(room)
            private_room_timers.pop(room_id, None)
            await room.manager.restart_game()
        else:
            print(
                f"[AUTO-START] Auto-start skipped for room {room_id}: "
                f"only {len(room.players)} player(s)"
            )
            await room.manager.broadcast({
                "type": "error",
                "message": "Auto-start failed: At least 2 players are required to start the game.",
            })

    except asyncio.CancelledError:
        print(f"[AUTO-START] Timer cancelled for private room: {room_id}")

    except Exception as e:
        print(f"[AUTO-START] Timer error for private room {room_id}: {e}")

    finally:
        private_room_timers.pop(room_id, None)


async def start_public_room_timer(room_id: str):
    """
    Starts 5-minute auto discard timer for public rooms.
    """
    try:
        print(f"[AUTO-DISCARD] Timer started for room: {room_id}")

        await asyncio.sleep(300)  # 5 minutes

        # Room might already be deleted
        if room_id not in rooms:
            print(f"[AUTO-DISCARD] Room already gone: {room_id}")
            return

        room = rooms[room_id]

        # ONLY discard if host is alone
        if (
            room.room_type == "public"
            and not room.game_started
            and len(room.players) <= 1
        ):
            print(f"[AUTO-DISCARD] No players joined. Removing room: {room_id}")

            await cleanup_public_room(room_id)

        else:
            print(
                f"[AUTO-DISCARD] Room survived timer: {room_id} | Players: {len(room.players)}"
            )

    except asyncio.CancelledError:
        print(f"[AUTO-DISCARD] Timer cancelled for room: {room_id}")

    except Exception as e:
        print(f"[AUTO-DISCARD] Timer error for room {room_id}: {e}")

@app.websocket("/ws/lobby")
async def lobby_endpoint(websocket: WebSocket):
    await websocket.accept()
    lobby_connections.append(websocket)
    await broadcast_lobby_update() # Send initial list
    try:
        while True:
            await websocket.receive_text() # Keep connection alive
    except:
        lobby_connections.remove(websocket)

async def send_lobby_data(ws):
    rooms_data = []

    for room in public_rooms.values():
        rooms_data.append({
            "room_id": room.room_id,
            "players": len(room.players),
            "max_players": room.max_players
        })

    await ws.send_json({
        "type": "lobby_list",
        "rooms": rooms_data
    })
async def broadcast_lobby():
    for ws in lobby_connections:
        try:
            await send_lobby_data(ws)
        except:
            continue

@app.websocket("/ws") 
async def websocket_endpoint(
    websocket: WebSocket,
    username: str = Cookie(None),
    room_id: str = Cookie(None),
    guest_id: str = Cookie(None),
):
    room = rooms.get(room_id)

    if not username or not room_id or room_id not in rooms:
        print(f"[DEBUG] WS Connection Denied: Missing credentials or room {room_id} exists: {room_id in rooms}")
        await websocket.close()
        return

    validated_guest_id = validate_guest_id(guest_id)
    if not validated_guest_id:
        print(f"[DEBUG] WS Connection Denied: Missing or invalid guest_id for {username}")
        await websocket.close()
        return

    room = rooms[room_id]

    if is_guest_banned_in_room(room, validated_guest_id):
        print(f"[DEBUG] WS Connection Denied: Guest {validated_guest_id} is banned from room {room_id}")
        await websocket.accept()
        await websocket.send_json({
            "type": "error",
            "code": "banned",
            "message": "You have been banned from this room and cannot rejoin.",
        })
        await websocket.close()
        return

    manager = room.manager
    print(f"[WEBSOCKET] Connecting: {username} to room {room_id} (guest={validated_guest_id})")
    print(f"[WEBSOCKET] Room status={room.status} game_started={room.game_started} players={len(room.players)}/{room.max_players}")

    role = await manager.connect(websocket, username, validated_guest_id)
    print(f"[WEBSOCKET] {username} connected to room {room_id}, role={role}, room_type={room.room_type}")
    
    # Auto-start ONLY when public room first becomes full before game has started.
    # Mid-game joiners filling the room must NOT restart the game.
    if room.room_type == "public" and room.is_full() and not room.game_started:
        print(f"[GAME] Starting game in room {room_id}")
        print(f"[GAME] Removing room from wait lobby because room is full")
        print(f"[WAIT_LOBBY] Removed room {room_id}")
        room.game_started = True
        room.status = "PLAYING"
        persist_game_start(room)
        await room.manager.restart_game()
        log_room_state(room)
        await broadcast_lobby_update()
    elif room.room_type == "public" and room.is_full() and room.game_started:
        print(f"[GAME] Public room {room_id} is full again during RUNNING game — no restart")
        print(f"[WAIT_LOBBY] Room full again")
        print(f"[WAIT_LOBBY] Removing from wait lobby")
        print(f"[WAIT_LOBBY] Removed room {room_id}")
        log_room_state(room)

    if room.should_start_game() and not room.game_started:
        print(f"[GAME] Starting game in room {room_id} (should_start_game)")
        print(f"[GAME] Removing room from wait lobby because room is full")
        room.game_started = True
        room.status = "PLAYING"
        persist_game_start(room)
        await manager.restart_game()
    
    print(f"[WAIT_LOBBY] Broadcasting lobby update after WS connect")
    await broadcast_lobby_update()

    if role is None:
        return  
    name = username
    current_time_left = manager.get_remaining_time()
    current_round = manager.get_round()

    await websocket.send_json({
        "type": "init", 
        "role": role, 
        "round_number": current_round,
        "total_rounds": manager.total_rounds,
        "room_status": room.status,
        "host_name": room.host,
        "room_type": room.room_type,
        "player_count": len(room.players),
        "max_players": room.max_players,
        "movie_set": bool(manager.game_state["movie"]),
        "display": manager.game_state["display_name"], 
        "full_movie": manager.game_state["movie"],
        "drawer_name": manager.game_state["drawer_name"], 
        "selection_active": manager.game_state.get("selection_active", False),
        "selection_time_left": manager.get_selection_time_left(),
        "history": manager.draw_history,
        "winner_msg": manager.game_state["winner_announcement"], 
        "revealed": manager.game_state["revealed_movie"],
        "time_left": current_time_left, 
        "lobby_time_left": get_lobby_time_left(room),
        "history_movies": manager.movie_history,
        "category": room.category,
        "categories": word_manager.get_categories(),
    })
    print(
        f"[WEBSOCKET] Sent init to {username}: status={room.status} "
        f"movie_set={bool(manager.game_state['movie'])} "
        f"selection_active={manager.game_state.get('selection_active', False)} "
        f"drawer={manager.game_state.get('drawer_name')} "
        f"category={room.category}"
    )    
    try:
        while True:
            data = await websocket.receive_json()
            if data["type"] == "start_game":
                print(f"[GAME] start_game event from {username} in room {room_id}. Is host? {username == room.host}")
                if username == room.host:
                    if len(room.players) >= 2:
                        print(f"[GAME] Starting game in room {room_id}")
                        if room.room_type == "public":
                            print(f"[GAME] Removing room from wait lobby because room is full" if room.is_full() else f"[GAME] Host started public room (may still have slots)")
                            if room.is_full():
                                print(f"[WAIT_LOBBY] Removed room {room_id}")
                        room.status = "PLAYING"
                        room.game_started = True
                        persist_game_start(room)
                        log_room_state(room)

                        # Cancel auto-discard timer once game starts
                        if room_id in public_room_timers:
                            print(f"[AUTO-DISCARD] Game started. Cancelling timer for room: {room_id}")

                            public_room_timers[room_id].cancel()
                            del public_room_timers[room_id]

                        cancel_private_room_timer(room_id)

                        await manager.restart_game() 
                        await broadcast_lobby_update()
                    else:
                        print(f"[GAME] Host tried to start with only {len(room.players)} players (need 2+)")
                        await websocket.send_json({
                            "type": "error",
                            "message": "At least 2 players are required to start the game."
                        })
                else:
                    print(f"[GAME] Non-host {username} tried to start game (host is {room.host})")
            if data["type"] not in ["drawing"]: 
                print(f"[DEBUG] WS Message from {username} in {room_id}: {data['type']}")
            if data["type"] == "set_movie":
                manager.cancel_selection_timer()
                manager.game_state["movie"] = data["movie"].upper()
                manager.game_state["show_vowels"] = data.get("show_vowels", True)

                manager.game_state["display_name"] = process_movie(
                    manager.game_state["movie"],
                    manager.game_state["show_vowels"]
                )
                manager.persist_round_word(manager.game_state["movie"])

                await manager.start_round_timer(duration=manager.round_duration)

                await manager.broadcast({
                    "type": "movie_selected",
                    "drawer_name": manager.game_state["drawer_name"],
                    "full_movie": manager.game_state["movie"]
                })

                await manager.broadcast({
                    "type": "game_start", 
                    "display": manager.game_state["display_name"],
                    "full_movie": manager.game_state["movie"], 
                    "drawer_name": manager.game_state["drawer_name"],
                    "time_left": manager.round_duration 
                })
            elif data["type"] == "won" and manager.game_state["is_round_active"]:
                manager.game_state["is_round_active"] = False

                if manager.round_timer_task:
                    manager.round_timer_task.cancel()
                    manager.round_timer_task = None

                manager.set_player_score(username, 50) 
                if manager.game_state["drawer_name"]:
                    manager.set_player_score(manager.game_state["drawer_name"], 25)

                await manager.record_current_movie_history()
                manager.finish_current_round(username)

                manager.game_state["winner_announcement"] = f"🎉 {username} guessed it first!"
                manager.game_state["revealed_movie"] = manager.game_state["movie"]
                is_final_round = manager.current_round >= manager.total_rounds

                await manager.broadcast({"type": "player_list", "players": manager.get_player_data()})
                await manager.broadcast({
                    "type": "announcement",
                    "message": manager.game_state["winner_announcement"],
                    "reveal": manager.game_state["revealed_movie"],
                    "is_final_round": is_final_round,
                    "round_number": manager.current_round,
                    "total_rounds": manager.total_rounds,
                })
                # Last round finished — show Quit / Continue (no "Next Round")
                if is_final_round:
                    await manager.end_game()
            elif data["type"] == "restart":
                # Ignore mid-click "next round" once the series is already complete
                if manager.current_round >= manager.total_rounds or manager.game_complete:
                    await manager.end_game()
                else:
                    await manager.restart_game()
            elif data["type"] == "continue_game":
                print(f"[GAME] continue_game requested by {username} in room {room_id}")
                await manager.continue_game()
            elif data["type"] == "drawing":
                manager.draw_history.append(data)
                await manager.broadcast(data)
            elif data["type"] == "clear":
                manager.draw_history = []
                await manager.broadcast(data)
            elif data["type"] == "random_movie":
                # Drawer requested a fresh set of word options (uses room category)
                if name == manager.game_state["drawer_name"]:
                    # Prefer room category; allow optional override from client
                    requested = data.get("category") or data.get("section")
                    if requested and word_manager.has_category(requested):
                        # Temporary override for this pick only (room category stays)
                        category = requested
                        try:
                            options = word_manager.get_random_words(category, count=3)
                        except CategoryNotFoundError:
                            options = word_manager.get_random_words(
                                manager.get_room_category(), count=3
                            )
                        await websocket.send_json({
                            "type": "movie_options",
                            "options": options,
                            "category": category,
                        })
                    else:
                        await manager.send_word_options_to_drawer(count=3)
            elif data["type"] == "select_movie":
                if name == manager.game_state["drawer_name"]:
                    manager.cancel_selection_timer()
                    movie = data["movie"]
                    # Normalize to uppercase for consistent guessing
                    manager.game_state["movie"] = movie.strip().upper()
                    manager.game_state["show_vowels"] = data.get("show_vowels", True)

                    manager.game_state["display_name"] = process_movie(
                        manager.game_state["movie"],
                        manager.game_state["show_vowels"]
                    )
                    manager.persist_round_word(manager.game_state["movie"])
                    print(
                        f"[WORD_MANAGER] Drawer {name} selected word="
                        f"{manager.game_state['movie']} "
                        f"category={manager.get_room_category()}"
                    )

                    await manager.start_round_timer(duration=manager.round_duration)

                    await manager.broadcast({
                        "type": "movie_selected",
                        "drawer_name": manager.game_state["drawer_name"],
                        "full_movie": manager.game_state["movie"]
                    })

                    await manager.broadcast({
                        "type": "game_start",
                        "display": manager.game_state["display_name"],
                        "full_movie": manager.game_state["movie"],
                        "drawer_name": manager.game_state["drawer_name"],
                        "time_left": manager.round_duration 
                    })
            elif data["type"] == "initiate_vote_kick":
                target_player = data.get("target_player")
                success = await manager.initiate_vote_kick(name, target_player)
                if not success:
                    await websocket.send_json({
                        "type": "error",
                        "message": "Cannot initiate vote kick. Either a vote is already in progress or invalid target."
                    })
            elif data["type"] == "vote_kick_response":
                vote = data.get("vote")  # "yes" or "no"
                success = await manager.cast_vote_kick(name, vote)
                if not success:
                    await websocket.send_json({
                        "type": "error",
                        "message": "Cannot vote. Either you've already voted or the vote session has ended."
                    })
    except WebSocketDisconnect:
        print(f"[WEBSOCKET] {username} disconnected from room {room_id}")
        print(f"[PLAYER_LEAVE] Player {username} left room {room_id}")

        # Remove websocket connection
        await manager.disconnect(websocket)

        # Remove player from room player list
        if username in room.players:
            room.players.remove(username)
            print(f"[PLAYER_LEAVE] Removed {username} from room.players")

            if room.db_id:
                player_db_id = room.get_player_db_id(username)
                if player_db_id:
                    with get_db_session() as db:
                        set_player_online(db, room.db_id, player_db_id, False)
                        leave_room_record(db, room.db_id, player_db_id)

        print(f"[PLAYER_LEAVE] Remaining players={len(room.players)}")
        print(f"[PLAYER_LEAVE] Max players={room.max_players}")
        print(f"[PLAYER_LEAVE] Players list={room.players}")
        print(f"[PLAYER_LEAVE] Game started={room.game_started} status={room.status}")

        # ==========================================
        # HOST TRANSFER LOGIC
        # ==========================================
        if username == room.host and not room.game_started:
            print(f"[ROOM] Host left before game started")

            # Transfer host if players remain
            if room.players:
                new_host = room.players[0]
                room.host = new_host

                print(f"[ROOM] New host assigned: {new_host}")

                old_host_id = room.get_player_db_id(username)
                new_host_id = room.get_player_db_id(new_host)
                if room.db_id and old_host_id and new_host_id:
                    with get_db_session() as db:
                        record_host_transfer(db, room.db_id, old_host_id, new_host_id)

                # Broadcast new host to everyone
                await manager.broadcast({
                    "type": "host_transferred",
                    "new_host": new_host
                })

            else:
                print(f"[SKIP] Room destroyed")
                print(f"[WAIT_LOBBY] Removed room {room_id}")

                if room.db_id:
                    with get_db_session() as db:
                        end_room_record(db, room.db_id)

                # Delete room completely
                cancel_private_room_timer(room_id)

                if room_id in rooms:
                    del rooms[room_id]

                if room_id in public_rooms:
                    del public_rooms[room_id]

        # Mid-game vacancy: running public rooms with open slots re-enter wait lobby
        if room_id in rooms:
            log_wait_lobby_eligibility(room)
            log_room_state(room)

        # ==========================================
        # RESTART AUTO-DISCARD TIMER
        # ==========================================
        if (
            room_id in rooms
            and room.room_type == "public"
            and not room.game_started
        ):
            # If only one player remains, restart timer
            if len(room.players) == 1:

                if room_id not in public_room_timers:

                    print(
                        f"[AUTO-DISCARD] Only one player left in room {room_id}. Restarting timer."
                    )

                    task = asyncio.create_task(start_public_room_timer(room_id))
                    public_room_timers[room_id] = task

            # Cancel timer if room recovered
            elif len(room.players) >= 2:

                if room_id in public_room_timers:

                    print(
                        f"[AUTO-DISCARD] Room recovered with multiple players. Cancelling timer: {room_id}"
                    )

                    public_room_timers[room_id].cancel()
                    del public_room_timers[room_id]

        # Update public lobby instantly (re-adds running public rooms with vacant slots)
        await broadcast_lobby_update()

        # Send updated player list
        if room_id in rooms:
            await manager.broadcast({
                "type": "player_list",
                "players": manager.get_player_data()
            })

        print(f"[PLAYER_LEAVE] Disconnect handling completed for {username} in {room_id}")
        