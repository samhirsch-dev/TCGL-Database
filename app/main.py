"""Web app for archiving Pokemon TCG Live battle logs.

Routes
------
GET  /                  Submission form and recent games
POST /games             Save a pasted log
GET  /games/{id}        One game, with the raw log
POST /games/{id}/edit   Update deck, variant, date or result
POST /games/{id}/delete Delete a game
GET  /games/{id}/raw    The raw log as text/plain
GET  /export.csv        Every game as CSV (raw log excluded)
GET  /api/games         Every game as JSON (for future tooling)
GET  /healthz           Liveness probe
"""

from __future__ import annotations

import csv
import io
from contextlib import asynccontextmanager
from datetime import date as date_cls
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from . import db
from .parser import LogParseError, log_hash, parse

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

app = FastAPI(title="TCG Log Vault", docs_url=None, redoc_url=None)

# CSV/JSON export never includes raw_log; keep both a single source of truth.
EXPORT_FIELDS = [
    "id",
    "date",
    "username",
    "players_deck",
    "players_variant",
    "opponents_deck",
    "opponents_variant",
    "result",
    "turns",
    "rank_points",
    "created_at",
]

# The form remembers the username of your most recent save via this cookie,
# so repeat entry is one paste and one click. No account system needed for a
# single-user app.
USERNAME_COOKIE = "tcg_username"
USERNAME_COOKIE_MAX_AGE = 60 * 60 * 24 * 365 * 5


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init_db()
    yield


app.router.lifespan_context = lifespan


def _clean(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    value = value.strip()
    return value or None


def _error_redirect(message: str, path: str = "/") -> RedirectResponse:
    return RedirectResponse(f"{path}?{urlencode({'error': message})}", status_code=303)


def _rank_points(value: str) -> Optional[int]:
    """Parse the rank points field; blank means the game wasn't ranked."""
    points = _clean(value)
    if points is None:
        return None
    if not points.isdigit():
        raise ValueError("Rank points must be a whole number, 0 or more.")
    return int(points)


@app.get("/", response_class=HTMLResponse)
def index(request: Request, saved: Optional[int] = None, error: Optional[str] = None):
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "games": db.list_games(limit=50),
            "stats": db.stats(),
            "saved_id": saved,
            "error": error,
            "today": date_cls.today().isoformat(),
            "last_username": request.cookies.get(USERNAME_COOKIE, ""),
        },
    )


@app.post("/games")
def create_game(
    raw_log: str = Form(""),
    username: str = Form(""),
    date: str = Form(""),
    players_deck: str = Form(""),
    players_variant: str = Form(""),
    opponents_deck: str = Form(""),
    opponents_variant: str = Form(""),
    result: str = Form(""),
    rank_points: str = Form(""),
):
    username = username.strip()
    if not username:
        return _error_redirect("Enter your username.")
    if not raw_log.strip():
        return _error_redirect("Paste a battle log.")

    try:
        parsed = parse(raw_log)
    except LogParseError as exc:
        return _error_redirect(str(exc))

    if username not in parsed.players:
        found = ", ".join(parsed.players) or "none"
        return _error_redirect(f"Username '{username}' is not in this log. Players found: {found}.")

    entered_result = _clean(result)
    if entered_result not in (None, "W", "L"):
        return _error_redirect("Result must be W or L.")

    try:
        points = _rank_points(rank_points)
    except ValueError as exc:
        return _error_redirect(str(exc))

    game = {
        "date": _clean(date) or date_cls.today().isoformat(),
        "username": username,
        "players_deck": _clean(players_deck),
        "players_variant": _clean(players_variant),
        "opponents_deck": _clean(opponents_deck),
        "opponents_variant": _clean(opponents_variant),
        "result": entered_result or parsed.result_for(username),
        "turns": parsed.turns,
        "rank_points": points,
        "log_hash": log_hash(raw_log),
        "raw_log": raw_log,
    }

    try:
        game_id = db.insert_game(game)
    except db.DuplicateLogError as exc:
        return _error_redirect(f"Already saved as game #{exc.existing_id}.")

    response = RedirectResponse(f"/?{urlencode({'saved': game_id})}", status_code=303)
    response.set_cookie(USERNAME_COOKIE, username, max_age=USERNAME_COOKIE_MAX_AGE)
    return response


@app.get("/games/{game_id}", response_class=HTMLResponse)
def game_detail(request: Request, game_id: int, error: Optional[str] = None):
    game = db.get_game(game_id)
    if game is None:
        raise HTTPException(status_code=404, detail="Game not found")
    try:
        parsed = parse(game["raw_log"])
    except LogParseError:
        parsed = None
    return templates.TemplateResponse(
        request, "detail.html", {"game": game, "parsed": parsed, "error": error}
    )


@app.post("/games/{game_id}/edit")
def edit_game(
    game_id: int,
    date: str = Form(""),
    players_deck: str = Form(""),
    players_variant: str = Form(""),
    opponents_deck: str = Form(""),
    opponents_variant: str = Form(""),
    result: str = Form(""),
    rank_points: str = Form(""),
):
    try:
        points = _rank_points(rank_points)
    except ValueError as exc:
        return _error_redirect(str(exc), f"/games/{game_id}")
    db.update_game(
        game_id,
        {
            "date": _clean(date),
            "players_deck": _clean(players_deck),
            "players_variant": _clean(players_variant),
            "opponents_deck": _clean(opponents_deck),
            "opponents_variant": _clean(opponents_variant),
            "result": _clean(result),
            "rank_points": points,
        },
    )
    return RedirectResponse(f"/games/{game_id}", status_code=303)


@app.post("/games/{game_id}/delete")
def remove_game(game_id: int):
    db.delete_game(game_id)
    return RedirectResponse("/", status_code=303)


@app.get("/games/{game_id}/raw", response_class=PlainTextResponse)
def raw_log(game_id: int):
    game = db.get_game(game_id)
    if game is None:
        raise HTTPException(status_code=404, detail="Game not found")
    return PlainTextResponse(game["raw_log"])


@app.get("/export.csv")
def export_csv():
    rows = db.list_games(limit=100000)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(EXPORT_FIELDS)
    for row in rows:
        writer.writerow([row[key] for key in EXPORT_FIELDS])
    buffer.seek(0)
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=tcg-games.csv"},
    )


@app.get("/api/games")
def api_games(limit: int = 500, include_log: bool = False):
    rows = db.list_games(limit=limit)
    games = []
    for row in rows:
        item = {key: row[key] for key in row.keys()}
        if include_log:
            full = db.get_game(row["id"])
            item["raw_log"] = full["raw_log"] if full else None
        games.append(item)
    return JSONResponse({"games": games, "stats": db.stats()})


@app.get("/healthz", response_class=PlainTextResponse)
def healthz():
    return "ok"
