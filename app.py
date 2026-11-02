"""
app.py

A small Flask API wrapping game.py, so the web board can create, join and
play games.

This file should stay a thin translation layer — no new game logic here,
only HTTP <-> game.py plumbing.
"""

import random
import secrets
from functools import wraps

from flask import Flask, g, request, jsonify
from flask_cors import CORS

from game import DEFAULT_SETTINGS, Game, ScrabbleError
from storage import create_game, delete_game, edit_game, load_game

app = Flask(__name__)
CORS(app)

# No I, L, O or 0/1, so codes are easy to read aloud and type.
CODE_LETTERS = "ABCDEFGHJKMNPQRSTUVWXYZ"
CODE_LENGTH = 4
MAX_NAME_LENGTH = 20
MAX_TIMER_SECONDS = 7 * 24 * 60 * 60

SETTING_LIMITS = {
    "turn_seconds": (10, MAX_TIMER_SECONDS),
    "total_seconds": (60, MAX_TIMER_SECONDS),
    "overtime_penalty": (0, 100),
    "overtime_limit_minutes": (0, 60),
}
SETTING_CHOICES = {
    "time_mode": ("none", "per_turn", "total"),
    "challenge_rule": ("free", "lose_turn"),
    "end_condition": ("classic",),
}


class ApiError(Exception):
    def __init__(self, message: str, status: int):
        self.message = message
        self.status = status


@app.errorhandler(ApiError)
def handle_api_error(e):
    return jsonify({"error": e.message}), e.status


@app.errorhandler(ScrabbleError)
def handle_scrabble_error(e):
    return jsonify({"error": str(e)}), 400


def json_field(data: dict, *names):
    values = [data.get(name) for name in names]
    if None in values:
        raise ApiError("Missing required field(s).", 400)
    return values


def clean_name(data: dict) -> str:
    name = str(data.get("name") or "").strip()
    if not name:
        raise ApiError("Enter a name first.", 400)
    if len(name) > MAX_NAME_LENGTH:
        raise ApiError(f"Names can be at most {MAX_NAME_LENGTH} characters.", 400)
    return name


def clean_settings(data: dict) -> dict:
    settings = dict(DEFAULT_SETTINGS)
    for key, value in (data.get("settings") or {}).items():
        if key in SETTING_CHOICES:
            if value not in SETTING_CHOICES[key]:
                raise ApiError(f"Invalid value for {key}.", 400)
        elif key in SETTING_LIMITS:
            low, high = SETTING_LIMITS[key]
            if type(value) is not int or not low <= value <= high:
                raise ApiError(f"{key} must be between {low} and {high}.", 400)
        else:
            raise ApiError(f"Unknown setting: {key}.", 400)
        settings[key] = value
    return settings


def player_from_token(game: Game):
    token = request.headers.get("X-Player-Token", "")
    for player in game.players.values():
        if token and secrets.compare_digest(player.token, token):
            return player
    return None


def room_action(view):
    """Open the room for editing and require a valid player token. The view
    gets the game in g.game and the acting player in g.player."""
    @wraps(view)
    def wrapper(code):
        with edit_game(code.upper()) as game:
            if game is None:
                raise ApiError("That game doesn't exist.", 404)
            player = player_from_token(game)
            if player is None:
                raise ApiError("You are not a player in this game.", 403)
            game.apply_clock()
            g.game = game
            g.player = player
            return view(code.upper())
    return wrapper


def add_new_player(game: Game, name: str) -> dict:
    player_id = secrets.token_hex(8)
    token = secrets.token_urlsafe(24)
    game.add_player(player_id, name, token)
    return {"player_id": player_id, "token": token}


@app.route("/status", methods=["GET"])
def status():
    return jsonify({"ok": True})


@app.route("/games", methods=["POST"])
def new_game():
    data = request.get_json()
    name = clean_name(data)
    game = Game(clean_settings(data))
    credentials = add_new_player(game, name)
    for _ in range(20):
        code = "".join(random.choices(CODE_LETTERS, k=CODE_LENGTH))
        if create_game(code, game):
            return jsonify({"code": code, **credentials})
    raise ApiError("Could not create a game. Try again.", 500)


def public_state(code: str, game: Game) -> dict:
    return {
        "code": code,
        "started": game.started,
        "settings": game.settings,
        "players": [
            {
                "player_id": pid,
                "name": game.players[pid].name,
                "seconds_left": game.seconds_left(pid),
                "penalty": game.overtime_points(pid),
                "forfeited": pid in game.forfeited,
            }
            for pid in game.turn_order
        ],
        "current_player_id": game.current_player_id(),
        "turn_seconds_left": game.turn_seconds_left(),
        "clock_running": game.clock_running(),
        "challenge": game.challenge,
        "winner": game.winner,
        "bag_remaining": len(game.bag),
        "board": game.board,
        "board_blanks": game.board_blanks,
        "pending": [
            {
                "player_id": pid,
                "player_name": game.players[pid].name,
                "letters": p.letters,
                "positions": p.positions,
                "blanks": list(p.blanks),
            }
            for pid, p in game.pending_plays.items()
        ],
        "history": game.history[-15:],  # last 15 events, most recent last
    }


def require_game(code: str) -> Game:
    game = load_game(code)
    if game is None:
        raise ApiError("That game doesn't exist.", 404)
    if game.clock_expired():
        with edit_game(code) as game:
            if game is None:
                raise ApiError("That game doesn't exist.", 404)
            game.apply_clock()
    return game


@app.route("/games/<code>", methods=["GET"])
def game_state(code):
    code = code.upper()
    game = require_game(code)
    me = player_from_token(game)
    return jsonify({
        **public_state(code, game),
        "me": me.player_id if me else None,
        "rack": me.rack if me else None,
    })


@app.route("/games/<code>/spectate", methods=["GET"])
def spectate(code):
    code = code.upper()
    game = require_game(code)
    return jsonify({
        **public_state(code, game),
        "racks": [{"name": p.name, "rack": p.rack} for p in game.players.values()],
    })


@app.route("/games/<code>/join", methods=["POST"])
def join(code):
    name = clean_name(request.get_json())
    with edit_game(code.upper()) as game:
        if game is None:
            raise ApiError("That game doesn't exist. Check the code.", 404)
        credentials = add_new_player(game, name)
    return jsonify({"code": code.upper(), **credentials})


@app.route("/games/<code>/start", methods=["POST"])
@room_action
def start(code):
    g.game.start_game()
    return jsonify({"ok": True})


@app.route("/games/<code>/play", methods=["POST"])
@room_action
def play(code):
    data = request.get_json()
    letters, row, col, direction = json_field(data, "letters", "row", "col", "direction")
    blanks = data.get("blanks", [])  # list of 0-based indices into `letters`
    g.game.play_tiles(g.player.player_id, letters, row, col, direction, blanks=blanks)
    return jsonify({"ok": True, "message": f"Play recorded: {letters}"})


@app.route("/games/<code>/pass", methods=["POST"])
@room_action
def pass_turn(code):
    g.game.pass_turn(g.player.player_id)
    return jsonify({"ok": True})


@app.route("/games/<code>/skip", methods=["POST"])
@room_action
def skip_turn(code):
    g.game.skip_turn(g.player.player_id)
    return jsonify({"ok": True})


@app.route("/games/<code>/challenge", methods=["POST"])
@room_action
def challenge(code):
    g.game.start_challenge(g.player.player_id)
    return jsonify({"ok": True})


@app.route("/games/<code>/challenge/resolve", methods=["POST"])
@room_action
def resolve_challenge(code):
    valid, = json_field(request.get_json(), "valid")
    g.game.resolve_challenge(g.player.player_id, bool(valid))
    return jsonify({"ok": True})


@app.route("/games/<code>/exchange", methods=["POST"])
@room_action
def exchange(code):
    letters, = json_field(request.get_json(), "letters")
    g.game.exchange_tiles(g.player.player_id, letters)
    return jsonify({"ok": True, "message": f"Exchanged: {letters}"})


@app.route("/games/<code>/undo", methods=["POST"])
@room_action
def undo(code):
    letters = g.game.undo_last_play(g.player.player_id)
    return jsonify({"ok": True, "message": f"Undid play: {letters}"})


@app.route("/games/<code>/end", methods=["POST"])
def end(code):
    code = code.upper()
    with edit_game(code) as game:
        if game is None:
            raise ApiError("That game doesn't exist.", 404)
        if player_from_token(game) is None:
            raise ApiError("You are not a player in this game.", 403)
        game.end_game()
        summary = {p.name: p.rack_display() for p in game.players.values()}
    delete_game(code)
    return jsonify({"ok": True, "summary": summary})


if __name__ == "__main__":
    app.run(debug=True, port=5000)
