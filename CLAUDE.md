# Log vault

Self-hosted archive for Pokémon TCG Live battle logs. A web form takes a pasted
log plus metadata, parses it, and stores it in SQLite with the raw text intact.
Single user, runs as a Docker container on an Ubuntu home server, reached via
Cloudflare Tunnel behind Cloudflare Access.

## Commands

```bash
pip install -r requirements.txt pytest httpx
python -m pytest -q                                   # 25 tests, all must pass
TCG_DB_PATH=./data/games.db uvicorn app.main:app --reload --port 8000
python -m scripts.backfill                            # dry run
python -m scripts.backfill --apply                    # re-derive fields from raw logs
docker compose up -d --build                          # deploy
```

## Layout

- `app/parser.py` — log parsing. Imports nothing else from the app; keep it that way.
- `app/db.py` — schema, `init_db()` (idempotent, runs every start), queries.
- `app/main.py` — FastAPI routes, form handling, CSV/JSON export.
- `app/templates/` — Jinja2. Styles are inline in `base.html`; no static files, no JS build.
- `scripts/backfill.py` — recompute derived columns across the archive.
- `tests/sample_log.txt` — a real log; tests assert known values (13 turns,
  super-victini13 wins 6–0 on knockouts, went first, 3 mulligans).
- `docs/` — schema, log format, deployment, queries, roadmap. Update the
  relevant doc when behaviour changes.

## Core design rules

1. **The raw log is the source of truth.** Never modify `raw_log` or
   `log_hash` after insert. Every other column must be re-derivable from the
   raw log plus `username`.
2. **Don't store what you can derive, unless it's filtered on constantly.**
   `turns` is the only stored derived field. `went_first`, mulligans, prizes
   and knockouts are parsed on demand and shown on the detail page. New
   derived columns go in via `ALTER TABLE` + `scripts/backfill.py` (see
   `docs/schema.md`), with `SCHEMA_VERSION` bumped in `app/db.py`.
3. **`username` is required** — it is the only way to tell which side of a log
   is the user. Reject logs where it doesn't appear.
4. **Duplicates are impossible, not unlikely.** `log_hash` is UNIQUE and taken
   after normalisation (curly apostrophes → straight, CRLF → LF, strip).
5. **Blank form fields become NULL, never empty strings.**
6. **Deck and variant are free text.** No lookup tables; archetype names shift
   every rotation. Variant splits one archetype into builds
   (e.g. `Dragapult ex` / `Dusknoir` vs `Dudunsparce`).

## Parser gotchas

- The export mixes `'` and `’`. Always call `normalize()` before matching.
- Players are collected **only** from authoritative lines (turn headers, opening
  hands, mulligans, prizes, KOs, concessions, the win line). Sub-lines like
  `- X drew Y and played it to the Bench.` otherwise invent a phantom player.
  Card capture is a second pass that only accepts lines starting with a known
  player name. There's a test for this; don't regress it.
- Mulligans past the first are reported as a restated running total
  (`took a mulligan.` then `took 3 mulligans.`), not one line per mulligan.
  `MULLIGAN_RE` takes the max seen per player rather than summing — summing
  double-counts. There's a test for this; don't regress it.
- `parse()` raises `LogParseError` only when there are zero turn headers.
  Anything else odd goes in `warnings` so the log still gets archived.
- Opponent cards are only what was revealed — treat as partial.

## Deployment constraints

- SQLite must be on a **local** filesystem (the RAID 1 array is bind-mounted
  at `/data`). Never put the DB on SMB/NFS.
- WAL mode is on. Backups use `sqlite3 ... ".backup"`, not `cp`.
- Port binds to `127.0.0.1:8088` only; external access is via the tunnel.
- The app has **no authentication**. Don't add routes that assume otherwise,
  and don't suggest exposing it without Cloudflare Access.

## Deferred on purpose (see docs/roadmap.md)

- AI deck/variant detection: rules first, model fallback on the card list only,
  store source + confidence, manual entry always wins, alias table for naming
  drift. `parser.cards_seen` already provides per-player card lists.
- AI access to the database: read-only (`mode=ro`) MCP server or the existing
  `/api/games` endpoint.

Don't start either without being asked.

## Style

Python 3.12, type hints, small functions, docstrings that explain *why*.
UI copy is sentence case, plain verbs, errors say what to fix.
