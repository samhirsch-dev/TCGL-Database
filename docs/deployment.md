# Deployment

Target: a Docker container on the Ubuntu server, with the database on the RAID
1 array, reached remotely through Cloudflare Tunnel and protected by Cloudflare
Access.

## One rule about where the database lives

SQLite must sit on a **local filesystem**. Its locking is unreliable over SMB
and NFS, and a corrupted database is the likely outcome of ignoring this. A
bind mount to a locally-mounted RAID array is local as far as SQLite is
concerned, so `/mnt/raid/...` is fine. A share mounted from another machine is
not.

## Install

```bash
sudo mkdir -p /mnt/raid/tcg-log-vault
git clone <your repo> /opt/tcg-log-vault   # or copy the folder there
cd /opt/tcg-log-vault
```

Edit the volume line in `docker-compose.yml` if your array is mounted
elsewhere:

```yaml
volumes:
  - /mnt/raid/tcg-log-vault:/data
```

Then:

```bash
docker compose up -d --build
docker compose logs -f          # watch the first start
curl http://127.0.0.1:8088/healthz
```

The image runs `uvicorn` on port 8000 inside the container. The compose file
publishes it on `127.0.0.1:8088`, so it is reachable from the host only. The
container has a `HEALTHCHECK`, so `docker ps` reports health, and Portainer
shows it too.

## Remote access with Cloudflare Tunnel

Add a public hostname to your existing tunnel, pointing at the published port:

| Setting | Value |
|---|---|
| Subdomain | `tcg` |
| Domain | `samhirsch.dev` |
| Service type | HTTP |
| URL | `localhost:8088` |

If you configure the tunnel by file rather than in the dashboard:

```yaml
ingress:
  - hostname: tcg.samhirsch.dev
    service: http://localhost:8088
  - service: http_status:404
```

## Put Cloudflare Access in front of it

**Do this before the hostname goes live.** The app has no login of its own.
Anyone who reaches the URL can read your archive, submit logs, and delete
games.

1. Cloudflare Zero Trust → Access → Applications → Add a self-hosted
   application.
2. Domain: `tcg.samhirsch.dev`.
3. Policy: Allow, with the rule "Emails" set to your own address.
4. Session duration to taste; a long one avoids re-authenticating on every
   visit.

Two further hardening options, only if you want them:

- Keep the port bound to `127.0.0.1` (the default here) so the only route in is
  the tunnel.
- Add a Cloudflare WAF rate-limiting rule on `POST /games` if you ever open it
  more widely.

## Backups

RAID 1 survives a dead disk. It does not survive an accidental delete, a bad
migration, or file corruption. A daily copy takes a second.

```bash
sudo mkdir -p /mnt/raid/backups/tcg-log-vault
```

`/etc/cron.daily/tcg-log-vault-backup`, or a crontab entry:

```bash
#!/bin/sh
set -eu
DB=/mnt/raid/tcg-log-vault/games.db
OUT=/mnt/raid/backups/tcg-log-vault
sqlite3 "$DB" ".backup '$OUT/games-$(date +%F).db'"
gzip -f "$OUT/games-$(date +%F).db"
find "$OUT" -name 'games-*.db.gz' -mtime +30 -delete
```

Use `.backup` rather than `cp`. It takes a consistent snapshot while the app is
running; a plain copy can catch the file mid-write, especially with WAL mode
on.

Send a copy offsite as well, using whatever you already run (rclone to a cloud
bucket, a sync to another machine, or a periodic download of `/export.csv`).
The CSV is not a full backup, since it excludes the raw logs, but it is a
useful human-readable secondary copy.

### Restoring

```bash
docker compose stop
gunzip -c /mnt/raid/backups/tcg-log-vault/games-2026-09-20.db.gz \
  > /mnt/raid/tcg-log-vault/games.db
docker compose start
```

## Updating

```bash
cd /opt/tcg-log-vault
git pull
docker compose up -d --build
```

Take a backup first if the update includes a schema change. `init_db()` is
idempotent, so restarts and rebuilds never lose data, and the database is
outside the image.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `unable to open database file` at start | The mounted directory does not exist or is not writable | Create it on the host; check ownership |
| `database is locked` | Two writers, or the file is on a network share | Confirm the mount is local; only one container should use it |
| Page loads but saving fails silently | Reverse proxy dropping the POST body size | Raise the body limit; logs run tens of KB |
| Health check failing | App failed to start | `docker compose logs tcg-log-vault` |

## If you later prefer Postgres

You already run PostgreSQL for the finance pipeline, and moving is
straightforward if Grafana dashboards become the goal: the schema is one table
with no SQLite-specific types except the `CHECK` constraint, which Postgres
accepts as written. Swapping `app/db.py` for a psycopg implementation is the
only code change. Until then, the single-file database is simpler to back up
and keeps your match history independent of your finance data.
