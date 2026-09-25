"""SQLite storage for battle logs.

Design notes:

* One table, `games`, one row per match. The raw log is stored verbatim, so any
  field derived from it can be recomputed later.
* `log_hash` is UNIQUE, which makes duplicate submissions impossible rather
  than merely unlikely.
* `schema_version` records migrations so new columns can be added safely.
* WAL mode is on: it survives an unclean container stop far better than the
  default rollback journal.
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional

DB_PATH = Path(os.environ.get("TCG_DB_PATH", "/data/games.db"))

SCHEMA_VERSION = 2

_SCHEMA = """
CREATE TABLE IF NOT EXISTS games (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    date               TEXT    NOT NULL,
    username           TEXT    NOT NULL,
    players_deck       TEXT,
    players_variant    TEXT,
    opponents_deck     TEXT,
    opponents_variant  TEXT,
    result             TEXT    CHECK (result IN ('W', 'L') OR result IS NULL),
    turns              INTEGER,
    log_hash           TEXT    NOT NULL UNIQUE,
    raw_log            TEXT    NOT NULL,
    created_at         TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_games_date       ON games (date);
CREATE INDEX IF NOT EXISTS idx_games_opp_deck   ON games (opponents_deck, opponents_variant);
CREATE INDEX IF NOT EXISTS idx_games_my_deck    ON games (players_deck, players_variant);
CREATE INDEX IF NOT EXISTS idx_games_result     ON games (result);

CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

# Columns added after v1. CREATE TABLE IF NOT EXISTS never alters an existing
# table, so init_db() adds whichever of these a database lacks. Every install,
# new or old, gets them the same way, so they are defined only here.
_ADDED_COLUMNS = {
    "rank_points": "INTEGER CHECK (rank_points >= 0 OR rank_points IS NULL)",
}

EDITABLE_FIELDS = {
    "date",
    "players_deck",
    "players_variant",
    "opponents_deck",
    "opponents_variant",
    "result",
    "rank_points",
}


class DuplicateLogError(ValueError):
    """Raised when a log with the same hash is already stored."""

    def __init__(self, existing_id: int):
        self.existing_id = existing_id
        super().__init__(f"This log is already saved as game #{existing_id}.")


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


@contextmanager
def cursor() -> Iterator[sqlite3.Cursor]:
    conn = connect()
    try:
        yield conn.cursor()
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with cursor() as cur:
        cur.executescript(_SCHEMA)
        existing = {row["name"] for row in cur.execute("PRAGMA table_info(games)")}
        for name, definition in _ADDED_COLUMNS.items():
            if name not in existing:
                cur.execute(f"ALTER TABLE games ADD COLUMN {name} {definition}")
        cur.execute(
            "INSERT INTO schema_meta (key, value) VALUES ('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(SCHEMA_VERSION),),
        )


def insert_game(game: dict[str, Any]) -> int:
    fields = [
        "date",
        "username",
        "players_deck",
        "players_variant",
        "opponents_deck",
        "opponents_variant",
        "result",
        "turns",
        "rank_points",
        "log_hash",
        "raw_log",
    ]
    placeholders = ", ".join("?" for _ in fields)
    with cursor() as cur:
        existing = cur.execute(
            "SELECT id FROM games WHERE log_hash = ?", (game["log_hash"],)
        ).fetchone()
        if existing:
            raise DuplicateLogError(existing["id"])
        cur.execute(
            f"INSERT INTO games ({', '.join(fields)}) VALUES ({placeholders})",
            [game.get(f) for f in fields],
        )
        return int(cur.lastrowid)


def list_games(limit: int = 100, offset: int = 0) -> list[sqlite3.Row]:
    with cursor() as cur:
        return cur.execute(
            "SELECT id, date, username, players_deck, players_variant, "
            "opponents_deck, opponents_variant, result, turns, rank_points, "
            "created_at "
            "FROM games ORDER BY date DESC, id DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()


def get_game(game_id: int) -> Optional[sqlite3.Row]:
    with cursor() as cur:
        return cur.execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone()


def delete_game(game_id: int) -> bool:
    with cursor() as cur:
        cur.execute("DELETE FROM games WHERE id = ?", (game_id,))
        return cur.rowcount > 0


def update_game(game_id: int, fields: dict[str, Any]) -> bool:
    """Update editable metadata. The raw log and hash are never changed."""
    editable = {k: v for k, v in fields.items() if k in EDITABLE_FIELDS}
    if not editable:
        return False
    assignments = ", ".join(f"{k} = ?" for k in editable)
    with cursor() as cur:
        cur.execute(
            f"UPDATE games SET {assignments} WHERE id = ?",
            [*editable.values(), game_id],
        )
        return cur.rowcount > 0


def stats() -> dict[str, Any]:
    with cursor() as cur:
        totals = cur.execute(
            "SELECT COUNT(*) AS games, "
            "SUM(result = 'W') AS wins, "
            "SUM(result = 'L') AS losses "
            "FROM games"
        ).fetchone()
        by_matchup = cur.execute(
            "SELECT COALESCE(opponents_deck, 'Unknown') AS deck, "
            "COALESCE(NULLIF(opponents_variant, ''), '') AS variant, "
            "COUNT(*) AS games, SUM(result = 'W') AS wins "
            "FROM games GROUP BY deck, variant ORDER BY games DESC LIMIT 10"
        ).fetchall()
    return {
        "games": totals["games"] or 0,
        "wins": totals["wins"] or 0,
        "losses": totals["losses"] or 0,
        "by_matchup": [dict(row) for row in by_matchup],
    }
