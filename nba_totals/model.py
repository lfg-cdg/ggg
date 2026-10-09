"""Totals model: pure functions, no network access."""
from dataclasses import dataclass
from datetime import date, timedelta

from .config import SETTINGS, Settings


@dataclass
class GameRecord:
    """One finished game seen from a team's side."""
    day: date
    is_home: bool
    pts_for: float
    pts_against: float
    poss: float | None = None       # team possessions (None if box score missing)
    opp_poss: float | None = None


@dataclass
class TeamForm:
    games: int
    pts_for: float
    pts_against: float
    pace: float | None          # possessions per game
    ortg: float | None          # points per 100 possessions
    drtg: float | None


@dataclass
class RosterPlayer:
    name: str
    status: str     # "" = expected to play, else injury status ("Out", "Day-To-Day", ...)
    ppg: float      # points per game in the games he played (last N team games)
    mpg: float
    share: float    # part of the last N team games he played


def possessions(fga: float, oreb: float, tov: float, fta: float) -> float:
    return fga - oreb + tov + 0.44 * fta


def _mean(xs):
    xs = list(xs)
    return sum(xs) / len(xs) if xs else None


def team_form(games: list[GameRecord], before: date, n: int, home: bool | None = None) -> TeamForm | None:
    """Form over the last n games played strictly before `before`.

    home=True/False keeps only home/away games, None keeps all.
    """
    gs = [g for g in games if g.day < before and (home is None or g.is_home == home)]
    gs = sorted(gs, key=lambda g: g.day)[-n:]
    if not gs:
        return None
    with_poss = [g for g in gs if g.poss and g.opp_poss]
    pace = ortg = drtg = None
    if with_poss:
        tp = sum(g.poss for g in with_poss)
        op = sum(g.opp_poss for g in with_poss)
        pace = (tp + op) / 2 / len(with_poss)
        ortg = 100 * sum(g.pts_for for g in with_poss) / tp
        drtg = 100 * sum(g.pts_against for g in with_poss) / op
    return TeamForm(len(gs), _mean(g.pts_for for g in gs), _mean(g.pts_against for g in gs), pace, ortg, drtg)


def blend(overall: TeamForm, split: TeamForm | None, w: float) -> TeamForm:
    """Mix overall form with home-only/away-only form."""
    if split is None or split.games < 3:
        return overall

    def mix(a, b):
        if a is None or b is None:
            return a
        return (1 - w) * a + w * b

    return TeamForm(overall.games, mix(overall.pts_for, split.pts_for), mix(overall.pts_against, split.pts_against),
                    mix(overall.pace, split.pace), mix(overall.ortg, split.ortg), mix(overall.drtg, split.drtg))


@dataclass
class LeagueAvg:
    ppg: float
    pace: float | None
    rtg: float | None


def expected_points(off: TeamForm, deff: TeamForm, league: LeagueAvg) -> float:
    """Points `off` should score against `deff`, normalised to the league average."""
    if None not in (off.pace, deff.pace, off.ortg, deff.drtg, league.pace, league.rtg):
        pace = off.pace * deff.pace / league.pace
        rtg = off.ortg * deff.drtg / league.rtg
        return pace * rtg / 100
    # fallback when possessions are unknown: plain points
    return off.pts_for * deff.pts_against / league.ppg


def miss_prob(status: str, s: Settings = SETTINGS) -> float:
    return dict(s.injury_miss_prob).get(status, 0.0)


def lineup_delta(roster: list[RosterPlayer], s: Settings = SETTINGS) -> float:
    """Points to add to a team's form because today's lineup differs from the lineup of the form window.

    The last-N form already contains every player in proportion to the games he played (share).
    A player who misses today removes ppg*share; a player who plays today but missed part of the
    window (back from injury) adds ppg*(1-share). Replacements cover part of it -> injury_loss_factor.
    """
    total = 0.0
    for p in roster:
        q = miss_prob(p.status, s)
        total += p.ppg * ((1 - q) * (1 - p.share) - q * p.share)
    return total * s.injury_loss_factor


def played_yesterday(games: list[GameRecord], day: date) -> bool:
    return any(g.day == day - timedelta(days=1) for g in games)


def verdict(pred: float, line: float | None, s: Settings = SETTINGS) -> str:
    if line is None:
        return "no line"
    diff = pred - line
    if diff >= s.edge_threshold:
        return "OVER"
    if diff <= -s.edge_threshold:
        return "UNDER"
    return "skip"


@dataclass
class TeamSide:
    games: list[GameRecord]
    roster: list[RosterPlayer]


@dataclass
class Prediction:
    home_pts: float
    away_pts: float
    home_b2b: bool
    away_b2b: bool
    home_lineup: float    # signed lineup adjustment, negative = weaker than in form window
    away_lineup: float

    @property
    def total(self) -> float:
        return self.home_pts + self.away_pts


def predict(home: TeamSide, away: TeamSide, day: date, league: LeagueAvg, s: Settings = SETTINGS) -> Prediction | None:
    h_all = team_form(home.games, day, s.last_n_games)
    a_all = team_form(away.games, day, s.last_n_games)
    if h_all is None or a_all is None:
        return None
    h = blend(h_all, team_form(home.games, day, s.last_n_games, home=True), s.home_away_weight)
    a = blend(a_all, team_form(away.games, day, s.last_n_games, home=False), s.home_away_weight)
    hp, ap = expected_points(h, a, league), expected_points(a, h, league)
    h_b2b, a_b2b = played_yesterday(home.games, day), played_yesterday(away.games, day)
    h_lu, a_lu = lineup_delta(home.roster, s), lineup_delta(away.roster, s)
    hp += h_lu - (s.back_to_back_penalty if h_b2b else 0)
    ap += a_lu - (s.back_to_back_penalty if a_b2b else 0)
    return Prediction(hp, ap, h_b2b, a_b2b, h_lu, a_lu)
