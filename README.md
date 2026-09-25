# Log vault

Long-term storage for Pokémon TCG Live battle logs. Paste a log into a web
form, and it is parsed, deduplicated, and stored in SQLite alongside the
original text, so you can query your match history years from now.

The design rule behind everything here: **the raw log is the source of truth.**
Stored columns are conveniences that can be recomputed at any time. Nothing you
save today limits what you can extract from the archive later.

---

## Contents

| Document | What it covers |
|---|---|
| This file | Install, run, use, and back up |
| [docs/schema.md](docs/schema.md) | Table layout, why each column exists, how to add more |
| [docs/log-format.md](docs/log-format.md) | What the parser reads out of a log and how reliably |
| [docs/deployment.md](docs/deployment.md) | Docker, Cloudflare Tunnel, Cloudflare Access, backups |
| [docs/queries.md](docs/queries.md) | SQL for win rates, matchups, and turn counts |
| [docs/roadmap.md](docs/roadmap.md) | Where AI deck detection and database access plug in |

---

## What it does today

- A single web page: paste a log, add your username, optionally set the date,
  decks, variants, and result.
- Parses the turn count and the winner, and turns the winner into W or L for
  you.
- Refuses duplicates. The same log pasted twice is rejected, not double-counted.
- Refuses logs where your username does not appear, which catches typos.
- Stores the log verbatim, in full.
- Browsing, editing, CSV export, and a JSON endpoint.

## What it deliberately does not do yet

- No AI deck detection. The deck and variant fields are typed by hand for now.
  Everything needed to add detection later is already in place: see
  [docs/roadmap.md](docs/roadmap.md).
- No login of its own. Put Cloudflare Access in front of it; see
  [docs/deployment.md](docs/deployment.md).

---

## Quick start with Docker

```bash
# 1. Choose where the database lives (a directory on your RAID array)
sudo mkdir -p /mnt/nas/tcg-log-vault

# 2. Point docker-compose.yml at that path if it differs, then:
docker compose up -d --build

# 3. Open it
curl http://127.0.0.1:8088/healthz     # -> ok
```

For the real server, including how code gets there (the repo is private, so
it's copied over as an archive), follow [docs/deployment.md](docs/deployment.md).

The port is published on 8088 on every interface, so it is reachable from the
LAN at `http://<server-ip>:8088`. The app has no login: anyone on the LAN can
read and delete games. Never forward the port on your router; use Cloudflare
Tunnel with Access for outside access. Change the `ports` line in
`docker-compose.yml` to `"127.0.0.1:8088:8000"` for host-only access.

## Running it without Docker

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
TCG_DB_PATH=./data/games.db uvicorn app.main:app --reload --port 8000
```

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `TCG_DB_PATH` | `/data/games.db` | Full path to the SQLite file. Its directory is created on startup. |
That is the entire configuration surface. There is intentionally nothing else
to set. The form's default date follows the server's timezone, which
`docker-compose.yml` shares into the container; set it on the host with
`sudo timedatectl set-timezone America/Chicago`.

---

## Using it

### Adding a game

1. Export the log from Pokémon TCG Live and paste the whole thing into the
   **Battle log** box.
2. Enter **your username as it appears in that log** (for example
   `super-victini13`). This is the only required field besides the log. It is what
   turns "super-victini13 wins" into a W or an L for you, and it is what lets
   any future feature tell your side from your opponent's.
3. Optional fields:
   - **Date played** — defaults to today.
   - **Your deck / variant** and **Opponent's deck / variant** — free text.
     Variant is for splitting one archetype into its builds, for example
     `Dragapult ex` with variant `Dusknoir` or `Dudunsparce`.
   - **Rank points** — the points you had going *into* the game. Leave it
     blank for casual games: a game with points is a ranked game.
   - **Result** — leave it on "Read it from the log" unless the log is
     truncated or the game ended in a way the parser does not recognise.

The username box is pre-filled with the username from your most recent save,
so repeat entry is one paste and one click.

### Editing and deleting

Open a game from the list to change its date, decks, variants, or result. The
raw log and its hash are never editable: that is what keeps the archive
trustworthy. Deleting a game removes it permanently.

### Getting data out

| Route | Returns |
|---|---|
| `/export.csv` | Every game as CSV, without the raw logs |
| `/api/games` | JSON, with `?include_log=true` to embed raw logs |
| `/games/{id}/raw` | One log as plain text |

The database itself is a single file. `sqlite3 /mnt/nas/tcg-log-vault/games.db`
gives you everything, and [docs/queries.md](docs/queries.md) has queries to
start from.

---

## Project layout

```
app/
  main.py        Web routes and form handling
  parser.py      Battle-log parsing (no database or web dependencies)
  db.py          Schema, migrations, and queries
  templates/     Jinja2 templates
scripts/
  backfill.py    Recompute derived fields across the whole archive
tests/
  test_app.py    35 tests covering parser, database, and routes
  sample_log.txt A real log, used as the test fixture
docs/            The documents listed above
```

`parser.py` imports nothing from the rest of the app on purpose. You can use it
standalone:

```python
from app.parser import parse
p = parse(open("game.txt").read())
print(p.winner, p.turns, p.first_player, p.prizes_taken)
```

## Tests

```bash
pip install pytest httpx
python -m pytest -q
```

The fixture is a real sample game, so the tests assert against known-good
values: 13 turns, super-victini13 winning 6 prizes to 0 on knockouts, going
first, and mulliganing 3 times.

---

## Backups

RAID 1 protects against a failed disk. It does not protect against deletion,
corruption, or a bad `DELETE`. On the server, `scripts/backup.sh` takes a
consistent snapshot while the app runs and saves it to
`~/backups/tcg-log-vault`:

```bash
sh /opt/tcg-log-vault/scripts/backup.sh
```

The daily cron entry, restoring, and deploying are covered in
[docs/deployment.md](docs/deployment.md).
