"""
bot.py

Responsible only for:
- Discord connection
- Slash commands
- Sending messages
- Receiving user commands

No game logic lives here — everything delegates to game.py / tiles.py.
Persistence is handled by storage.py.
"""

import os
import discord
import threading
from app import app as flask_app
from discord import app_commands
from dotenv import load_dotenv
from collections import Counter

from game import Game, ScrabbleError
from storage import load_game, save_game, game_exists

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.default()
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

# Single game per bot instance for now (matches "one game" scope of your
# current plan — "multiple games per server" is listed as a future feature).
game = load_game() if game_exists() else Game()


@client.event
async def on_ready():
    await tree.sync()
    print(f"Logged in as {client.user}")

async def send_rack_dm(player_id: str):
    """DM the player their current full rack."""
    try:
        rack_str = game.get_rack(player_id)
        user = await client.fetch_user(int(player_id))
        await user.send(f"Your updated rack:\n\n{rack_str}")
    except Exception as e:
        print(f"Failed to DM rack to {player_id}: {e}")

@tree.command(name="ping", description="Check the bot is alive")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message("🏓 Pong!!!")

@tree.command(name="newgame", description="Start a new Scrabble game")
async def newgame(interaction: discord.Interaction):
    global game
    game = Game()
    save_game(game)
    await interaction.response.send_message("🎲 New game created. Use /join to add players.")


@tree.command(name="join", description="Join the current game")
async def join(interaction: discord.Interaction):
    try:
        game.add_player(str(interaction.user.id), interaction.user.display_name)
        save_game(game)
        await interaction.response.send_message(
            f"✅ {interaction.user.display_name} joined the game."
        )
    except ScrabbleError as e:
        await interaction.response.send_message(f"❌ {e}", ephemeral=True)


@tree.command(name="players", description="List players in the current game")
async def players(interaction: discord.Interaction):
    names = game.list_players()
    if not names:
        await interaction.response.send_message("No players yet. Use /join.")
    else:
        await interaction.response.send_message("Players: " + ", ".join(names))


@tree.command(name="start", description="Start the game and deal racks")
async def start(interaction: discord.Interaction):
    try:
        game.start_game()
        save_game(game)
        await interaction.response.send_message("🀄 Game started! Racks are being sent via DM...")
        for player_id, player in game.players.items():
            user = await client.fetch_user(int(player_id))
            await user.send(f"Your rack:\n\n{player.rack_display()}")
    except ScrabbleError as e:
        await interaction.response.send_message(f"❌ {e}", ephemeral=True)


@tree.command(name="rack", description="DM yourself your current rack")
async def rack(interaction: discord.Interaction):
    try:
        rack_str = game.get_rack(str(interaction.user.id))
        await interaction.user.send(f"Your rack:\n\n{rack_str}")
        await interaction.response.send_message("📬 Sent your rack via DM.", ephemeral=True)
    except ScrabbleError as e:
        await interaction.response.send_message(f"❌ {e}", ephemeral=True)

@tree.command(name="play", description="Play tiles from your rack (pending challenge)")
@app_commands.describe(
    letters="The letters you're playing, e.g. HISTORY",
    row="Row on the board, 0-14 (0 is the top row)",
    col="Column on the board, 0-14 (0 is the left column)",
    direction="Is the word going across or down?",
)
@app_commands.choices(
    direction=[
        app_commands.Choice(name="across", value="across"),
        app_commands.Choice(name="down", value="down"),
    ]
)
async def play(
    interaction: discord.Interaction,
    letters: str,
    row: int,
    col: int,
    direction: app_commands.Choice[str],
):
    try:
        acting_id = str(interaction.user.id)
        before = set(game.pending_plays.keys()) - {acting_id}

        game.play_tiles(acting_id, letters, row, col, direction.value)
        save_game(game)

        # Anyone whose pending play just got auto-confirmed needs their
        # replacement tiles drawn and a DM.
        auto_confirmed_names = []
        for other_id in before:
            if other_id not in game.pending_plays:
                game.draw_replacements(other_id)
                await send_rack_dm(other_id)
                auto_confirmed_names.append(game.players[other_id].name)
        save_game(game)

        await send_rack_dm(acting_id)

        message = (
            f"📝 {interaction.user.display_name} played **{letters.upper()}** "
            f"at ({row}, {col}), {direction.value}.\n"
            "Pending play recorded. Waiting for challenge resolution."
        )
        if auto_confirmed_names:
            message += f"\n\n✅ {', '.join(auto_confirmed_names)}'s previous play was unchallenged and now stands."
        if len(game.bag) == 0:
            message += "\n\n🎒 The bag is now empty!"

        await interaction.response.send_message(message)
    except ScrabbleError as e:
        await interaction.response.send_message(f"❌ {e}", ephemeral=True)

@tree.command(name="undo", description="Undo your current pending play")
async def undo(interaction: discord.Interaction):
    try:
        letters = game.undo_last_play(str(interaction.user.id))
        save_game(game)
        await send_rack_dm(str(interaction.user.id))
        await interaction.response.send_message(
            f"↩️ {interaction.user.display_name} undid their play of **{letters}**."
        )
    except ScrabbleError as e:
        await interaction.response.send_message(f"❌ {e}", ephemeral=True)

@tree.command(name="help", description="Show available Scrabble bot commands")
async def help_command(interaction: discord.Interaction):
    help_text = (
        "**🎲 Scrabble Bot Commands**\n\n"
        "**Game setup**\n"
        "`/newgame` — start a fresh game\n"
        "`/join` — join the current game\n"
        "`/players` — list who's joined\n"
        "`/start` — deal racks and begin (needs 2+ players)\n\n"
        "**Playing**\n"
        "`/rack` — DM yourself your current tiles\n"
        "`/play LETTERS` — record a play as pending (e.g. `/play HISTORY`)\n"
        "`/draw` — draw replacement tiles after a confirmed play\n"
        "`/exchange LETTERS` — swap tiles with the bag\n"
        "`/undo` — undo your own pending play if you made a mistake\n\n"
        "**Challenges**\n"
        "`/challenge success @player` — void their pending play, tiles returned\n"
        "`/challenge fail @player` — play stands, they draw replacements\n\n"
        "**Extras**\n"
        "`/help` — get some help\n"
        "`/tilestatus` — show how many tiles remain in the bag\n"
        "`/end` — end the current game (there is no point to this since you could just /newgame)\n"
        "`/howtoplay` — learn how to play\n\n"
        "_Reminder: the bot doesn't check words are real — that's on you and the challenge rule._"
    )
    await interaction.response.send_message(help_text, ephemeral=True)

@tree.command(name="howtoplay", description="Learn how this Scrabble bot works")
async def howtoplay(interaction: discord.Interaction):
    instructions = (
        "**📖 How This Bot Works**\n\n"
        "This bot only manages your **tiles** — the board itself lives in a "
        "shared Excel/Google Sheet, where you place words visually.\n\n"
        "**Setup**\n"
        "1. `/newgame` — start a game\n"
        "2. Everyone runs `/join`\n"
        "3. `/start` — shuffles the bag, deals 7 tiles to each player via DM\n\n"
        "**Playing a turn**\n"
        "1. Check `/rack` to see your tiles (also DMed automatically)\n"
        "2. Place your word on the shared sheet\n"
        "3. Run `/play LETTERS` to tell the bot which tiles you used — "
        "this removes them from your rack and marks the play as *pending*\n"
        "4. Other players can challenge if they think the word isn't valid — "
        "**the bot doesn't check dictionaries, that's on you and the challenge rule**\n\n"
        "**Resolving a play**\n"
        "• No challenge / challenge fails → `/challenge fail @player` or next player `/play LETTERS` — "
        "play stands, they draw replacement tiles automatically\n"
        "• Challenge succeeds → `/challenge success @player` — "
        "tiles are returned to their rack, no replacements drawn\n\n"
        "**Other tiles commands**\n"
        "`/exchange LETTERS` — swap tiles with the bag instead of playing\n"
        "`/undo` — take back your own pending play if you made a mistake\n"
        "`/status` — see how many tiles are left in the bag\n\n"
        "**Ending**\n"
        "`/end` — ends the game and shows final racks\n\n"
        "Full command list: `/help`"
    )
    await interaction.response.send_message(instructions, ephemeral=True)

@tree.command(name="draw", description="Draw replacement tiles after a confirmed play")
async def draw(interaction: discord.Interaction):
    try:
        drawn = game.draw_replacements(str(interaction.user.id))
        save_game(game)
        await interaction.user.send(f"You drew: {' '.join(drawn)}")
        bag_note = " 🎒 The bag is now empty!" if len(game.bag) == 0 else ""
        await interaction.response.send_message(f"🔤 Replacement tiles sent via DM.{bag_note}", ephemeral=True)
    except ScrabbleError as e:
        await interaction.response.send_message(f"❌ {e}", ephemeral=True)


@tree.command(name="exchange", description="Exchange tiles with the bag")
@app_commands.describe(letters="The letters you want to exchange")
async def exchange(interaction: discord.Interaction, letters: str):
    try:
        game.exchange_tiles(str(interaction.user.id), letters)
        save_game(game)
        await send_rack_dm(str(interaction.user.id))
        await interaction.response.send_message(
            f"🔄 {interaction.user.display_name} exchanged {len(letters)} tile(s)."
        )
    except ScrabbleError as e:
        await interaction.response.send_message(f"❌ {e}", ephemeral=True)

@tree.command(name="tilestatus", description="Show how many tiles remain in the bag")
async def status(interaction: discord.Interaction):
    if not game.started:
        await interaction.response.send_message("Game hasn't started yet — no bag to report on.")
        return

    remaining = len(game.bag)
    counts = Counter(game.bag)
    # Sort alphabetically, blanks last
    letters_sorted = sorted(counts.keys(), key=lambda l: (l == "?", l))
    breakdown = "  ".join(f"{letter}:{counts[letter]}" for letter in letters_sorted)

    await interaction.response.send_message(
        f"🎒 **Tile Bag Status**\n"
        f"Remaining: **{remaining}** tiles\n\n"
        f"{breakdown}"
    )

@tree.command(name="challenge", description="Resolve a challenge on a pending play")
@app_commands.describe(
    result="Did the challenge succeed or fail?",
    player="The player whose play is being challenged",
)
@app_commands.choices(
    result=[
        app_commands.Choice(name="success", value="success"),
        app_commands.Choice(name="fail", value="fail"),
    ]
)
async def challenge(
    interaction: discord.Interaction,
    result: app_commands.Choice[str],
    player: discord.Member,
):
    try:
        player_id = str(player.id)
        if result.value == "success":
            game.challenge_success(player_id)
            save_game(game)
            await send_rack_dm(player_id)
            await interaction.response.send_message(
                f"❌ Challenge succeeded — {player.display_name}'s tiles were returned.\n\n \"Thats not a word! 👎\" - Justin"
            )
        else:
            game.challenge_fail(player_id)
            game.draw_replacements(player_id)
            save_game(game)
            await send_rack_dm(player_id)
            bag_note = "\n\n🎒 The bag is now empty!" if len(game.bag) == 0 else ""
            await interaction.response.send_message(
                f"✅ Challenge failed — {player.display_name}'s play stands, "
                f"replacement tiles sent via DM.{bag_note}\n\n \"How is this a word??? 🤬\" - Andy"
            )
    except ScrabbleError as e:
        await interaction.response.send_message(f"❌ {e}", ephemeral=True)

@tree.command(name="end", description="End the current game")
async def end(interaction: discord.Interaction):
    global game
    try:
        game.end_game()
        summary = "\n".join(
            f"{p.name}: {p.rack_display()}" for p in game.players.values()
        )
        game = Game()
        save_game(game)
        await interaction.response.send_message(
            f"🏁 Game ended.\n\n**Final racks:**\n{summary}\n\nUse /newgame to start another."
        )
    except ScrabbleError as e:
        await interaction.response.send_message(f"❌ {e}", ephemeral=True)

def run_flask():
    port = int(os.getenv("PORT", 5000))
    flask_app.run(host="0.0.0.0", port=port)

# Run Flask in a background thread so the Discord bot's event loop
# (which needs the main thread) isn't blocked by it.
threading.Thread(target=run_flask, daemon=True).start()

client.run(TOKEN)