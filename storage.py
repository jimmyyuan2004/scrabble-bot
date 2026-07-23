"""
storage.py

Responsible for:
- Saving game state
- Loading previous games

Storage method: game.json (single file, single game — matches the
current one-game-per-bot-instance scope).
"""

import json
import os

from game import Game, Player, PendingPlay

DEFAULT_SAVE_PATH = "game.json"


def save_game(game: Game, path: str = DEFAULT_SAVE_PATH) -> None:
    data = {
        "started": game.started,
        "bag": game.bag,
        "turn_order": game.turn_order,
        "turn_index": game.turn_index,
        "history": game.history,
        "board": game.board,
        "players": {
            player_id: {"name": player.name, "rack": player.rack}
            for player_id, player in game.players.items()
        },
        "pending_plays": {
            player_id: {
                "letters": pending.letters,
                "positions": pending.positions,
                "new_letters": pending.new_letters,
            }
            for player_id, pending in game.pending_plays.items()
        },
        "confirmed_counts": game._confirmed_counts,
    }
    tmp_path = path + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp_path, path)


def load_game(path: str = DEFAULT_SAVE_PATH) -> Game:
    with open(path, "r") as f:
        data = json.load(f)

    game = Game()
    game.started = data["started"]
    game.bag = data["bag"]
    game.turn_order = data["turn_order"]
    game.turn_index = data["turn_index"]
    game.history = data["history"]
    game.board = data.get("board", {})

    for player_id, pdata in data["players"].items():
        player = Player(player_id, pdata["name"])
        player.rack = pdata["rack"]
        game.players[player_id] = player

    for player_id, pdata in data["pending_plays"].items():
        # positions come back as lists from JSON; tuples aren't required internally
        game.pending_plays[player_id] = PendingPlay(
            player_id, pdata["letters"], pdata["positions"], pdata["new_letters"]
        )

    game._confirmed_counts = data.get("confirmed_counts", {})

    return game


def game_exists(path: str = DEFAULT_SAVE_PATH) -> bool:
    return os.path.exists(path)