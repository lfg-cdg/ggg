"""Turn ESPN data into model inputs: team game records, player averages, league averages."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date

from . import espn
from .cache import get_json
from .model import GameRecord, LeagueAvg, RosterPlayer, possessions


def all_team_ids() -> list[str]:
    d = get_json(f"{espn.BASE}/teams", ttl=None)
    return [t["team"]["id"] for t in d["sports"][0]["leagues"][0]["teams"]]


@dataclass
class PlayerAvg:
    name: str
    ppg: float
    mpg: float
    share: float     # games played / team games in the window
    recent: bool     # played in one of the last 2 games (False: traded away or long injury)


class Store:
    """Keeps schedules and box scores in memory on top of the disk cache."""

    def __init__(self, workers: int = 8):
        self.workers = workers
        self._sched: dict[tuple, list[espn.ScheduleGame]] = {}
        self._sum: dict[str, espn.Summary] = {}

    def schedule(self, team_id: str, season: int) -> list[espn.ScheduleGame]:
        key = (team_id, season)
        if key not in self._sched:
            self._sched[key] = espn.team_schedule(team_id, season)
        return self._sched[key]

    def summaries(self, event_ids) -> None:
        """Download missing box scores in parallel."""
        todo = [e for e in set(event_ids) if e not in self._sum]
        if not todo:
            return
        with ThreadPoolExecutor(self.workers) as ex:
            for eid, s in zip(todo, ex.map(espn.summary, todo)):
                self._sum[eid] = s

    def summary(self, event_id: str) -> espn.Summary:
        self.summaries([event_id])
        return self._sum[event_id]

    def last_games(self, team_id: str, day: date, n: int, seasons: list[int]) -> list[espn.ScheduleGame]:
        """Last n finished regular-season games before `day`."""
        gs = [g for s in seasons for g in self.schedule(team_id, s) if g.completed and g.day < day]
        return sorted(gs, key=lambda g: g.day)[-n:]

    def records(self, team_id: str, games: list[espn.ScheduleGame]) -> list[GameRecord]:
        self.summaries(g.event_id for g in games)
        out = []
        for g in games:
            box = self._sum[g.event_id].teams
            me, opp = box.get(team_id), box.get(g.opp_id)
            poss = opp_poss = None
            if me and opp and me.fga and opp.fga:
                poss = possessions(me.fga, me.oreb, me.tov, me.fta)
                opp_poss = possessions(opp.fga, opp.oreb, opp.tov, opp.fta)
            out.append(GameRecord(g.day, g.is_home, g.pts_for, g.pts_against, poss, opp_poss))
        return out

    def player_avgs(self, team_id: str, games: list[espn.ScheduleGame]) -> dict[str, PlayerAvg]:
        self.summaries(g.event_id for g in games)
        acc: dict[str, list] = {}
        for k, g in enumerate(games):
            box = self._sum[g.event_id].teams.get(team_id)
            for p in box.players if box else []:
                if p.played:
                    a = acc.setdefault(p.athlete_id, [p.name, 0, 0.0, 0, False])
                    a[1] += p.points
                    a[2] += p.minutes
                    a[3] += 1
                    a[4] |= k >= len(games) - 2
        n = max(len(games), 1)
        return {pid: PlayerAvg(name, pts / gp, mins / gp, gp / n, recent)
                for pid, (name, pts, mins, gp, recent) in acc.items()}


def roster_from_report(injuries: list[espn.Injury], avgs: dict[str, PlayerAvg]) -> list[RosterPlayer]:
    """Upcoming game: injury report joined with recent averages (by id, then by name).

    Players not in the report are expected to play if they played in one of the last 2 games;
    others (traded away, long injury not reported) are left out.
    """
    status = {}
    for i in injuries:
        key = i.athlete_id if i.athlete_id in avgs else next(
            (pid for pid, a in avgs.items() if a.name.lower() == i.name.lower()), None)
        status[key or "name:" + i.name] = i
    out = []
    for pid, a in avgs.items():
        if pid in status:
            out.append(RosterPlayer(a.name, status.pop(pid).status, a.ppg, a.mpg, a.share))
        elif a.recent:
            out.append(RosterPlayer(a.name, "", a.ppg, a.mpg, a.share))
    out += [RosterPlayer(i.name, i.status, 0.0, 0.0, 0.0) for i in status.values()]  # no recent games
    return out


def roster_from_box(team_id: str, box: "espn.Summary", avgs: dict[str, PlayerAvg]) -> list[RosterPlayer]:
    """Finished game: who really played (known only after the game).

    ESPN keeps no game-day injury report, so past games use the box score instead.
    """
    team = box.teams.get(team_id)
    played = {p.athlete_id for p in team.players if p.played} if team else set()
    return [RosterPlayer(a.name, "" if pid in played else "Out", a.ppg, a.mpg, a.share)
            for pid, a in avgs.items()]


def league_avg(team_records: list[list[GameRecord]]) -> LeagueAvg:
    games = [g for recs in team_records for g in recs]
    ppg = sum(g.pts_for for g in games) / len(games)
    wp = [g for g in games if g.poss and g.opp_poss]
    if not wp:
        return LeagueAvg(ppg, None, None)
    pace = sum((g.poss + g.opp_poss) / 2 for g in wp) / len(wp)
    rtg = 100 * sum(g.pts_for for g in wp) / sum(g.poss for g in wp)
    return LeagueAvg(ppg, pace, rtg)
