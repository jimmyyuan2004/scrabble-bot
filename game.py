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

No Discord-specific code lives here — bot.py should import Game/Player
from this file and call methods on them in response to slash commands.
"""

from collections import Counter
from typing import Dict, List
import random

from discord import player

from tiles import create_tile_bag, draw_tiles


class ScrabbleError(Exception):
    """Raised for any invalid game action (bad player, bad tiles, etc.)."""
    pass


class Player:
    def __init__(self, player_id: str, name: str):
        self.player_id = player_id
        self.name = name
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
    def __init__(self, player_id: str, letters: str, positions: List[tuple], new_letters: str):
        self.player_id = player_id
        self.letters = letters.upper()       # the full word, for rendering
        self.positions = positions           # full list of (r, c, letter)
        self.new_letters = new_letters.upper()  # only the letters actually charged from rack

    def __repr__(self):
        return f"PendingPlay(player_id={self.player_id!r}, letters={self.letters!r})"


class Game:
    def __init__(self):
        self.players: Dict[str, Player] = {}
        self.bag: List[str] = []
        self.started: bool = False
        self.board: Dict[str, str] = {}  # "row,col" -> letter, only for CONFIRMED plays
        self.turn_order: List[str] = []
        self.turn_index: int = 0
        # At most one unresolved pending play per player.
        self.pending_plays: Dict[str, PendingPlay] = {}
        # How many replacement tiles a player is owed once their play is confirmed.
        self._confirmed_counts: Dict[str, int] = {}
        self.history: List[str] = []

    # ------------------------------------------------------------------
    # Setup / lifecycle
    # ------------------------------------------------------------------

    def add_player(self, player_id: str, name: str) -> Player:
        if self.started:
            raise ScrabbleError("Cannot join — the game has already started.")
        if player_id in self.players:
            raise ScrabbleError(f"{name} has already joined.")
        player = Player(player_id, name)
        self.players[player_id] = player
        self.turn_order.append(player_id)
        return player

    def list_players(self) -> List[str]:
        return [p.name for p in self.players.values()]

    def start_game(self) -> None:
        if self.started:
            raise ScrabbleError("Game has already started.")
        if len(self.players) < 1:
            raise ScrabbleError("Need at least 2 players to start.")

        self.bag = create_tile_bag()
        for player in self.players.values():
            player.rack = draw_tiles(self.bag, 7)

        self.started = True
        self.turn_index = 0
        self.history.append("Game started.")

    def _require_player(self, player_id: str) -> Player:
        player = self.players.get(player_id)
        if player is None:
            raise ScrabbleError("You are not in this game. Use /join first.")
        return player

    def _require_started(self) -> None:
        if not self.started:
            raise ScrabbleError("Game has not started yet. Use /start first.")
        
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
        for r, c, letter in pending.positions:
            self.board[f"{r},{c}"] = letter

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

    def play_tiles(self, player_id: str, letters: str, row: int, col: int, direction: str) -> None:
        self._require_started()
        player = self._require_player(player_id)

        if player_id in self.pending_plays:
            raise ScrabbleError(
                "You already have a pending play awaiting challenge resolution."
            )

        letters = letters.upper()
        positions = self._compute_positions(row, col, direction, letters)

        # Split into new tiles (need rack + bag) vs crossed tiles (must match board).
        new_letters = ""
        for r, c, letter in positions:
            key = f"{r},{c}"
            existing = self.board.get(key)
            if existing is not None:
                if existing != letter:
                    raise ScrabbleError(
                        f"Letter mismatch at ({r},{c}): board already has "
                        f"'{existing}', but your word needs '{letter}' there."
                    )
            else:
                new_letters += letter

        if not new_letters:
            raise ScrabbleError(
                "That play doesn't use any new tiles from your rack — "
                "did you mean to play somewhere else?"
            )

        if not player.has_tiles(new_letters):
            raise ScrabbleError(self._missing_letters_message(player, new_letters))

        self.auto_confirm_others(player_id)

        player.remove_tiles(new_letters)
        self.pending_plays[player_id] = PendingPlay(player_id, letters, positions, new_letters)
        self.history.append(f"{player.name} played {letters} at ({row},{col}) {direction} (pending).")
    
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
        self._require_started()
        player = self._require_player(player_id)

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

    # ------------------------------------------------------------------
    # Challenges
    # ------------------------------------------------------------------

    def challenge_success(self, player_id: str) -> None:
        """Challenge succeeded: only the NEWLY placed tiles are returned to the
        player's rack — tiles that were already on the board (crossed letters)
        were never charged, so there's nothing to return for those.
        """
        pending = self._pop_pending(player_id)
        player = self.players[player_id]
        player.add_tiles(list(pending.new_letters))
        self.history.append(f"Challenge against {player.name} succeeded — play voided.")

    def challenge_fail(self, player_id: str) -> None:
        """Challenge failed: play becomes permanent. Marks the player as owed
        replacement tiles — call draw_replacements() next (or have bot.py
        auto-call it right after this)."""
        pending = self._pop_pending(player_id)
        self._write_to_board(pending)
        player = self.players[player_id]
        self._confirmed_counts[player_id] = len(pending.new_letters)
        self.history.append(f"Challenge against {player.name} failed — play stands.")

    def _pop_pending(self, player_id: str) -> PendingPlay:
        self._require_player(player_id)
        pending = self.pending_plays.pop(player_id, None)
        if pending is None:
            raise ScrabbleError("That player has no pending play to resolve.")
        return pending
    
    def auto_confirm_others(self, acting_player_id: str) -> List[str]:
        """When `acting_player_id` starts a new play, any pending plays belonging
        to OTHER players are treated as unchallenged and become permanent.
        Returns the list of player_ids that got auto-confirmed (so bot.py can
        draw their replacement tiles and notify them).
        """
        resolved = []
        for player_id, pending in list(self.pending_plays.items()):
            if player_id == acting_player_id:
                continue
            self.pending_plays.pop(player_id)
            self._write_to_board(pending)
            self._confirmed_counts[player_id] = (
                self._confirmed_counts.get(player_id, 0) + len(pending.new_letters)
            )
            resolved.append(player_id)
            self.history.append(
                f"{self.players[player_id].name}'s play of {pending.letters} "
                "auto-confirmed (unchallenged)."
            )
        return resolved

    def undo_last_play(self, player_id: str) -> str:
        """Undo the player's current pending play, returning tiles to their rack."""
        self._require_player(player_id)
        pending = self.pending_plays.pop(player_id, None)
        if pending is None:
            raise ScrabbleError("You have no pending play to undo.")
        player = self.players[player_id]
        player.add_tiles(list(pending.new_letters)) # only return the new letters
        self.history.append(f"{player.name} undid their play of {pending.letters}.")
        return pending.letters
    
    def end_game(self) -> None:
        """Ends the current game. Does not reset — caller should discard
        this Game instance and create a fresh one if a new game is wanted.
        """
        if not self.started:
            raise ScrabbleError("There's no active game to end.")
        self.started = False
        self.history.append("Game ended.")