#!/bin/sh
# Snapshot the live database into ~/backups/tcg-log-vault on the host.
#
# Runs as a regular user in the docker group, no sudo: the database file is
# owned by root (the container writes it), so the snapshot is taken inside the
# container with SQLite's backup API and copied out. A plain `cp` could catch
# the file mid-write or miss changes still sitting in the WAL file.
#
# Usage:   sh scripts/backup.sh
# Daily:   30 3 * * * /bin/sh /opt/tcg-log-vault/scripts/backup.sh >> $HOME/backups/tcg-log-vault/backup.log 2>&1
set -eu

CONTAINER=tcg-log-vault
OUT="$HOME/backups/tcg-log-vault"
NAME="games-$(date +%F-%H%M).db"
KEEP_DAYS=30

mkdir -p "$OUT"

docker exec "$CONTAINER" python -c "
import sqlite3
src = sqlite3.connect('/data/games.db')
dst = sqlite3.connect('/tmp/backup.db')
src.backup(dst)
check = dst.execute('PRAGMA integrity_check').fetchone()[0]
games = dst.execute('SELECT COUNT(*) FROM games').fetchone()[0]
dst.close()
src.close()
assert check == 'ok', 'integrity check failed: ' + check
print(f'snapshot ok: {games} games')
"
docker cp "$CONTAINER:/tmp/backup.db" "$OUT/$NAME"
docker exec "$CONTAINER" rm -f /tmp/backup.db
gzip -f "$OUT/$NAME"
find "$OUT" -name 'games-*.db.gz' -mtime +"$KEEP_DAYS" -delete

echo "saved $OUT/$NAME.gz"
