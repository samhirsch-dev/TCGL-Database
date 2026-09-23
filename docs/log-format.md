# The log format, and what the parser reads

Pokémon TCG Live exports a plain-text log with no timestamps, no deck names,
and no machine-readable structure. It is a transcript written for humans. This
document records what the parser relies on, so that when the game's wording
changes you know exactly where to look.

## Structure

A log has a `Setup` block, then a sequence of turn blocks separated by blank
lines. Top-level lines are actions. Indented lines beginning with `-` or `•`
are the consequences of the line above them.

```
Setup
VoicesConsume chose tails for the opening coin flip.
super-victini13 won the coin toss.
super-victini13 decided to go first.
VoicesConsume drew 7 cards for the opening hand.
- 7 drawn cards.
super-victini13 drew 7 cards for the opening hand.
- 7 drawn cards.

super-victini13's Turn
super-victini13 drew Ultra Ball.
super-victini13 played Fossil Quarry to the Stadium spot.
super-victini13 ended their turn.
```

(This is a real excerpt from `tests/sample_log.txt`, the fixture the test
suite runs against.)

## What the parser extracts

| Field | Line it depends on | Reliability |
|---|---|---|
| Players | `X's Turn`, `X drew 7 cards for the opening hand.` | High |
| `turns` | Count of `X's Turn` headers | High |
| Winner | `... X wins.` | High when present |
| Win condition | `All Prize cards taken.` prefix, or `X conceded.` | Medium |
| First player | `X decided to go first/second.` | High |
| Mulligans | `X took a mulligan.` / `X took N mulligans.` | High |
| Prizes taken | `X took a/N Prize card(s).` | High |
| Knockouts | `X's POKEMON was Knocked Out!` | High |
| Cards seen | `played` / `evolved ... to` / `attached` | Partial, see below |

Only `turns` and the win/loss are stored. Everything else is computed on demand
and shown on the game detail page.

## Three traps this parser avoids

**Curly apostrophes.** The export mixes `'` and `’` in the same file, sometimes
within a single sentence — the sample fixture has both. Every log is
normalised before matching, and the hash is taken after normalisation, so two
pastes of the same game always collide.

**Nested lines inventing players.** A sub-line like
`- super-victini13 drew Antique Armor Fossil and played it to the Bench.`
matches a naive "who played what" pattern and yields a player named
`super-victini13 drew Antique Armor Fossil and`. Players are therefore
collected only from authoritative lines (turn headers, opening hands,
mulligans, prizes, knockouts, concessions, the win line), and card capture runs
as a second pass that only accepts lines starting with an already-known player
name.

**Cumulative mulligan counts.** After the *first* mulligan, TCG Live doesn't
keep emitting `X took a mulligan.` for each subsequent one — it restates a
running total instead: `X took a mulligan.` then `X took 3 mulligans.` for a
player who mulliganed three times. Summing every match would read that as
1 + 3 = 4. The parser takes the *maximum* count seen per player instead of
incrementing, which is correct for both a single mulligan and a run of them.
This came from a real log during development — see `tests/sample_log.txt`.

## Partial information is inherent

You see your own deck in full. You see your opponent's cards only when the game
reveals them, so `cards_seen` for the opponent is a floor, not a list. A game
that ends on turn three may reveal three of their cards. Any future deck
detection has to treat opponent identification as a confident guess rather than
a fact, which is why the roadmap includes a confidence field.

## When Pokémon TCG Live changes its wording

Symptoms, in order of likelihood:

| Symptom | Likely cause | Where to look |
|---|---|---|
| Result saved as Unknown | The win line changed | `WIN_RE` in `app/parser.py` |
| Turn count is 0 and the save is rejected | Turn header changed | `TURN_RE` |
| "Expected 2 players, found 1" warning | Setup lines changed | `OPENING_HAND_RE` |
| Went first shows Unknown | Coin-toss wording changed | `COIN_CHOICE_RE` |
| Mulligan count looks wrong | Mulligan wording changed | `MULLIGAN_RE` |

Nothing is lost while a pattern is stale: the log is still stored in full, and
`python -m scripts.backfill --apply` repairs every affected row once the regex
is fixed. This is the practical payoff of storing raw text.

## Parsing a log outside the app

```python
from app.parser import parse, log_hash

p = parse(open("game.txt").read())
p.players          # ['super-victini13', 'VoicesConsume']
p.turns            # 13
p.winner           # 'super-victini13'
p.first_player     # 'super-victini13'
p.prizes_taken     # {'super-victini13': 6}
p.mulligans        # {'super-victini13': 3}
p.result_for("super-victini13")   # 'W'
p.warnings         # [] when nothing looked odd
```

`parse()` raises `LogParseError` only when no turn headers exist at all, which
is the cheapest reliable signal that the wrong text was pasted. Anything else
unusual is reported in `warnings` rather than refused, so an odd log still gets
archived.
