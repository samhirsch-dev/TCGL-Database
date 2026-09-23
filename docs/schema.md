# Database schema

One table holds everything. SQLite was chosen because the whole archive is
small (roughly 10 KB per log, so about 5 MB at 500 games), it needs no server,
and a backup is a file copy.

## `games`

| Column | Type | Null? | Source | Notes |
|---|---|---|---|---|
| `id` | INTEGER PK | no | auto | Autoincrement, never reused |
| `date` | TEXT | no | form, else today | ISO `YYYY-MM-DD`, so string sort equals date sort |
| `username` | TEXT | no | form | Your name in this log. Required |
| `players_deck` | TEXT | yes | form | Free text, e.g. `Dragapult ex` |
| `players_variant` | TEXT | yes | form | e.g. `Dusknoir`, `Dudunsparce` |
| `opponents_deck` | TEXT | yes | form | Free text |
| `opponents_variant` | TEXT | yes | form | Free text |
| `result` | TEXT | yes | form, else parsed | `W`, `L`, or NULL, enforced by a CHECK constraint |
| `turns` | INTEGER | yes | parsed | Count of turn headers in the log |
| `log_hash` | TEXT UNIQUE | no | computed | SHA-256 of the normalised log |
| `raw_log` | TEXT | no | form | Stored exactly as pasted |
| `created_at` | TEXT | no | auto | When the row was written, UTC |

Indexes: `date`, `result`, `(players_deck, players_variant)`, and
`(opponents_deck, opponents_variant)`.

## Why these columns and not others

**`username` is not redundant.** The log names both players but never says
which one is you. Without this column, a stored log cannot be re-interpreted
later: you could not recompute W/L, and no future deck detector could tell your
cards from your opponent's. It also keeps the archive correct if you ever play
on a second account.

**`log_hash` is unique, not just indexed.** Duplicate submissions would quietly
skew every win rate. The hash is taken after normalising unicode apostrophes
and line endings, so the same game pasted from two sources still collides. It
is computed from the log's content alone, which means it is stable across
re-imports and database rebuilds.

**`turns` is stored even though it is derivable.** It is the one derived number
worth denormalising: it is used for sorting and filtering, and recomputing it
would mean parsing every log on every list view.

**`went_first` is intentionally absent.** It is derivable from the log, it is
not needed to render the list view, and the parser already exposes it on the
game detail page. If you later decide you want to filter on it, see the next
section.

## Adding a column later

Nothing is lost by deciding later. The raw log is intact, so any field you
think of can be backfilled across the whole archive.

```bash
# 1. Add the column
sqlite3 /mnt/raid/tcg-log-vault/games.db \
  "ALTER TABLE games ADD COLUMN went_first INTEGER;"

# 2. Teach the parser to produce it (app/parser.py already computes
#    first_player), then extend derive() in scripts/backfill.py:
#      "went_first": 1 if parsed.first_player == row["username"] else 0

# 3. Preview, then apply
python -m scripts.backfill
python -m scripts.backfill --apply
```

At a few hundred games this takes well under a second. Bump `SCHEMA_VERSION`
in `app/db.py` and add the `ALTER TABLE` to `_SCHEMA` so fresh installs match.

Good candidates already available from the parser: `went_first`,
`mulligans`, `prizes_taken`, `prizes_conceded`, `win_condition`,
`knockouts_suffered`.

## Conventions worth keeping

- **Dates as ISO text.** SQLite has no date type; ISO strings sort and compare
  correctly and are readable in a CSV export.
- **NULL means "not recorded", empty string is never stored.** The app converts
  blank form fields to NULL so `WHERE opponents_deck IS NULL` reliably finds
  untagged games.
- **Deck names are free text.** No lookup table, because archetype names shift
  with each set rotation and a constrained list would age badly. If naming
  drifts (`Rampardos Fossil` vs `Rampardos ex`), normalise with a single
  `UPDATE`, or add an alias table when AI detection lands.

## Migrations

`schema_meta` holds a `schema_version` row. Current version: **1**. Migrations
are plain `ALTER TABLE` statements plus a backfill, as above. `init_db()` runs
on every start and is idempotent, so restarting the container is always safe.
