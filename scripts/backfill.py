#!/usr/bin/env python3
"""Re-derive stored fields from the raw logs.

Run this after changing the parser, or after adding a new column that can be
computed from a log. It never touches raw_log or log_hash.

Usage:
    python -m scripts.backfill              # report what would change
    python -m scripts.backfill --apply      # write the changes

Adding a new derived column, end to end:
    1. ALTER TABLE games ADD COLUMN went_first INTEGER;   (see docs/schema.md)
    2. Extend UPDATES below with the new field.
    3. python -m scripts.backfill --apply
"""

from __future__ import annotations

import argparse
import sys

from app import db
from app.parser import LogParseError, parse


def derive(row) -> dict:
    """Fields that should be recomputed from the raw log."""
    parsed = parse(row["raw_log"])
    return {
        "turns": parsed.turns,
        "result": parsed.result_for(row["username"]),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write changes instead of reporting")
    ap.add_argument(
        "--overwrite-result",
        action="store_true",
        help="also replace a result you set by hand (off by default)",
    )
    args = ap.parse_args()

    db.init_db()
    conn = db.connect()
    rows = conn.execute("SELECT * FROM games ORDER BY id").fetchall()

    changed = 0
    for row in rows:
        try:
            values = derive(row)
        except LogParseError as exc:
            print(f"game #{row['id']}: cannot parse ({exc})")
            continue

        updates = {}
        for key, new in values.items():
            if new is None:
                continue
            if key == "result" and row["result"] and not args.overwrite_result:
                continue
            if row[key] != new:
                updates[key] = new

        if not updates:
            continue

        changed += 1
        summary = ", ".join(f"{k}: {row[k]!r} -> {v!r}" for k, v in updates.items())
        print(f"game #{row['id']}: {summary}")
        if args.apply:
            assignments = ", ".join(f"{k} = ?" for k in updates)
            conn.execute(
                f"UPDATE games SET {assignments} WHERE id = ?",
                [*updates.values(), row["id"]],
            )

    if args.apply:
        conn.commit()
        print(f"\nUpdated {changed} game(s).")
    else:
        print(f"\n{changed} game(s) would change. Re-run with --apply to write them.")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
