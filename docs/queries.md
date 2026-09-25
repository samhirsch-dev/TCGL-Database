# Query cookbook

```bash
sqlite3 -header -column /mnt/nas/tcg-log-vault/games.db
```

## Overall record

```sql
SELECT COUNT(*) AS games,
       SUM(result = 'W') AS wins,
       SUM(result = 'L') AS losses,
       ROUND(100.0 * SUM(result = 'W') / COUNT(*), 1) AS win_pct
FROM games;
```

## Ranked vs casual

```sql
SELECT COALESCE(game_mode, '(not recorded)') AS mode,
       COUNT(*) AS games,
       SUM(result = 'W') AS wins,
       ROUND(100.0 * SUM(result = 'W') / COUNT(*), 1) AS win_pct
FROM games
GROUP BY mode
ORDER BY games DESC;
```

Add `WHERE game_mode = 'ranked'` to any other query here to count only ranked
games.

## Rank points over time

`rank_points` is what you had going *into* a game, so a game's effect is the
next ranked game's points minus its own:

```sql
SELECT date, id, result, rank_points,
       LEAD(rank_points) OVER (ORDER BY date, id) - rank_points AS change
FROM games
WHERE game_mode = 'ranked' AND rank_points IS NOT NULL
ORDER BY date DESC, id DESC;
```

The newest game shows no change yet. If you played a ranked game without
logging it, the change on the game before the gap covers both games.

## Win rate by matchup, variant included

```sql
SELECT opponents_deck,
       COALESCE(opponents_variant, '(unspecified)') AS variant,
       COUNT(*) AS games,
       SUM(result = 'W') AS wins,
       ROUND(100.0 * SUM(result = 'W') / COUNT(*), 1) AS win_pct
FROM games
WHERE opponents_deck IS NOT NULL
GROUP BY opponents_deck, variant
HAVING games >= 3
ORDER BY games DESC;
```

The `HAVING games >= 3` matters more than it looks. A 0 for 1 matchup reads as
a 0% win rate and will mislead you every time.

## Does the variant actually change the matchup?

```sql
SELECT opponents_variant,
       COUNT(*) AS games,
       ROUND(100.0 * SUM(result = 'W') / COUNT(*), 1) AS win_pct
FROM games
WHERE opponents_deck = 'Dragapult ex'
GROUP BY opponents_variant
ORDER BY games DESC;
```

## Your decks compared

```sql
SELECT COALESCE(players_deck, '(untagged)') AS deck,
       COALESCE(players_variant, '') AS variant,
       COUNT(*) AS games,
       ROUND(100.0 * SUM(result = 'W') / COUNT(*), 1) AS win_pct,
       ROUND(AVG(turns), 1) AS avg_turns
FROM games
GROUP BY deck, variant
ORDER BY games DESC;
```

## Are your losses fast or slow?

```sql
SELECT result,
       COUNT(*) AS games,
       ROUND(AVG(turns), 1) AS avg_turns,
       MIN(turns) AS shortest,
       MAX(turns) AS longest
FROM games
GROUP BY result;
```

Short losses usually point at a start or prize-race problem; long losses point
at a resource or late-game problem. The two call for different deck changes.

## Results over time

```sql
SELECT substr(date, 1, 7) AS month,
       COUNT(*) AS games,
       ROUND(100.0 * SUM(result = 'W') / COUNT(*), 1) AS win_pct
FROM games
GROUP BY month
ORDER BY month DESC;
```

## Games still missing deck tags

```sql
SELECT id, date, opponents_deck, opponents_variant
FROM games
WHERE players_deck IS NULL OR opponents_deck IS NULL
ORDER BY date DESC;
```

## Searching inside the logs

Because the full text is stored, the archive is greppable through SQL:

```sql
-- Games where a particular card appeared at all
SELECT id, date, opponents_deck
FROM games
WHERE raw_log LIKE '%Rampardos ex%';

-- Games you lost where the opponent took all six prizes
SELECT id, date, opponents_deck
FROM games
WHERE result = 'L' AND raw_log LIKE '%All Prize cards taken%';
```

`LIKE` scans every row, which is instantaneous at this size. If the archive
ever grows past tens of thousands of games, add an FTS5 index over `raw_log`.

## Deriving something not in a column

Anything the parser knows can be computed on the fly without a migration:

```python
from app import db
from app.parser import parse

rows = db.list_games(limit=100000)
first = sum(
    1 for r in rows
    if parse(db.get_game(r["id"])["raw_log"]).first_player == r["username"]
)
print(f"Went first in {first} of {len(rows)} games")
```

If you find yourself running that often, promote it to a column:
[docs/schema.md](schema.md) has the three-step recipe.
