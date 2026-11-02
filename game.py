"""
game.py

Responsible for:
- Game state
- Players
- Racks
- Turns
- Pending plays
- Challenges
- Exchanges
- Turn order and clocks

No HTTP-specific code lives here — app.py imports Game and calls methods
on it in response to API requests.
"""

from collections import Counter
from typing import Dict, List, Optional
import math
import random
import time

from tiles import create_tile_bag, draw_tiles


MAX_PLAYERS = 14  # 100 tiles only stretches to 14 full racks

DEFAULT_SETTINGS = {
    "time_mode": "none",           # "none", "per_turn" or "total"
    "turn_seconds": 180,           # per_turn: time for each turn
    "total_seconds": 1500,         # total: each player's time for the game
    "overtime_penalty": 10,        # total: points lost per minute over
    "overtime_limit_minutes": 10,  # total: forfeit this far over
    "challenge_rule": "free",      # "free" or "lose_turn", for a failed challenge
    "challenge_seconds": 0,        # time to challenge a play, 0 for until the next turn
    "end_condition": "classic",    # score targets can be added with scoring
}


class ScrabbleError(Exception):
    """Raised for any invalid game action (bad player, bad tiles, etc.)."""
    pass


class Player:
    def __init__(self, player_id: str, name: str, token: str = ""):
        self.player_id = player_id
        self.name = name
        self.token = token  # secret proving a browser is this player
        self.rack: List[str] = []

    def rack_display(self) -> str:
        return " ".join(self.rack) if self.rack else "(empty)"

    def has_tiles(self, letters: str) -> bool:
        """Check if this player's rack can cover `letters` (case-insensitive).

        Blanks are only used if the player explicitly types '?' — we don't
        auto-substitute a blank for a missing letter, to keep bookkeeping
        unambiguous.
        """
        needed = Counter(letters.upper())
        available = Counter(self.rack)
        for letter, count in needed.items():
            if available[letter] < count:
                return False
        return True

    def remove_tiles(self, letters: str) -> None:
        """Remove given letters from the rack. Assumes has_tiles() already passed."""
        for letter in letters.upper():
            self.rack.remove(letter)

    def add_tiles(self, tiles: List[str]) -> None:
        self.rack.extend(tiles)


class PendingPlay:
    def __init__(self, player_id: str, letters: str, positions: List[tuple], new_letters: str, blanks: List[int] = None):
        self.player_id = player_id
        self.letters = letters.upper()       # the full word, for rendering
        self.positions = positions           # full list of (r, c, letter)
        self.new_letters = new_letters.upper()  # only the letters actually charged from rack
        self.blanks = set(blanks or [])  # indices into `positions`/`letters` that were blank tiles

    def __repr__(self):
        return f"PendingPlay(player_id={self.player_id!r}, letters={self.letters!r})"


class Game:
    def __init__(self, settings: dict = None):
        self.settings = {**DEFAULT_SETTINGS, **(settings or {})}
        self.players: Dict[str, Player] = {}
        self.bag: List[str] = []
        self.started: bool = False
        self.board: Dict[str, str] = {}  # "row,col" -> letter, only for CONFIRMED plays
        self.board_blanks: Dict[str, bool] = {}  # "row,col" -> True if that tile was a blank, only for CONFIRMED plays
        self.turn_order: List[str] = []
        self.turn_index: int = 0
        # At most one unresolved pending play per player.
        self.pending_plays: Dict[str, PendingPlay] = {}
        # How many replacement tiles a player is owed once their play is confirmed.
        self._confirmed_counts: Dict[str, int] = {}
        self.history: List[str] = []
        # Clock for the current turn. turn_started_at is None while paused.
        self.turn_started_at: Optional[float] = None
        self.turn_time_used: float = 0
        # total mode: seconds each player has left, negative once in overtime.
        self.time_banks: Dict[str, float] = {}
        self.forfeited: List[str] = []
        self.skip_next: List[str] = []  # players who lose their next turn
        # {"player_id": whose play, "challenger_id": who challenged it}
        self.challenge: Optional[dict] = None
        self.winner: Optional[str] = None
        self.pending_since: Optional[float] = None  # when the pending play was made

    # ------------------------------------------------------------------
    # Setup / lifecycle
    # ------------------------------------------------------------------

    def add_player(self, player_id: str, name: str, token: str = "") -> Player:
        if self.started:
            raise ScrabbleError("Cannot join — the game has already started.")
        if any(p.name.lower() == name.lower() for p in self.players.values()):
            raise ScrabbleError(f"{name} has already joined.")
        if len(self.players) >= MAX_PLAYERS:
            raise ScrabbleError(f"A game can have at most {MAX_PLAYERS} players.")
        player = Player(player_id, name, token)
        self.players[player_id] = player
        self.turn_order.append(player_id)
        return player

    def list_players(self) -> List[str]:
        return [p.name for p in self.players.values()]

    def start_game(self) -> None:
        if self.started:
            raise ScrabbleError("Game has already started.")
        if len(self.players) < 2:
            raise ScrabbleError("Need at least 2 players to start.")

        self.bag = create_tile_bag()
        for player in self.players.values():
            player.rack = draw_tiles(self.bag, 7)

        random.shuffle(self.turn_order)
        self.time_banks = {pid: self.settings["total_seconds"] for pid in self.turn_order}
        self.started = True
        self.turn_index = 0
        self._start_clock()
        names = ", ".join(self.players[pid].name for pid in self.turn_order)
        self.history.append(f"Game started. Turn order: {names}.")

    def _require_player(self, player_id: str) -> Player:
        player = self.players.get(player_id)
        if player is None:
            raise ScrabbleError("That player is not in this game.")
        return player

    def _require_started(self) -> None:
        if not self.started:
            raise ScrabbleError("Game has not started yet.")
        
    def _require_turn(self, player_id: str) -> Player:
        self._require_started()
        player = self._require_player(player_id)
        self._require_in_play()
        if player_id != self.current_player_id():
            current = self.players[self.current_player_id()].name
            raise ScrabbleError(f"It's {current}'s turn.")
        return player

    def _require_in_play(self) -> None:
        if self.winner is not None:
            raise ScrabbleError("This game is over.")
        if self.challenge is not None:
            raise ScrabbleError("Resolve the challenge first.")

    def _compute_positions(self, row: int, col: int, direction: str, letters: str) -> List[tuple]:
        if direction not in ("across", "down"):
            raise ScrabbleError("Direction must be 'across' or 'down'.")
        positions = []
        for i, letter in enumerate(letters):
            r = row + (i if direction == "down" else 0)
            c = col + (i if direction == "across" else 0)
            if not (0 <= r < 15 and 0 <= c < 15):
                raise ScrabbleError("That play goes off the board.")
            positions.append((r, c, letter))
        return positions
    
    def _write_to_board(self, pending: "PendingPlay") -> None:
        for i, (r, c, letter) in enumerate(pending.positions):
            key = f"{r},{c}"
            self.board[key] = letter
            if i in pending.blanks:
                self.board_blanks[key] = True

    # ------------------------------------------------------------------
    # Turn order
    # ------------------------------------------------------------------

    def current_player_id(self) -> Optional[str]:
        if not self.started or self.winner is not None:
            return None
        return self.turn_order[self.turn_index]

    def _advance_turn(self) -> None:
        self._settle_clock()
        count = len(self.turn_order)
        while True:
            self.turn_index = (self.turn_index + 1) % count
            player_id = self.turn_order[self.turn_index]
            if player_id in self.forfeited:
                continue
            if player_id in self.skip_next:
                self.skip_next.remove(player_id)
                self.history.append(f"{self.players[player_id].name} lost a turn.")
                continue
            break
        self._start_clock()

    def _return_turn_to(self, player_id: str) -> None:
        self._settle_clock()
        self.turn_index = self.turn_order.index(player_id)
        self._start_clock()

    def pass_turn(self, player_id: str) -> None:
        player = self._require_turn(player_id)
        self.auto_confirm_others(player_id)
        self.history.append(f"{player.name} passed.")
        self._advance_turn()

    def skip_turn(self, player_id: str) -> None:
        """Let others move on from an absent player. Only for untimed games,
        since timed games move on by themselves."""
        self._require_started()
        player = self._require_player(player_id)
        self._require_in_play()
        current = self.current_player_id()
        if self.settings["time_mode"] != "none":
            raise ScrabbleError("Timed games move on by themselves.")
        if player_id == current:
            raise ScrabbleError("Use Pass to skip your own turn.")
        self.auto_confirm_others(current)
        self.history.append(f"{player.name} skipped {self.players[current].name}'s turn.")
        self._advance_turn()

    # ------------------------------------------------------------------
    # Clocks
    # ------------------------------------------------------------------

    def clock_running(self) -> bool:
        return self.turn_started_at is not None

    def _turn_elapsed(self) -> float:
        running = time.time() - self.turn_started_at if self.clock_running() else 0
        return self.turn_time_used + running

    def _start_clock(self) -> None:
        self.turn_time_used = 0
        self.turn_started_at = time.time()

    def _pause_clock(self) -> None:
        self.turn_time_used = self._turn_elapsed()
        self.turn_started_at = None

    def _resume_clock(self) -> None:
        self.turn_started_at = time.time()

    def _settle_clock(self) -> None:
        """Charge the turn just finished to the player's game time."""
        player_id = self.turn_order[self.turn_index]
        self.time_banks[player_id] -= self._turn_elapsed()
        self.turn_time_used = 0
        self.turn_started_at = None

    def seconds_left(self, player_id: str) -> Optional[float]:
        """Game time left in total mode. Negative means overtime."""
        if self.settings["time_mode"] != "total" or not self.started:
            return None
        used = self._turn_elapsed() if player_id == self.current_player_id() else 0
        return self.time_banks[player_id] - used

    def turn_seconds_left(self) -> Optional[float]:
        if self.settings["time_mode"] != "per_turn" or self.current_player_id() is None:
            return None
        return self.settings["turn_seconds"] - self._turn_elapsed()

    def overtime_points(self, player_id: str) -> int:
        """Points lost so far, per minute or part of a minute over."""
        left = self.seconds_left(player_id)
        if left is None or left >= 0:
            return 0
        return math.ceil(-left / 60) * self.settings["overtime_penalty"]

    def challenge_seconds_left(self) -> Optional[float]:
        """Time left to challenge the pending play, if the game limits it."""
        limit = self.settings["challenge_seconds"]
        if not limit or not self.pending_plays or self.challenge is not None:
            return None
        return limit - (time.time() - self.pending_since)

    def _challenge_window_closed(self) -> bool:
        left = self.challenge_seconds_left()
        return left is not None and left <= 0

    def clock_expired(self) -> bool:
        return self._challenge_window_closed() or self._turn_expired()

    def _turn_expired(self) -> bool:
        current = self.current_player_id()
        if current is None or not self.clock_running():
            return False
        if self.settings["time_mode"] == "per_turn":
            return self.turn_seconds_left() <= 0
        if self.settings["time_mode"] == "total":
            return -self.seconds_left(current) >= self.settings["overtime_limit_minutes"] * 60
        return False

    def apply_clock(self) -> bool:
        """Act on an expired clock. The server has no background worker, so
        this runs whenever a game is read or changed. Handles one expiry at a
        time, so nobody loses a run of turns while everyone is away."""
        if not self.clock_expired():
            return False
        if self._challenge_window_closed():
            self.auto_confirm_others(None)
            return True
        current = self.current_player_id()
        name = self.players[current].name
        self.auto_confirm_others(current)
        if self.settings["time_mode"] == "per_turn":
            self.history.append(f"{name} ran out of time and lost the turn.")
            self._advance_turn()
        else:
            self._forfeit(current)
        return True

    def _forfeit(self, player_id: str) -> None:
        self.forfeited.append(player_id)
        self.history.append(f"{self.players[player_id].name} ran out of overtime and forfeited.")
        active = [pid for pid in self.turn_order if pid not in self.forfeited]
        if len(active) == 1:
            self._settle_clock()
            self.winner = active[0]
            self.history.append(f"{self.players[self.winner].name} wins.")
        else:
            self._advance_turn()

    # ------------------------------------------------------------------
    # Racks
    # ------------------------------------------------------------------

    def get_rack(self, player_id: str) -> str:
        self._require_started()
        player = self._require_player(player_id)
        return player.rack_display()

    # ------------------------------------------------------------------
    # Playing tiles
    # ------------------------------------------------------------------

    def play_tiles(self, player_id: str, letters: str, row: int, col: int, direction: str, blanks: List[int] = None) -> None:
        player = self._require_turn(player_id)

        # Playing closes the challenge window on the previous play. This goes
        # first so that a word can be built through that play's tiles. If the
        # checks below fail, the game isn't saved, so this doesn't stick.
        self.auto_confirm_others(player_id)

        if player_id in self.pending_plays:
            raise ScrabbleError(
                "You already have a pending play awaiting challenge resolution."
            )

        blanks = set(blanks or [])
        letters = letters.upper()
        positions = self._compute_positions(row, col, direction, letters)

        # Split into new tiles (need rack + bag) vs crossed tiles (must match board).
        new_letters = ""
        for i, (r, c, letter) in enumerate(positions):
            key = f"{r},{c}"
            existing = self.board.get(key)
            if existing is not None:
                if existing != letter:
                    raise ScrabbleError(
                        f"Letter mismatch at ({r},{c}): board already has "
                        f"'{existing}', but your word needs '{letter}' there."
                    )
            else:
                # A blank tile is charged as "?" against the rack, even though
                # the board itself will display the real assigned letter.
                new_letters += "?" if i in blanks else letter

        if not new_letters:
            raise ScrabbleError(
                "That play doesn't use any new tiles from your rack — "
                "did you mean to play somewhere else?"
            )

        if not player.has_tiles(new_letters):
            raise ScrabbleError(self._missing_letters_message(player, new_letters))

        player.remove_tiles(new_letters)
        self.pending_plays[player_id] = PendingPlay(player_id, letters, positions, new_letters, blanks=blanks)
        self.pending_since = time.time()
        self.history.append(f"{player.name} played {letters} at ({row},{col}) {direction} (pending).")
        self._advance_turn()
    
    def _missing_letters_message(self, player: Player, letters: str) -> str:
        needed = Counter(letters)
        available = Counter(player.rack)
        for letter, count in needed.items():
            if available[letter] < count:
                return f"You do not have enough {letter} tiles."
        return "You do not have those tiles."

    # ------------------------------------------------------------------
    # Drawing replacement tiles
    # ------------------------------------------------------------------

    def draw_replacements(self, player_id: str) -> List[str]:
        """Draw replacement tiles after a play is confirmed (no longer pending)."""
        self._require_started()
        player = self._require_player(player_id)

        if player_id in self.pending_plays:
            raise ScrabbleError(
                "Your play is still pending challenge resolution — "
                "resolve the challenge before drawing."
            )

        num_owed = self._confirmed_counts.get(player_id, 0)
        if num_owed <= 0:
            raise ScrabbleError("You have no confirmed play awaiting replacement tiles.")

        drawn = draw_tiles(self.bag, num_owed)
        player.add_tiles(drawn)
        self._confirmed_counts[player_id] = 0

        if len(drawn) < num_owed:
            self.history.append(
                f"{player.name} drew {len(drawn)} tile(s) — bag is now empty "
                f"(wanted {num_owed})."
            )
        else:
            self.history.append(f"{player.name} drew {len(drawn)} replacement tile(s).")
        return drawn

    # ------------------------------------------------------------------
    # Exchanges
    # ------------------------------------------------------------------

    def exchange_tiles(self, player_id: str, letters: str) -> None:
        player = self._require_turn(player_id)
        self.auto_confirm_others(player_id)

        if player_id in self.pending_plays:
            raise ScrabbleError("Resolve your pending play before exchanging tiles.")

        letters = letters.upper()
        if not player.has_tiles(letters):
            raise ScrabbleError(self._missing_letters_message(player, letters))
        if len(self.bag) < len(letters):
            raise ScrabbleError("Not enough tiles left in the bag to exchange.")

        player.remove_tiles(letters)
        self.bag.extend(letters)
        random.shuffle(self.bag)

        drawn = draw_tiles(self.bag, len(letters))
        player.add_tiles(drawn)
        self.history.append(f"{player.name} exchanged {len(letters)} tile(s).")
        self._advance_turn()

    # ------------------------------------------------------------------
    # Challenges
    # ------------------------------------------------------------------

    def _pending_player_id(self, actor_id: str) -> str:
        """The player whose play is pending, checking `actor_id` may act on it."""
        self._require_started()
        self._require_player(actor_id)
        if not self.pending_plays:
            raise ScrabbleError("There is no pending play.")
        player_id = next(iter(self.pending_plays))
        if player_id == actor_id:
            raise ScrabbleError("You can't resolve your own play.")
        return player_id

    def _confirm_play(self, player_id: str) -> None:
        """Make a pending play permanent and draw its replacement tiles."""
        pending = self._pop_pending(player_id)
        self._write_to_board(pending)
        self._confirmed_counts[player_id] = (
            self._confirmed_counts.get(player_id, 0) + len(pending.new_letters)
        )
        self.draw_replacements(player_id)

    def start_challenge(self, actor_id: str) -> None:
        """Pause all clocks while the players check the word, like neutralising
        the timer in a tournament game."""
        self._require_in_play()
        player_id = self._pending_player_id(actor_id)
        self.challenge = {"player_id": player_id, "challenger_id": actor_id}
        self._pause_clock()
        self.history.append(
            f"{self.players[actor_id].name} challenged "
            f"{self.pending_plays[player_id].letters}. Clocks paused."
        )

    def resolve_challenge(self, actor_id: str, valid: bool) -> None:
        """Only the NEWLY placed tiles go back to the rack if the word is
        rejected — tiles that were already on the board were never charged.
        The turn stays with the next player, so the player who made the play
        has lost their turn.
        """
        if self.challenge is None:
            raise ScrabbleError("There is no challenge to resolve.")
        player_id = self._pending_player_id(actor_id)
        challenger_id = self.challenge["challenger_id"]
        player = self.players[player_id]
        self.challenge = None
        self._resume_clock()

        if not valid:
            pending = self._pop_pending(player_id)
            player.add_tiles(list(pending.new_letters))
            self.history.append(f"{pending.letters} was not valid. {player.name} loses the turn.")
            return

        letters = self.pending_plays[player_id].letters
        self._confirm_play(player_id)
        self.history.append(f"{letters} was valid and stands.")
        if self.settings["challenge_rule"] == "lose_turn":
            if challenger_id == self.current_player_id():
                self.history.append(f"{self.players[challenger_id].name} lost a turn.")
                self._advance_turn()
            else:
                self.skip_next.append(challenger_id)

    def _pop_pending(self, player_id: str) -> PendingPlay:
        self._require_player(player_id)
        pending = self.pending_plays.pop(player_id, None)
        if pending is None:
            raise ScrabbleError("That player has no pending play to resolve.")
        return pending
    
    def auto_confirm_others(self, acting_player_id: Optional[str]) -> List[str]:
        """When `acting_player_id` takes their turn, any pending plays belonging
        to OTHER players are treated as unchallenged and become permanent.
        Returns the list of player_ids that got auto-confirmed.
        """
        resolved = []
        for player_id, pending in list(self.pending_plays.items()):
            if player_id == acting_player_id:
                continue
            self._confirm_play(player_id)
            resolved.append(player_id)
            self.history.append(
                f"{self.players[player_id].name}'s play of {pending.letters} "
                "auto-confirmed (unchallenged)."
            )
        return resolved

    def undo_last_play(self, player_id: str) -> str:
        """Undo the player's current pending play, returning tiles to their rack."""
        self._require_player(player_id)
        if self.challenge is not None:
            raise ScrabbleError("You can't undo a play that's being challenged.")
        pending = self.pending_plays.pop(player_id, None)
        if pending is None:
            raise ScrabbleError("You have no pending play to undo.")
        player = self.players[player_id]
        player.add_tiles(list(pending.new_letters)) # only return the new letters
        self.history.append(f"{player.name} undid their play of {pending.letters}.")
        self._return_turn_to(player_id)
        return pending.letters
    
    def end_game(self) -> None:
        """Ends the current game. Does not reset — caller should discard
        this Game instance and create a fresh one if a new game is wanted.
        """
        if not self.started:
            raise ScrabbleError("There's no active game to end.")
        self.started = False
        self.history.append("Game ended.")