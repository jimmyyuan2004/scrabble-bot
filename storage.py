"""
storage.py

Responsible for:
- Saving game state
- Loading previous games

Each room is one JSON document, keyed by its room code. If DATABASE_URL is
set, documents live in a Postgres table. Otherwise they are files in
games/, which is enough for local development.
"""

import json
import os
import threading
from contextlib import contextmanager
from typing import Optional

from game import Game, Player, PendingPlay

DATABASE_URL = os.getenv("DATABASE_URL")
GAMES_DIR = "games"

_file_lock = threading.Lock()


def game_to_dict(game: Game) -> dict:
    return {
        "settings": game.settings,
        "turn_started_at": game.turn_started_at,
        "turn_time_used": game.turn_time_used,
        "time_banks": game.time_banks,
        "forfeited": game.forfeited,
        "skip_next": game.skip_next,
        "challenge": game.challenge,
        "winner": game.winner,
        "pending_since": game.pending_since,
        "started": game.started,
        "bag": game.bag,
        "turn_order": game.turn_order,
        "turn_index": game.turn_index,
        "history": game.history,
        "board": game.board,
        "board_blanks": game.board_blanks,
        "players": {
            player_id: {"name": player.name, "rack": player.rack, "token": player.token}
            for player_id, player in game.players.items()
        },
        "pending_plays": {
            player_id: {
                "letters": pending.letters,
                "positions": pending.positions,
                "new_letters": pending.new_letters,
                "blanks": list(pending.blanks),
            }
            for player_id, pending in game.pending_plays.items()
        },
        "confirmed_counts": game._confirmed_counts,
    }


def game_from_dict(data: dict) -> Game:
    game = Game(data.get("settings"))
    game.turn_started_at = data.get("turn_started_at")
    game.turn_time_used = data.get("turn_time_used", 0)
    game.time_banks = data.get("time_banks", {})
    game.forfeited = data.get("forfeited", [])
    game.skip_next = data.get("skip_next", [])
    game.challenge = data.get("challenge")
    game.winner = data.get("winner")
    game.pending_since = data.get("pending_since")
    game.started = data["started"]
    game.bag = data["bag"]
    game.turn_order = data["turn_order"]
    game.turn_index = data["turn_index"]
    game.history = data["history"]
    game.board = data.get("board", {})
    game.board_blanks = data.get("board_blanks", {})
    for player_id, pdata in data["players"].items():
        player = Player(player_id, pdata["name"], pdata["token"])
        player.rack = pdata["rack"]
        game.players[player_id] = player

    for player_id, pdata in data["pending_plays"].items():
        # positions come back as lists from JSON; tuples aren't required internally
        game.pending_plays[player_id] = PendingPlay(
            player_id, pdata["letters"], pdata["positions"], pdata["new_letters"],
            blanks=pdata.get("blanks", []),
        )

    game._confirmed_counts = data.get("confirmed_counts", {})

    return game


# ----------------------------------------------------------------------
# Postgres backend
# ----------------------------------------------------------------------

def _connect():
    import psycopg
    return psycopg.connect(DATABASE_URL)


def _init_db() -> None:
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS games (
                code TEXT PRIMARY KEY,
                state JSONB NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )


if DATABASE_URL:
    _init_db()


# ----------------------------------------------------------------------
# File backend
# ----------------------------------------------------------------------

def _path(code: str) -> str:
    return os.path.join(GAMES_DIR, f"{code}.json")


def _read_file(code: str) -> Optional[dict]:
    if not os.path.exists(_path(code)):
        return None
    with open(_path(code), "r") as f:
        return json.load(f)


def _write_file(code: str, data: dict) -> None:
    os.makedirs(GAMES_DIR, exist_ok=True)
    tmp_path = _path(code) + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp_path, _path(code))


# ----------------------------------------------------------------------
# Public API
# ----------------------------------------------------------------------

def load_game(code: str) -> Optional[Game]:
    """Read a game without locking it. Returns None if the room doesn't exist."""
    if DATABASE_URL:
        with _connect() as conn:
            row = conn.execute("SELECT state FROM games WHERE code = %s", (code,)).fetchone()
        return game_from_dict(row[0]) if row else None
    data = _read_file(code)
    return game_from_dict(data) if data else None


def create_game(code: str, game: Game) -> bool:
    """Save a new room. Returns False if the code is already taken."""
    state = json.dumps(game_to_dict(game))
    if DATABASE_URL:
        with _connect() as conn:
            cur = conn.execute(
                "INSERT INTO games (code, state) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                (code, state),
            )
            return cur.rowcount == 1
    with _file_lock:
        if _read_file(code) is not None:
            return False
        _write_file(code, game_to_dict(game))
        return True


@contextmanager
def edit_game(code: str):
    """Load a game, yield it, and save it when the block finishes.

    The game is locked for the whole block, so two simultaneous requests
    can't overwrite each other. Yields None if the room doesn't exist, and
    nothing is saved if the block raises.
    """
    if DATABASE_URL:
        with _connect() as conn:
            row = conn.execute(
                "SELECT state FROM games WHERE code = %s FOR UPDATE", (code,)
            ).fetchone()
            game = game_from_dict(row[0]) if row else None
            yield game
            if game is not None:
                conn.execute(
                    "UPDATE games SET state = %s, updated_at = now() WHERE code = %s",
                    (json.dumps(game_to_dict(game)), code),
                )
        return

    with _file_lock:
        data = _read_file(code)
        game = game_from_dict(data) if data else None
        yield game
        if game is not None:
            _write_file(code, game_to_dict(game))


def delete_game(code: str) -> None:
    if DATABASE_URL:
        with _connect() as conn:
            conn.execute("DELETE FROM games WHERE code = %s", (code,))
        return
    with _file_lock:
        if os.path.exists(_path(code)):
            os.remove(_path(code))
