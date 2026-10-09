"""ESPN API: download (through the cache) and parse into small plain structures."""
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from .cache import get_json
from .config import SETTINGS

BASE = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
ET = ZoneInfo("America/New_York")
REGULAR, PLAYOFFS = 2, 3


def et_date(iso: str) -> date:
    """ESPN gives UTC ('2026-10-10T00:00Z'); NBA days are US Eastern."""
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(ET).date()


def season_of(day: date) -> int:
    """ESPN season year: the 2025-26 season is 2026."""
    return day.year + 1 if day.month >= 8 else day.year


def _ttl_for(day: date) -> int | None:
    """Old data never changes; recent data is refreshed."""
    today = datetime.now(ET).date()
    return None if day < today - timedelta(days=2) else SETTINGS.volatile_ttl


def _int(x) -> int:
    try:
        return int(float(x))
    except (TypeError, ValueError):
        return 0


# ---------- scoreboard ----------

@dataclass
class Game:
    event_id: str
    day: date
    status: str              # STATUS_SCHEDULED / STATUS_FINAL / ...
    season_type: int
    home_id: str
    away_id: str
    home_abbr: str
    away_abbr: str
    home_score: int
    away_score: int
    line: float | None       # bookmaker total (only before the game)


def scoreboard(day: date) -> list[Game]:
    d = get_json(f"{BASE}/scoreboard?dates={day:%Y%m%d}", ttl=_ttl_for(day))
    games = []
    for e in d.get("events", []):
        c = e["competitions"][0]
        side = {x["homeAway"]: x for x in c["competitors"]}
        odds = c.get("odds") or []
        line = odds[0].get("overUnder") if odds else None
        games.append(Game(
            e["id"], et_date(e["date"]), c["status"]["type"]["name"], e["season"]["type"],
            side["home"]["team"]["id"], side["away"]["team"]["id"],
            side["home"]["team"]["abbreviation"], side["away"]["team"]["abbreviation"],
            _int(side["home"].get("score")), _int(side["away"].get("score")),
            float(line) if line is not None else None))
    return games


# ---------- injuries ----------

STATUS_FROM_TYPE = {
    "INJURY_STATUS_OUT": "Out",
    "INJURY_STATUS_DOUBTFUL": "Doubtful",
    "INJURY_STATUS_QUESTIONABLE": "Questionable",
    "INJURY_STATUS_DAYTODAY": "Day-To-Day",
}


@dataclass
class Injury:
    athlete_id: str | None
    name: str
    status: str
    detail: str = ""


def _athlete_id(athlete: dict) -> str | None:
    if athlete.get("id"):
        return str(athlete["id"])
    for link in athlete.get("links", []):
        m = re.search(r"/id/(\d+)", link.get("href", ""))
        if m:
            return m.group(1)
    return None


def parse_injury(i: dict) -> Injury:
    status = i.get("status") or STATUS_FROM_TYPE.get(i.get("type", {}).get("name"), "")
    det = i.get("details", {})
    detail = " ".join(x for x in (det.get("type"), det.get("detail")) if x)
    if det.get("returnDate"):
        detail += f" (return {det['returnDate']})"
    a = i["athlete"]
    return Injury(_athlete_id(a), a.get("displayName", "?"), status, detail.strip())


def injuries() -> dict[str, list[Injury]]:
    """team_id -> current injury report."""
    d = get_json(f"{BASE}/injuries", ttl=SETTINGS.volatile_ttl)
    out = {}
    for team in d.get("injuries", []):
        out[str(team["id"])] = [parse_injury(i) for i in team.get("injuries", [])]
    return out


# ---------- team schedule ----------

@dataclass
class ScheduleGame:
    event_id: str
    day: date
    is_home: bool
    opp_id: str
    pts_for: int
    pts_against: int
    completed: bool


def _trim_schedule(d: dict) -> dict:
    events = []
    for e in d.get("events", []):
        c = e["competitions"][0]
        events.append({
            "id": e["id"], "date": e["date"], "completed": c["status"]["type"].get("completed", False),
            "competitors": [{"id": x["id"], "homeAway": x["homeAway"],
                             "score": (x.get("score") or {}).get("value")} for x in c["competitors"]],
        })
    return {"events": events}


def team_schedule(team_id: str, season: int, season_type: int = REGULAR) -> list[ScheduleGame]:
    today = datetime.now(ET).date()
    ttl = None if season < season_of(today) else SETTINGS.volatile_ttl
    d = get_json(f"{BASE}/teams/{team_id}/schedule?season={season}&seasontype={season_type}",
                 ttl=ttl, transform=_trim_schedule)
    games = []
    for e in d["events"]:
        side = {x["homeAway"]: x for x in e["competitors"]}
        me = "home" if side["home"]["id"] == str(team_id) else "away"
        opp = "away" if me == "home" else "home"
        games.append(ScheduleGame(e["id"], et_date(e["date"]), me == "home", side[opp]["id"],
                                  _int(side[me]["score"]), _int(side[opp]["score"]), e["completed"]))
    return games


# ---------- box score ----------

@dataclass
class PlayerLine:
    athlete_id: str
    name: str
    minutes: float
    points: int
    played: bool


@dataclass
class TeamBox:
    pts: int
    fga: int
    fta: int
    oreb: int
    tov: int
    players: list[PlayerLine] = field(default_factory=list)


@dataclass
class Summary:
    event_id: str
    teams: dict[str, TeamBox]          # team_id -> box
    line: float | None                 # bookmaker total from pickcenter (kept after the game)


def _made_att(v: str) -> tuple[int, int]:
    m, _, a = str(v).partition("-")
    return _int(m), _int(a)


def _trim_summary(d: dict) -> dict:
    """Keep only what the model needs (full summary is ~400 KB, this is ~5 KB)."""
    teams = {}
    for t in d.get("boxscore", {}).get("teams", []):
        teams[t["team"]["id"]] = {s["name"]: s["displayValue"] for s in t.get("statistics", [])}
    players = {}
    for p in d.get("boxscore", {}).get("players", []):
        g = p["statistics"][0] if p.get("statistics") else {"keys": [], "athletes": []}
        rows = []
        for a in g["athletes"]:
            row = dict(zip(g["keys"], a.get("stats") or []))
            rows.append({"id": _athlete_id(a["athlete"]) or a["athlete"].get("displayName"),
                         "name": a["athlete"].get("displayName", "?"),
                         "min": row.get("minutes"), "pts": row.get("points"),
                         "dnp": a.get("didNotPlay", False) or not a.get("stats")})
        players[p["team"]["id"]] = rows
    line = None
    for pc in d.get("pickcenter", []):
        if pc.get("overUnder") is not None:
            line = pc["overUnder"]
            break
    # summary["injuries"] is NOT kept: it is today's report, not the one from game day
    return {"teams": teams, "players": players, "line": line}


def summary(event_id: str) -> Summary:
    d = get_json(f"{BASE}/summary?event={event_id}", ttl=None, transform=_trim_summary)
    teams = {}
    for tid, st in d["teams"].items():
        _, fga = _made_att(st.get("fieldGoalsMade-fieldGoalsAttempted", "0-0"))
        _, fta = _made_att(st.get("freeThrowsMade-freeThrowsAttempted", "0-0"))
        tov = _int(st.get("totalTurnovers", st.get("turnovers")))
        players = [PlayerLine(p["id"], p["name"], float(_int(p["min"])), _int(p["pts"]), not p["dnp"])
                   for p in d["players"].get(tid, [])]
        pts = sum(p.points for p in players)
        teams[tid] = TeamBox(pts, fga, fta, _int(st.get("offensiveRebounds")), tov, players)
    line = float(d["line"]) if d["line"] is not None else None
    return Summary(str(event_id), teams, line)
