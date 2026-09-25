"""Tests for the parser, the database layer and the web routes.

Run with:  pytest -q
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

SAMPLE = (Path(__file__).parent / "sample_log.txt").read_text(encoding="utf-8")
USERNAME = "super-victini13"


@pytest.fixture(autouse=True)
def temp_db(monkeypatch):
    """Point the app at a throwaway database for every test."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "test.db"
        os.environ["TCG_DB_PATH"] = str(path)
        from app import db

        monkeypatch.setattr(db, "DB_PATH", path)
        db.init_db()
        yield path


# --- parser -----------------------------------------------------------------

def test_parses_players_and_turns():
    from app.parser import parse

    p = parse(SAMPLE)
    assert p.players == ["super-victini13", "VoicesConsume"]
    assert p.turns == 13


def test_identifies_winner_and_result():
    from app.parser import parse

    p = parse(SAMPLE)
    assert p.winner == "super-victini13"
    assert p.loser == "VoicesConsume"
    assert p.win_condition == "prizes"
    assert p.result_for("super-victini13") == "W"
    assert p.result_for("VoicesConsume") == "L"


def test_reads_first_player_from_coin_toss():
    from app.parser import parse

    # super-victini13 won the toss and chose to go first.
    assert parse(SAMPLE).first_player == "super-victini13"


def test_counts_mulligans_and_prizes():
    from app.parser import parse

    p = parse(SAMPLE)
    # TCG Live announces the first mulligan on its own line, then restates
    # the running total on each further one ("took a mulligan." then
    # "took 3 mulligans."). This must read as 3, not 1 + 3.
    assert p.mulligans == {"super-victini13": 3}
    assert p.prizes_taken["super-victini13"] == 6
    assert "VoicesConsume" not in p.prizes_taken


def test_counts_knockouts_suffered():
    from app.parser import parse

    p = parse(SAMPLE)
    assert p.knockouts == {"VoicesConsume": 4}


def test_cards_seen_never_invents_a_player():
    from app.parser import parse

    p = parse(SAMPLE)
    assert set(p.cards_seen) == {"super-victini13", "VoicesConsume"}
    assert "Rampardos ex" in p.cards_seen["super-victini13"]
    assert "Toxtricity" in p.cards_seen["VoicesConsume"]


def test_hash_ignores_apostrophe_and_whitespace_differences():
    from app.parser import log_hash

    curly = SAMPLE.replace("'", "’")
    assert log_hash(SAMPLE) == log_hash(curly)
    assert log_hash(SAMPLE) == log_hash(SAMPLE + "\n\n")


def test_rejects_text_that_is_not_a_log():
    from app.parser import LogParseError, parse

    with pytest.raises(LogParseError):
        parse("my shopping list\nmilk\neggs")


def test_handles_a_concession():
    from app.parser import parse

    log = "Alpha's Turn\nAlpha ended their turn.\nBeta's Turn\nBeta conceded.\n"
    p = parse(log)
    assert p.winner == "Alpha"
    assert p.win_condition == "concession"


# --- database ---------------------------------------------------------------

def _game(hash_suffix: str = "a"):
    from app.parser import log_hash, parse

    p = parse(SAMPLE)
    return {
        "date": "2026-09-20",
        "username": USERNAME,
        "players_deck": "Rampardos ex",
        "players_variant": "Bastiodon",
        "opponents_deck": "Toxtricity",
        "opponents_variant": None,
        "result": p.result_for(USERNAME),
        "turns": p.turns,
        "log_hash": log_hash(SAMPLE) + hash_suffix,
        "raw_log": SAMPLE,
    }


def test_insert_and_read_back():
    from app import db

    game_id = db.insert_game(_game())
    row = db.get_game(game_id)
    assert row["result"] == "W"
    assert row["turns"] == 13
    assert row["raw_log"] == SAMPLE


def test_duplicate_logs_are_rejected():
    from app import db

    db.insert_game(_game())
    with pytest.raises(db.DuplicateLogError):
        db.insert_game(_game())


def test_stats_count_wins_and_losses():
    from app import db

    db.insert_game(_game("a"))
    loss = _game("b")
    loss["result"] = "L"
    db.insert_game(loss)
    s = db.stats()
    assert s["games"] == 2 and s["wins"] == 1 and s["losses"] == 1


# --- web routes -------------------------------------------------------------

@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c


def test_form_submission_stores_a_game(client):
    r = client.post(
        "/games",
        data={"raw_log": SAMPLE, "username": USERNAME, "date": "2026-09-20"},
        follow_redirects=False,
    )
    assert r.status_code == 303 and "saved=" in r.headers["location"]

    from app import db

    row = db.list_games()[0]
    assert row["result"] == "W" and row["turns"] == 13


def test_missing_username_is_rejected(client):
    r = client.post("/games", data={"raw_log": SAMPLE, "username": ""}, follow_redirects=False)
    assert "error=" in r.headers["location"]


def test_username_not_in_log_is_rejected(client):
    r = client.post(
        "/games",
        data={"raw_log": SAMPLE, "username": "NotAPlayer"},
        follow_redirects=False,
    )
    assert "not+in+this+log" in r.headers["location"]


def test_error_message_is_url_safe_for_odd_usernames(client):
    """Usernames or messages containing &, #, or quotes must not corrupt the redirect."""
    r = client.post(
        "/games",
        data={"raw_log": SAMPLE, "username": "weird&name#'s"},
        follow_redirects=False,
    )
    location = r.headers["location"]
    assert location.startswith("/?error=")
    assert "&name" not in location  # an un-encoded '&' would start a new query param


def test_manual_result_overrides_the_parsed_one(client):
    client.post(
        "/games",
        data={"raw_log": SAMPLE, "username": USERNAME, "result": "L"},
        follow_redirects=False,
    )
    from app import db

    assert db.list_games()[0]["result"] == "L"


def test_date_defaults_to_today(client):
    from datetime import date

    client.post("/games", data={"raw_log": SAMPLE, "username": USERNAME}, follow_redirects=False)
    from app import db

    assert db.list_games()[0]["date"] == date.today().isoformat()


def test_duplicate_submission_is_refused(client):
    data = {"raw_log": SAMPLE, "username": USERNAME}
    client.post("/games", data=data, follow_redirects=False)
    r = client.post("/games", data=data, follow_redirects=False)
    assert "Already+saved" in r.headers["location"]


def test_username_cookie_prefills_the_form(client):
    r = client.post("/games", data={"raw_log": SAMPLE, "username": USERNAME}, follow_redirects=False)
    assert r.cookies.get("tcg_username") == USERNAME

    index = client.get("/")
    assert f'value="{USERNAME}"' in index.text


def test_csv_and_api_exports(client):
    client.post("/games", data={"raw_log": SAMPLE, "username": USERNAME}, follow_redirects=False)
    csv_body = client.get("/export.csv").text
    assert "opponents_variant" in csv_body and USERNAME in csv_body
    payload = client.get("/api/games").json()
    assert payload["stats"]["games"] == 1
    assert payload["games"][0]["turns"] == 13


def test_health_check(client):
    assert client.get("/healthz").text == "ok"


def test_pages_render(client):
    assert client.get("/").status_code == 200
    client.post(
        "/games",
        data={
            "raw_log": SAMPLE,
            "username": USERNAME,
            "opponents_deck": "Toxtricity",
            "opponents_variant": "Munkidori",
        },
        follow_redirects=False,
    )
    index = client.get("/")
    assert "Toxtricity" in index.text and "Munkidori" in index.text
    detail = client.get("/games/1")
    assert detail.status_code == 200
    assert "VoicesConsume" in detail.text


def test_edit_updates_metadata_but_not_the_log(client):
    client.post("/games", data={"raw_log": SAMPLE, "username": USERNAME}, follow_redirects=False)
    client.post(
        "/games/1/edit",
        data={"date": "2026-09-19", "players_deck": "Rampardos ex", "result": "W"},
        follow_redirects=False,
    )
    from app import db

    row = db.get_game(1)
    assert row["date"] == "2026-09-19" and row["players_deck"] == "Rampardos ex"
    assert row["raw_log"] == SAMPLE


def test_delete_removes_the_game(client):
    client.post("/games", data={"raw_log": SAMPLE, "username": USERNAME}, follow_redirects=False)
    client.post("/games/1/delete", follow_redirects=False)
    from app import db

    assert db.get_game(1) is None


# --- ranked games -----------------------------------------------------------

def test_existing_database_gains_the_ranked_columns(tmp_path, monkeypatch):
    """A database created before these columns existed is upgraded in place."""
    import sqlite3

    from app import db

    path = tmp_path / "v1.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE games (id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT NOT NULL, "
        "username TEXT NOT NULL, players_deck TEXT, players_variant TEXT, "
        "opponents_deck TEXT, opponents_variant TEXT, result TEXT, turns INTEGER, "
        "log_hash TEXT NOT NULL UNIQUE, raw_log TEXT NOT NULL, "
        "created_at TEXT NOT NULL DEFAULT (datetime('now')));"
        "INSERT INTO games (date, username, result, turns, log_hash, raw_log) "
        "VALUES ('2026-09-20', 'super-victini13', 'W', 13, 'abc', 'the log');"
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(db, "DB_PATH", path)
    db.init_db()
    db.init_db()  # restarts must be safe

    row = db.get_game(1)
    assert row["result"] == "W" and row["raw_log"] == "the log"
    assert row["game_mode"] is None and row["rank_points"] is None


def _post(client, **fields):
    data = {"raw_log": SAMPLE, "username": USERNAME, **fields}
    return client.post("/games", data=data, follow_redirects=False)


def test_ranked_game_stores_mode_and_points(client):
    _post(client, game_mode="ranked", rank_points="1250")
    from app import db

    row = db.list_games()[0]
    assert row["game_mode"] == "ranked" and row["rank_points"] == 1250


def test_points_without_a_mode_mark_the_game_ranked(client):
    _post(client, rank_points="0")
    from app import db

    row = db.list_games()[0]
    assert row["game_mode"] == "ranked" and row["rank_points"] == 0


def test_mode_and_points_default_to_not_recorded(client):
    _post(client)
    from app import db

    row = db.list_games()[0]
    assert row["game_mode"] is None and row["rank_points"] is None


def test_casual_game_with_points_is_rejected(client):
    r = _post(client, game_mode="casual", rank_points="300")
    assert "only+apply+to+ranked" in r.headers["location"]
    from app import db

    assert db.list_games() == []


@pytest.mark.parametrize("points", ["-5", "12.5", "lots"])
def test_invalid_rank_points_are_rejected(client, points):
    r = _post(client, game_mode="ranked", rank_points=points)
    assert "whole+number" in r.headers["location"]


def test_edit_sets_and_clears_ranked_fields(client):
    _post(client)
    client.post(
        "/games/1/edit",
        data={"date": "2026-09-20", "game_mode": "ranked", "rank_points": "900"},
        follow_redirects=False,
    )
    from app import db

    row = db.get_game(1)
    assert row["game_mode"] == "ranked" and row["rank_points"] == 900

    client.post(
        "/games/1/edit",
        data={"date": "2026-09-20", "game_mode": "casual", "rank_points": ""},
        follow_redirects=False,
    )
    row = db.get_game(1)
    assert row["game_mode"] == "casual" and row["rank_points"] is None


def test_invalid_edit_shows_an_error_and_changes_nothing(client):
    _post(client, game_mode="ranked", rank_points="900")
    r = client.post(
        "/games/1/edit",
        data={"date": "2026-09-20", "game_mode": "casual", "rank_points": "500"},
        follow_redirects=False,
    )
    assert r.headers["location"].startswith("/games/1?error=")
    assert "only apply to ranked games" in client.get(r.headers["location"]).text
    from app import db

    row = db.get_game(1)
    assert row["game_mode"] == "ranked" and row["rank_points"] == 900


def test_ranked_fields_appear_in_list_and_exports(client):
    _post(client, game_mode="ranked", rank_points="1250")
    assert "1,250" in client.get("/").text
    csv_body = client.get("/export.csv").text
    assert "game_mode,rank_points" in csv_body and "ranked,1250" in csv_body
    assert client.get("/api/games").json()["games"][0]["rank_points"] == 1250
