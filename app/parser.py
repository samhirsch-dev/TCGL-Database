"""Parsing for Pokemon TCG Live battle logs.

The parser is deliberately conservative: it reads only the lines it is sure
about and reports what it could not determine, rather than guessing. Every
field it produces can be re-derived at any time from the stored raw log, so
adding new fields later is a matter of extending this module and re-running it
over the archive (see scripts/backfill.py).
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Optional


class LogParseError(ValueError):
    """Raised when the text does not look like a battle log at all."""


# Pokemon TCG Live mixes straight and curly apostrophes in the same export.
# Normalising them once keeps every regex below simple.
_APOSTROPHES = {
    "\u2018": "'",
    "\u2019": "'",
    "\u201b": "'",
    "\u02bc": "'",
}

TURN_RE = re.compile(r"^(?P<player>.+?)'s Turn$")
OPENING_HAND_RE = re.compile(r"^(?P<player>.+?) drew 7 cards for the opening hand\.$")
WIN_RE = re.compile(r"(?P<player>\S[^.]*?) wins\.")
CONCEDE_RE = re.compile(r"^(?P<player>.+?) conceded\.?$")
COIN_CHOICE_RE = re.compile(r"^(?P<player>.+?) decided to go (?P<choice>first|second)\.$")
MULLIGAN_RE = re.compile(r"^(?P<player>.+?) took (?P<count>a|\d+) mulligans?\.$")
PRIZE_RE = re.compile(r"^(?P<player>.+?) took (?P<count>a|\d+) Prize cards?\.$")
KO_RE = re.compile(r"^(?P<player>.+?)'s (?P<pokemon>.+?) was Knocked Out!$")

# Card-name capture, used for future deck detection. Not stored in the database.
PLAYED_RE = re.compile(r"^(?P<player>.+?) played (?P<card>.+?)(?: to the .+?)?\.$")
EVOLVED_RE = re.compile(r"^(?P<player>.+?) evolved (?P<from>.+?) to (?P<to>.+?)(?: on the .+?| in the .+?)?\.$")
ATTACHED_RE = re.compile(r"^(?P<player>.+?) attached (?P<card>.+?) to .+?\.$")


def normalize(text: str) -> str:
    """Normalise unicode and line endings without changing the log's meaning."""
    text = unicodedata.normalize("NFC", text)
    for fancy, plain in _APOSTROPHES.items():
        text = text.replace(fancy, plain)
    return text.replace("\r\n", "\n").replace("\r", "\n")


def log_hash(raw_log: str) -> str:
    """Stable SHA-256 of the log, used to reject duplicate submissions.

    Hashing the *normalised* text means the same game pasted twice is caught
    even if the whitespace or apostrophes differ between pastes.
    """
    return hashlib.sha256(normalize(raw_log).strip().encode("utf-8")).hexdigest()


@dataclass
class ParsedLog:
    players: list[str] = field(default_factory=list)
    turns: int = 0
    winner: Optional[str] = None
    loser: Optional[str] = None
    win_condition: Optional[str] = None  # "prizes", "concession", or None
    first_player: Optional[str] = None
    mulligans: dict[str, int] = field(default_factory=dict)
    prizes_taken: dict[str, int] = field(default_factory=dict)
    knockouts: dict[str, int] = field(default_factory=dict)  # KOs *suffered* by player
    cards_seen: dict[str, list[str]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def result_for(self, username: str) -> Optional[str]:
        """Return 'W' or 'L' for the given player, or None if undetermined."""
        if self.winner is None:
            return None
        return "W" if self.winner == username else "L"


def _strip_bullet(line: str) -> str:
    """Sub-lines are indented and prefixed with '-' or a bullet character."""
    return line.lstrip().lstrip("-\u2022").strip()


def _collect_cards(lines: list[str], players: list[str]) -> dict[str, list[str]]:
    """Collect the card names each player revealed.

    Only lines that begin with a *known* player name are considered, so nested
    sub-lines such as "- X drew Y and played it to the Bench." cannot invent a
    third player. These cards are not stored in the database today; they exist
    so deck detection can be added later without changing the archive.
    """
    cards: dict[str, list[str]] = {p: [] for p in players}
    for raw_line in lines:
        body = _strip_bullet(raw_line)
        player = next((p for p in players if body.startswith(p + " ")), None)
        if player is None:
            continue
        for pattern, group in ((PLAYED_RE, "card"), (EVOLVED_RE, "to"), (ATTACHED_RE, "card")):
            m = pattern.match(body)
            if m and m.group("player") == player:
                cards[player].append(m.group(group))
                break
    return cards


def parse(raw_log: str) -> ParsedLog:
    """Parse a battle log into structured fields.

    Raises LogParseError if the text has no recognisable player turns, which is
    the cheapest reliable signal that someone pasted the wrong thing.
    """
    text = normalize(raw_log)
    lines = [line.rstrip() for line in text.split("\n")]
    result = ParsedLog()

    players: list[str] = []

    def see_player(name: str) -> None:
        if name and name not in players:
            players.append(name)

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue

        # Indented sub-lines describe the effects of the line above them. They
        # repeat player names, so they are used only for card capture.
        is_sub = raw_line.startswith((" ", "\t", "-")) or raw_line.lstrip().startswith(("-", "\u2022"))
        body = _strip_bullet(line) if is_sub else line

        if not is_sub:
            m = TURN_RE.match(body)
            if m:
                result.turns += 1
                see_player(m.group("player"))
                continue

            m = OPENING_HAND_RE.match(body)
            if m:
                see_player(m.group("player"))
                continue

            m = COIN_CHOICE_RE.match(body)
            if m:
                see_player(m.group("player"))
                result.first_player = m.group("player") if m.group("choice") == "first" else None
                # If they chose to go second, the opponent went first. The
                # opponent may not be known yet, so this is resolved below.
                if m.group("choice") == "second":
                    result.first_player = f"!not:{m.group('player')}"
                continue

            m = MULLIGAN_RE.match(body)
            if m:
                player = m.group("player")
                see_player(player)
                count = 1 if m.group("count") == "a" else int(m.group("count"))
                # TCG Live announces the first mulligan individually, then
                # restates the running total on each further one ("took a
                # mulligan." then "took 3 mulligans."). Taking the max instead
                # of summing avoids double-counting that restated total.
                result.mulligans[player] = max(result.mulligans.get(player, 0), count)
                continue

            m = PRIZE_RE.match(body)
            if m:
                player = m.group("player")
                see_player(player)
                count = 1 if m.group("count") == "a" else int(m.group("count"))
                result.prizes_taken[player] = result.prizes_taken.get(player, 0) + count
                continue

            m = KO_RE.match(body)
            if m:
                player = m.group("player")
                see_player(player)
                result.knockouts[player] = result.knockouts.get(player, 0) + 1
                continue

            m = CONCEDE_RE.match(body)
            if m:
                see_player(m.group("player"))
                result.loser = m.group("player")
                result.win_condition = "concession"
                continue

            m = WIN_RE.search(body)
            if m:
                winner = m.group("player").split(". ")[-1].strip()
                see_player(winner)
                result.winner = winner
                if result.win_condition is None:
                    result.win_condition = "prizes" if "Prize" in body else "unknown"
                continue

    if result.turns == 0:
        raise LogParseError(
            "No player turns found. Make sure you pasted a full battle log "
            "exported from Pokemon TCG Live."
        )

    result.players = players

    # Resolve "went second" into the actual first player now that both names
    # are known.
    if result.first_player and result.first_player.startswith("!not:"):
        other = result.first_player[len("!not:"):]
        opponents = [p for p in players if p != other]
        result.first_player = opponents[0] if len(opponents) == 1 else None

    # A concession names the loser; the winner is the other player.
    if result.winner is None and result.loser is not None:
        opponents = [p for p in players if p != result.loser]
        if len(opponents) == 1:
            result.winner = opponents[0]

    if result.winner is not None and result.loser is None:
        opponents = [p for p in players if p != result.winner]
        if len(opponents) == 1:
            result.loser = opponents[0]

    result.cards_seen = _collect_cards(lines, players)

    if result.winner is None:
        result.warnings.append(
            "No winner line found. The log may be truncated, or the game may "
            "have ended in a way this parser does not recognise yet."
        )
    if len(players) != 2:
        result.warnings.append(
            f"Expected 2 players, found {len(players)}: {', '.join(players) or 'none'}."
        )

    return result
