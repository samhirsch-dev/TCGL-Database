# Deployment

Target: a Docker container on the Ubuntu server, with the database on the RAID
1 array, reached remotely through Cloudflare Tunnel and protected by Cloudflare
Access.

## One rule about where the database lives

SQLite must sit on a **local filesystem**. Its locking is unreliable over SMB
and NFS, and a corrupted database is the likely outcome of ignoring this. A
bind mount to a locally-mounted RAID array is local as far as SQLite is
concerned, so `/mnt/nas/...` is fine. A share mounted from another machine is
not.

## The server

| | |
|---|---|
| Host | `linux-server`, `192.168.0.103` on the LAN (DHCP; check with `hostname -I` if it stops answering) |
| App folder | `/opt/tcg-log-vault`, owned by `sam` |
| Database | `/mnt/nas/tcg-log-vault/games.db` (RAID 1, `md0`), owned by root because the container writes it |
| Backups | `/home/sam/backups/tcg-log-vault` (NVMe, so a failed array doesn't take them too) |
| Timezone | `America/Chicago`, set on the host; the container follows it |

`sam` is in the `docker` group, so managing the container needs no `sudo`.
Membership is effectively root on this machine, which also runs Immich,
Portainer, and the Cloudflare tunnel.

## Install

The GitHub repo is private and the server has no credentials for it, so code
reaches the server as an archive built on the PC, never by `git clone`.

On the server, once:

```bash
sudo mkdir -p /mnt/nas/tcg-log-vault /opt/tcg-log-vault
sudo chown sam: /opt/tcg-log-vault
sudo usermod -aG docker sam          # log out and back in afterwards
sudo timedatectl set-timezone America/Chicago
```

Then deploy as in [Updating](#updating). Edit the volume line in
`docker-compose.yml` first if your array is mounted elsewhere.

The image runs `uvicorn` on port 8000 inside the container. The compose file
publishes it on port 8088 on every interface, so it is reachable from the LAN
at `http://<server-ip>:8088`. Anyone on the LAN can read and delete games, and
Docker's published ports bypass `ufw`, so a firewall rule will not restrict it.
Never forward 8088 on the router. For host-only access, change the `ports` line
to `"127.0.0.1:8088:8000"`; Cloudflare Tunnel works either way. The container
has a `HEALTHCHECK`, so `docker ps` reports health, and Portainer shows it too.

The form's default date is "today" in the container's timezone. A container
ignores the host's timezone unless it is shared, so `docker-compose.yml` mounts
the host's `/etc/localtime` read-only. Set the timezone on the host with
`timedatectl`, then restart the container: the mount keeps the old zone until
then. Left on UTC, the form defaults to tomorrow on US evenings. `created_at` is
always stored in UTC regardless.

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

- Bind the port to `127.0.0.1` so the only route in is the tunnel (at the cost
  of LAN access by IP).
- Add a Cloudflare WAF rate-limiting rule on `POST /games` if you ever open it
  more widely.

## Backups

RAID 1 survives a dead disk. It does not survive an accidental delete, a bad
migration, or file corruption.

[`scripts/backup.sh`](../scripts/backup.sh) takes a snapshot with SQLite's
backup API inside the container, runs an integrity check on it, copies it out to
`~/backups/tcg-log-vault/games-YYYY-MM-DD-HHMM.db.gz`, and deletes copies older
than 30 days. It needs no `sudo`. Never back up with `cp`: it can catch the file
mid-write, and in WAL mode recent changes may still be in `games.db-wal`.

Run it by hand before any deploy that changes the schema:

```bash
sh /opt/tcg-log-vault/scripts/backup.sh
```

To run it daily at 3:30 AM, add this with `crontab -e`:

```
30 3 * * * /bin/sh /opt/tcg-log-vault/scripts/backup.sh >> $HOME/backups/tcg-log-vault/backup.log 2>&1
```

Send a copy offsite as well, using whatever you already run (rclone to a cloud
bucket, a sync to another machine, or a periodic download of `/export.csv`).
The CSV is not a full backup, since it excludes the raw logs, but it is a
useful human-readable secondary copy.

### Restoring

The database is root-owned, so restoring needs `sudo`:

```bash
cd /opt/tcg-log-vault && docker compose stop
gunzip -c ~/backups/tcg-log-vault/games-2026-09-24-2130.db.gz | sudo tee /mnt/nas/tcg-log-vault/games.db > /dev/null
sudo rm -f /mnt/nas/tcg-log-vault/games.db-wal /mnt/nas/tcg-log-vault/games.db-shm
docker compose start
```

Delete the `-wal` and `-shm` files. A leftover WAL file belongs to the old
database, and SQLite would replay it onto the restored one.

## Updating

The server must always run a commit that is on GitHub, so the repo and the
server never drift apart. Never edit files in `/opt/tcg-log-vault` directly.
Change them in the repo, push, then deploy. From the repo on the PC:

```bash
git push origin main
git archive --format=tar.gz -o tcg-log-vault.tar.gz origin/main
scp tcg-log-vault.tar.gz sam@192.168.0.103:~/
ssh sam@192.168.0.103 "tar -xzf ~/tcg-log-vault.tar.gz -C /opt/tcg-log-vault && cd /opt/tcg-log-vault && docker compose up -d --build"
```

Archiving `origin/main` rather than `main` guarantees that what's deployed is
what GitHub has. Run `scripts/backup.sh` first if the update adds columns. The
database is outside the image, and `init_db()` is idempotent, so rebuilds never
lose data.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `unable to open database file` at start | The mounted directory does not exist or is not writable | Create it on the host; check ownership |
| `database is locked` | Two writers, or the file is on a network share | Confirm the mount is local; only one container should use it |
| Page loads but saving fails silently | Reverse proxy dropping the POST body size | Raise the body limit; logs run tens of KB |
| Health check failing | App failed to start | `docker compose logs tcg-log-vault` |
| Form defaults to tomorrow's date | Host on UTC, or timezone changed without a restart | `timedatectl`, then `docker compose restart` |

## If you later prefer Postgres

You already run PostgreSQL for the finance pipeline, and moving is
straightforward if Grafana dashboards become the goal: the schema is one table
with no SQLite-specific types except the `CHECK` constraint, which Postgres
accepts as written. Swapping `app/db.py` for a psycopg implementation is the
only code change. Until then, the single-file database is simpler to back up
and keeps your match history independent of your finance data.
