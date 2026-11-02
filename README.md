# Scrabble Board

A shared web Scrabble board for playing with friends. It tracks the bag, racks, board and pending plays. It does not check words or keep score: players judge words themselves by accepting or challenging each play.

## Project layout

| File | Purpose |
| --- | --- |
| `index.html` | The whole frontend: one static page with no build step. Polls the API every 4 seconds. |
| `app.py` | Flask API. A thin layer over `game.py`, with no game logic of its own. |
| `game.py` | All game rules and state. |
| `tiles.py` | The 100-tile bag and drawing. |
| `storage.py` | Saves and loads the single current game in `game.json`. |
| `archive/bot.py` | The retired Discord bot, kept for reference. |

## Running locally

Requires Python 3.

```sh
python -m venv .venv
.venv\Scripts\activate        # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

The API runs at `http://127.0.0.1:5000`. Flask does not serve the page, so open `index.html` directly in a browser.

`API_BASE` in `index.html` points at the deployed API. Change it to `http://127.0.0.1:5000` to use your local server, and change it back before deploying.

## Deployment

The API is hosted on Render at `https://scrabble-bot.onrender.com`.

The bot used to start Flask, so the Render service needs this start command now that it is archived:

```sh
gunicorn app:app
```

`gunicorn` does not run on Windows, so use `python app.py` for local work.

`game.json` is written to the working directory. It is lost whenever the instance restarts unless the service has a persistent disk.

## How a game works

1. Players join with a name, then anyone presses **Start Game**. At least 2 players are needed, and each is dealt 7 tiles.
2. To play, click a rack tile, click a square, then press **Submit Play**. Tiles leave your rack and the play is marked pending.
3. The play is then resolved in one of three ways:
   - **Accept:** the tiles go on the board and the player draws replacements.
   - **Challenge:** the play is voided and the tiles return to the player's rack.
   - **The next player submits a play:** any other pending play is accepted automatically.

A challenge is decided by whoever presses the button, so the group settles disputes itself.

## API

| Method | Route | Body | Purpose |
| --- | --- | --- | --- |
| GET | `/status` | | Started flag, player names, tiles left in the bag |
| GET | `/players` | | Player IDs and names |
| GET | `/board` | | Confirmed tiles and blank flags |
| GET | `/pending` | | Unresolved plays |
| GET | `/history` | | Last 15 events |
| GET | `/rack/<player_id>` | | A player's rack |
| POST | `/join` | `player_id`, `name` | Add a player |
| POST | `/start` | | Deal racks and start |
| POST | `/play` | `player_id`, `letters`, `row`, `col`, `direction`, `blanks` (optional) | Submit a play |
| POST | `/challenge` | `player_id`, `result` (`success` or `fail`) | Resolve a play |
| POST | `/exchange` | `player_id`, `letters` | Swap tiles with the bag |
| POST | `/undo` | `player_id` | Take back your pending play |
| POST | `/newgame` | | Wipe the game and start fresh |

Rows and columns run 0-14 from the top left. `direction` is `across` or `down`. `blanks` lists 0-based indices into `letters`.

## Known limitations

- **End Game does not work:** the page calls `POST /end`, which the API does not have. Use **Restart game** instead.
- **No authentication:** anyone with the URL can read any rack, play as any player, or wipe the game.
- **Rules that are not enforced:** turn order, first play covering the center, and plays connecting to existing tiles.
- **No scoring** and no end-of-game detection.
- **One game at a time** per deployment.
