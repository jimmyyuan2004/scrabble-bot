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

from game import Game, ScrabbleError
from storage import create_game, delete_game, edit_game, load_game

app = Flask(__name__)
CORS(app)

# No I, L, O or 0/1, so codes are easy to read aloud and type.
CODE_LETTERS = "ABCDEFGHJKMNPQRSTUVWXYZ"
CODE_LENGTH = 4
MAX_NAME_LENGTH = 20


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
    name = clean_name(request.get_json())
    game = Game()
    credentials = add_new_player(game, name)
    for _ in range(20):
        code = "".join(random.choices(CODE_LETTERS, k=CODE_LENGTH))
        if create_game(code, game):
            return jsonify({"code": code, **credentials})
    raise ApiError("Could not create a game. Try again.", 500)


@app.route("/games/<code>", methods=["GET"])
def game_state(code):
    game = load_game(code.upper())
    if game is None:
        raise ApiError("That game doesn't exist.", 404)

    me = player_from_token(game)
    return jsonify({
        "code": code.upper(),
        "started": game.started,
        "players": [{"player_id": pid, "name": p.name} for pid, p in game.players.items()],
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
        "me": me.player_id if me else None,
        "rack": me.rack if me else None,
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

    game, player_id = g.game, g.player.player_id
    before = set(game.pending_plays.keys()) - {player_id}
    game.play_tiles(player_id, letters, row, col, direction, blanks=blanks)

    # Draw replacements for anyone whose pending play just got auto-confirmed.
    for other_id in before:
        if other_id not in game.pending_plays:
            game.draw_replacements(other_id)

    return jsonify({"ok": True, "message": f"Play recorded: {letters}"})


@app.route("/games/<code>/challenge", methods=["POST"])
@room_action
def challenge(code):
    data = request.get_json()
    target_id, result = json_field(data, "player_id", "result")  # "success" or "fail"

    if target_id == g.player.player_id:
        raise ApiError("You can't resolve your own play.", 400)

    if result == "success":
        g.game.challenge_success(target_id)
    else:
        g.game.challenge_fail(target_id)
        g.game.draw_replacements(target_id)
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
