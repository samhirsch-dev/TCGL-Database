# Roadmap: AI deck detection and database access

Neither feature is built. This document records the seams that were left in
place so they can be added without touching the archive.

## What is already in place for them

| Need | Already there |
|---|---|
| Per-player card lists | `parser.parse().cards_seen`, keyed by player, with sub-line traps handled |
| Knowing which player is you | The required `username` column |
| Re-running detection over old games | `scripts/backfill.py`, plus full raw logs |
| Programmatic read access | `GET /api/games`, with `?include_log=true` |
| Somewhere to put new fields | `ALTER TABLE` plus backfill, see [schema.md](schema.md) |

Nothing being stored today has to change for either feature.

## Deck detection

**Shape.** Rules first, model second.

1. A signature table maps a set of key cards to an archetype and variant, for
   example `Rampardos ex` + `Antique Skull Fossil` → `Rampardos ex` /
   `Bastiodon`. Rules are free, instant, and stable.
2. When no rule matches, send only the card list (not the whole log) to a model
   and ask for the archetype and variant.
3. Store the answer plus its source (`manual`, `rule`, or `model`) and a
   confidence value. Manual entry always wins.
4. When you confirm a model answer in the UI, write it back as a rule. The
   model gets called less over time.

**Two things that will bite.**

*Model knowledge lags the meta.* A model may not know this season's cards or
the community's nickname for a deck. Ask it to name decks by main attacker and
engine rather than by nickname, and treat the result as a suggestion to
confirm.

*Naming drift.* The same deck will come back as `Rampardos Fossil` one day and
`Rampardos ex / Bastiodon` the next, which fragments every matchup query. An
alias table mapping variants to one canonical name fixes this, and confirming a
name once should create the alias.

**Opponent detection is a guess.** You only see the cards they revealed. A
short game may reveal three cards. Store confidence and show it; do not let a
low-confidence guess silently become a matchup statistic.

**Suggested columns:** `players_deck_source`, `opponents_deck_source`,
`opponents_deck_confidence`. Plus a `deck_aliases` table:
`variant_name` → `canonical_name`.

## Giving an AI access to the database

Two options, in increasing order of power.

**Read-only over the existing API.** `GET /api/games` already returns
everything as JSON. It is the smallest step: point a tool at it and ask
questions about the data it returns. Keep it behind Cloudflare Access, and note
that `include_log=true` returns full logs, which is a lot of text for a large
archive.

**An MCP server over the database.** A small server exposing a couple of tools
(`query_games` running a constrained read-only SQL statement, `get_log`
returning one raw log) would let a model answer "what's my win rate against
Dragapult/Dusknoir since the last rotation" directly.

Guardrails worth building in from the start:

- Open the SQLite file read-only (`file:games.db?mode=ro`) for anything a model
  drives. Read paths should not be able to write.
- Return row counts alongside results so small samples are visible.
- Keep the tunnel and Access in front of any endpoint that gets added.

## Other things the archive can already support

None of these need new data collection, only parsing and a backfill:

- **Prize-race shape.** `prizes_taken` per player is parsed already. Losses
  where you took four prizes are a different problem from losses where you took
  one.
- **Turn-one effects.** Which cards you played on your first turn, and how
  often that correlates with a win.
- **Mulligan impact.** Mulligans are counted; the correlation with results is a
  query away.
- **Going first or second.** The parser knows the first player. Promote it to a
  column when you want to filter on it.

## Deliberately out of scope

- **Deck lists.** A log does not contain one, only the cards that were
  revealed. Anything presented as a full list would be a fabrication.
- **Opponent tracking across games.** Usernames are in the raw logs, but
  building a per-opponent profile is a different product with different privacy
  implications.
