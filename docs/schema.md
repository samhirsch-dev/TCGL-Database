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
| `game_mode` | TEXT | yes | form | `ranked`, `casual`, or NULL (not recorded), enforced by a CHECK constraint. Added in v2 |
| `rank_points` | INTEGER | yes | form | Ranked points going *into* the game, 0 or more. Ranked games only. Added in v2 |

Columns added after v1 sit after `created_at` because SQLite can only append
columns to an existing table.

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

**`game_mode` and `rank_points` are typed by hand.** The log never says whether
a game was ranked, so neither can be derived. They are two columns rather than
one because "no points recorded" and "not ranked" are different facts: a ranked
game where you skipped the points is still ranked. The app enforces the rules
between them:

- Points with the mode left blank save as `ranked`, since only ranked play has
  points.
- Points on a `casual` game are rejected.
- `ranked` with no points is allowed.

`rank_points` is the value *before* the game, so the change a game caused is
the next ranked game's points minus this one's (see
[queries.md](queries.md)).

**`went_first` is intentionally absent.** It is derivable from the log, it is
not needed to render the list view, and the parser already exposes it on the
game detail page. If you later decide you want to filter on it, see the next
section.

## Adding a column later

Nothing is lost by deciding later. The raw log is intact, so any field you
think of can be backfilled across the whole archive.

1. Add the column to `_ADDED_COLUMNS` in `app/db.py` and bump
   `SCHEMA_VERSION`:

   ```python
   "went_first": "INTEGER CHECK (went_first IN (0, 1) OR went_first IS NULL)",
   ```

   `init_db()` adds any listed column a database lacks, on every start. Existing
   databases are upgraded by the next restart; no manual `ALTER TABLE` is
   needed. Don't edit the `CREATE TABLE` in `_SCHEMA`. That would give fresh
   installs the column twice, once from `_SCHEMA` and once from the loop.

2. If the value comes from the log, teach the parser to produce it
   (`app/parser.py` already computes `first_player`), then extend `derive()` in
   `scripts/backfill.py`:

   ```python
   "went_first": 1 if parsed.first_player == row["username"] else 0,
   ```

3. Deploy, then preview and apply the backfill:

   ```bash
   python -m scripts.backfill
   python -m scripts.backfill --apply
   ```

At a few hundred games this takes well under a second. Fields typed into the
form, like `game_mode`, skip steps 2 and 3; old rows stay NULL ("not recorded").

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

`schema_meta` holds a `schema_version` row. Current version: **2**.

| Version | Change |
|---|---|
| 1 | Initial `games` table |
| 2 | Added `game_mode` and `rank_points` |

Migrations only ever add columns, via `_ADDED_COLUMNS` as above. `init_db()`
runs on every start and is idempotent, so restarting the container is always
safe. Take a backup before deploying a version that adds columns anyway: the
upgrade writes to the live database.
