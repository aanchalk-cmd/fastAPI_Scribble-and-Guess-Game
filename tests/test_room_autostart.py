from main import GameRoom


def test_private_room_starts_when_full():
    room = GameRoom("ABC123", "host", "private", 2)
    room.add_player("host")
    room.add_player("guest")

    assert room.should_start_game() is True
