"""
app.py

A small Flask API wrapping game.py, so the web board can submit plays
directly instead of going through Discord slash commands.

Like bot.py, this file should stay a thin translation layer — no new
game logic here, only HTTP <-> game.py plumbing.
"""

from flask import Flask, request, jsonify
from flask_cors import CORS

from game import Game, ScrabbleError
from storage import load_game, save_game, game_exists

app = Flask(__name__)
CORS(app)


def get_game() -> Game:
    return load_game() if game_exists() else Game()

@app.route("/players", methods=["GET"])
def players():
    game = get_game()
    return jsonify({
        "players": [{"player_id": pid, "name": p.name} for pid, p in game.players.items()]
    })

@app.route("/status", methods=["GET"])
def status():
    game = get_game()
    return jsonify({
        "started": game.started,
        "players": game.list_players(),
        "bag_remaining": len(game.bag),
    })


@app.route("/rack/<player_id>", methods=["GET"])
def rack(player_id):
    game = get_game()
    try:
        return jsonify({"rack": game.get_rack(player_id)})
    except ScrabbleError as e:
        return jsonify({"error": str(e)}), 400


@app.route("/play", methods=["POST"])
def play():
    data = request.get_json()
    player_id = data.get("player_id")
    letters = data.get("letters")
    row = data.get("row")
    col = data.get("col")
    direction = data.get("direction")  # "across" or "down"

    game = get_game()
    try:
        game.play_tiles(player_id, letters, row, col, direction)
        save_game(game)
        return jsonify({"ok": True, "message": f"Play recorded: {letters}"})
    except ScrabbleError as e:
        return jsonify({"error": str(e)}), 400


@app.route("/board", methods=["GET"])
def board():
    game = get_game()
    return jsonify({"board": game.board})


@app.route("/join", methods=["POST"])
def join():
    data = request.get_json()
    player_id = data.get("player_id")
    name = data.get("name")

    game = get_game()
    try:
        game.add_player(player_id, name)
        save_game(game)
        return jsonify({"ok": True})
    except ScrabbleError as e:
        return jsonify({"error": str(e)}), 400


@app.route("/start", methods=["POST"])
def start():
    game = get_game()
    try:
        game.start_game()
        save_game(game)
        racks = {pid: p.rack for pid, p in game.players.items()}
        return jsonify({"ok": True, "racks": racks})
    except ScrabbleError as e:
        return jsonify({"error": str(e)}), 400


@app.route("/challenge", methods=["POST"])
def challenge():
    data = request.get_json()
    player_id = data.get("player_id")
    result = data.get("result")  # "success" or "fail"

    game = get_game()
    try:
        if result == "success":
            game.challenge_success(player_id)
        else:
            game.challenge_fail(player_id)
            game.draw_replacements(player_id)
        save_game(game)
        return jsonify({"ok": True})
    except ScrabbleError as e:
        return jsonify({"error": str(e)}), 400
    
@app.route("/newgame", methods=["POST"])
def newgame():
    game = Game()
    save_game(game)
    return jsonify({"ok": True, "message": "New game created."})

@app.route("/exchange", methods=["POST"])
def exchange():
    data = request.get_json()
    player_id = data.get("player_id")
    letters = data.get("letters")

    game = get_game()
    try:
        game.exchange_tiles(player_id, letters)
        save_game(game)
        return jsonify({"ok": True, "message": f"Exchanged: {letters}"})
    except ScrabbleError as e:
        return jsonify({"error": str(e)}), 400


@app.route("/undo", methods=["POST"])
def undo():
    data = request.get_json()
    player_id = data.get("player_id")

    game = get_game()
    try:
        letters = game.undo_last_play(player_id)
        save_game(game)
        return jsonify({"ok": True, "message": f"Undid play: {letters}"})
    except ScrabbleError as e:
        return jsonify({"error": str(e)}), 400

if __name__ == "__main__":
    app.run(debug=True, port=5000)