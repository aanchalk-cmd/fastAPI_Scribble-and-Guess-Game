from main import GameRoom


def test_private_room_starts_when_full():
    room = GameRoom("ABC123", "host", "private", 2)
    room.add_player("host")
    room.add_player("guest")

    assert room.should_start_game() is True


def test_player_list_keeps_roster_order_after_refresh():
    room = GameRoom("ABC123", "host", "private", 2)
    room.add_player("A")
    room.add_player("B")
    room.manager.active_connections = {"B": object(), "A": object()}

    assert [player["name"] for player in room.manager.get_player_data()] == ["A", "B"]
