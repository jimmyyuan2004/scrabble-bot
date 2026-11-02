# Scrabble Board

A shared web Scrabble board for playing with friends. Start a game, share the code, and play from any browser. It tracks the bag, racks, board and pending plays. It does not check words or keep score: players judge words themselves by challenging plays they doubt.

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

Render's free plan loses local files when the service sleeps or redeploys, and no database is connected, so **games currently disappear after about 15 idle minutes**. To keep them, set `DATABASE_URL` on Render to a Postgres connection string. The `games` table is created automatically.

The free plan also sleeps when idle, so the first request can take about a minute. The page shows a notice while this happens.

`gunicorn` does not run on Windows, so use `python app.py` for local work.

## How a game works

1. One player presses **New Game**, picks the settings, and shares the 4-letter code or invite link. Others join from the home page. A game holds up to 14 players, which is as far as 100 tiles stretch.
2. Once at least 2 players are in, anyone presses **Start Game**. Each player is dealt 7 tiles and the turn order is shuffled.
3. On your turn, click a rack tile, click a square, then press **Submit Play**. Tiles leave your rack, the play is marked pending, and the next player's turn starts. You can also **Exchange** or **Pass**, which use your turn.
4. The pending play is then resolved in one of two ways:
   - **Challenge:** any other player challenges. All clocks pause while the group checks the word, then someone presses **Word is valid** or **Not a word**. The game doesn't check words itself.
   - **The next player takes their turn or runs out of time:** the play stands, the player draws replacements, and it can no longer be challenged.

If the word is rejected, the tiles go back and that player has lost their turn. If it's valid, the challenger pays the penalty set for the game.

Anyone with the code can choose **Spectate** on the home page to watch without joining. Spectators see the board and everyone's racks, but can't change anything. That includes players, so a player could use it to see opponents' tiles.

Press **?** in the top bar at any time for the full controls.

## Settings

| Setting | Options |
| --- | --- |
| Time control | **No limit**, **Per turn** or **Total time** |
| If a challenge fails | **No penalty** or **Lose a turn** |
| Time to challenge | **Next turn** (default), **15 sec**, **30 sec** or **1 min** |
| Game ends | **Classic** only. **First to a score** is reserved for when scoring is added |

- **Per turn:** running out of time loses that turn.
- **Total time:** each player has their own clock, which runs only on their turn. Past zero, they lose points per minute or part of a minute (default 10). Going too far over forfeits (default 10 minutes), and the rest play on. The last player left wins.
- **No limit:** other players can **Skip** someone who is away.
- **Time to challenge:** once it runs out, the play stands and the player draws new tiles, so they aren't left waiting on a slow turn. The next player taking their turn also ends it early. It runs alongside the next player's clock.

The clock defaults follow WESPA tournament rules: 25 minutes each, 10 points per minute over, and forfeit at 10 minutes over. Scores aren't tracked yet, so penalties are shown next to the player's name for the group to apply.

The server has no background process, so expired clocks are acted on the next time someone loads or changes the game. Only one expiry is handled at a time, so nobody loses a run of turns while everyone is away.

## API

Requests marked with a token need an `X-Player-Token` header, using the token returned when creating or joining a game. Tokens are stored in the browser.

| Method | Route | Token | Body | Purpose |
| --- | --- | --- | --- | --- |
| GET | `/status` | | | Health check |
| POST | `/games` | | `name`, `settings` (optional) | Create a game. Returns `code`, `player_id` and `token` |
| POST | `/games/<code>/join` | | `name` | Join before the game starts. Returns the same fields |
| GET | `/games/<code>` | Optional | | Full game state. Includes your rack if you send a token |
| GET | `/games/<code>/spectate` | | | Game state plus every player's rack |
| POST | `/games/<code>/start` | Yes | | Deal racks and start |
| POST | `/games/<code>/play` | Yes | `letters`, `row`, `col`, `direction`, `blanks` (optional) | Submit a play |
| POST | `/games/<code>/pass` | Yes | | Pass your turn |
| POST | `/games/<code>/skip` | Yes | | Skip the current player, in untimed games |
| POST | `/games/<code>/challenge` | Yes | | Challenge the pending play and pause the clocks |
| POST | `/games/<code>/challenge/resolve` | Yes | `valid` | Record the result of the challenge |
| POST | `/games/<code>/exchange` | Yes | `letters` | Swap tiles with the bag |
| POST | `/games/<code>/undo` | Yes | | Take back your pending play and get your turn back |
| POST | `/games/<code>/end` | Yes | | End and delete the game. Returns the final racks |

`settings` can include `time_mode` (`none`, `per_turn` or `total`), `turn_seconds`, `total_seconds`, `overtime_penalty`, `overtime_limit_minutes`, `challenge_rule` (`free` or `lose_turn`), `challenge_seconds` (0 means until the next turn) and `end_condition` (`classic`).

Rows and columns run 0-14 from the top left. `direction` is `across` or `down`. `blanks` lists 0-based indices into `letters`.

## Known limitations

- **One browser per player:** your token is stored in the browser you joined from, so you can't continue on another device.
- **Rules that are not enforced:** first play covering the center, and plays connecting to existing tiles.
- **No scoring** and no end-of-game detection, so time penalties aren't deducted from anything yet.
- **Challenges run on trust:** whoever presses the result button decides it, and a paused game stays paused until someone does.
- **Extending a word:** only the tiles you placed are recorded, so adding an S shows as "S" in the activity log. The board is still correct.
- **Old games are not cleaned up:** a game stays in the database until someone ends it.
