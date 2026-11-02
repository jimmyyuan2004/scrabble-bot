# Scrabble Board

A shared web Scrabble board for playing with friends. Start a game, share the code, and play from any browser. It tracks the bag, racks, board and pending plays. It does not check words or keep score: players judge words themselves by accepting or challenging each play.

Play at <https://jimmyyuan2004.github.io/scrabble-bot/>.

## Project layout

| File | Purpose |
| --- | --- |
| `index.html` | The whole frontend: one static page with no build step. Has Home, Lobby and Game screens. |
| `app.py` | Flask API. A thin layer over `game.py`, with no game logic of its own. |
| `game.py` | All game rules and state. |
| `tiles.py` | The 100-tile bag and drawing. |
| `storage.py` | Saves each game by room code, in Postgres or in local files. |
| `archive/bot.py` | The retired Discord bot, kept for reference. It no longer works with the current API. |

## Running locally

Requires Python 3.

```sh
python -m venv .venv
.venv\Scripts\activate        # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

The API runs at `http://127.0.0.1:5000`. Flask does not serve the page, so in a second terminal run:

```sh
python -m http.server 8000
```

Then open <http://127.0.0.1:8000>. The page uses the local API when opened from `localhost` or `127.0.0.1`, and the Render API everywhere else.

Without `DATABASE_URL`, games are saved as files in `games/`.

## Deployment

Pushing to `main` deploys both parts:

- **Frontend:** GitHub Pages serves `index.html` from the root of `main`.
- **API:** Render auto-deploys `https://scrabble-bot.onrender.com` on the free plan, with the start command `gunicorn app:app`.

Set `DATABASE_URL` on Render to a Postgres connection string so games are kept. The `games` table is created automatically. Render's free plan loses local files when the service sleeps or redeploys, so without a database, games disappear.

The free plan also sleeps when idle, so the first request can take about a minute. The page shows a notice while this happens.

`gunicorn` does not run on Windows, so use `python app.py` for local work.

## How a game works

1. One player presses **New Game** and shares the 4-letter code or invite link. Others join from the home page.
2. Once at least 2 players are in, anyone presses **Start Game** and each player is dealt 7 tiles.
3. To play, click a rack tile, click a square, then press **Submit Play**. Tiles leave your rack and the play is marked pending.
4. The play is then resolved in one of three ways:
   - **Accept:** the tiles go on the board and the player draws replacements.
   - **Challenge:** the play is voided and the tiles return to the player's rack.
   - **The next player submits a play:** any other pending play is accepted automatically.

Press **?** in the top bar at any time for the full controls.

## API

Requests marked with a token need an `X-Player-Token` header, using the token returned when creating or joining a game. Tokens are stored in the browser.

| Method | Route | Token | Body | Purpose |
| --- | --- | --- | --- | --- |
| GET | `/status` | | | Health check |
| POST | `/games` | | `name` | Create a game. Returns `code`, `player_id` and `token` |
| POST | `/games/<code>/join` | | `name` | Join before the game starts. Returns the same fields |
| GET | `/games/<code>` | Optional | | Full game state. Includes your rack if you send a token |
| POST | `/games/<code>/start` | Yes | | Deal racks and start |
| POST | `/games/<code>/play` | Yes | `letters`, `row`, `col`, `direction`, `blanks` (optional) | Submit a play |
| POST | `/games/<code>/challenge` | Yes | `player_id`, `result` (`success` or `fail`) | Resolve another player's play |
| POST | `/games/<code>/exchange` | Yes | `letters` | Swap tiles with the bag |
| POST | `/games/<code>/undo` | Yes | | Take back your pending play |
| POST | `/games/<code>/end` | Yes | | End and delete the game. Returns the final racks |

Rows and columns run 0-14 from the top left. `direction` is `across` or `down`. `blanks` lists 0-based indices into `letters`.

## Known limitations

- **One browser per player:** your token is stored in the browser you joined from, so you can't continue on another device.
- **Rules that are not enforced:** turn order, first play covering the center, and plays connecting to existing tiles.
- **No scoring** and no end-of-game detection.
- **Playing through a pending tile:** the server treats it as a new tile, so the play is rejected unless you hold that letter, and then it costs you one. Accept the pending play first.
- **Extending a word:** only the tiles you placed are recorded, so adding an S shows as "S" in the activity log. The board is still correct.
- **Old games are not cleaned up:** a game stays in the database until someone ends it.
