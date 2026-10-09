from datetime import date, timedelta

from nba_totals.config import Settings
from nba_totals.model import (Absence, GameRecord, LeagueAvg, TeamSide, expected_points, injury_penalty,
                              possessions, predict, team_form, verdict)

D = date(2025, 1, 31)


def games(n, pf, pa, poss=100.0, start=D - timedelta(days=40)):
    return [GameRecord(start + timedelta(days=2 * i), i % 2 == 0, pf, pa, poss, poss) for i in range(n)]


def test_possessions():
    assert possessions(88, 10, 14, 25) == 88 - 10 + 14 + 11


def test_team_form_uses_last_n_before_date():
    gs = games(15, 110, 105)
    gs.append(GameRecord(D, True, 200, 0, 100, 100))  # same day -> must be ignored
    f = team_form(gs, D, 10)
    assert f.games == 10 and f.pts_for == 110 and f.ortg == 110 and f.pace == 100


def test_average_teams_give_league_average():
    lg = LeagueAvg(ppg=112, pace=100, rtg=112)
    f = team_form(games(10, 112, 112), D, 10)
    assert abs(expected_points(f, f, lg) - 112) < 1e-9


def test_good_offence_vs_bad_defence_scores_more():
    lg = LeagueAvg(112, 100, 112)
    off = team_form(games(10, 120, 112), D, 10)
    bad_d = team_form(games(10, 112, 120), D, 10)
    assert expected_points(off, bad_d, lg) > 120


def test_injury_penalty_partial():
    s = Settings(injury_loss_factor=0.5)
    pen = injury_penalty([Absence("A", "Out", 20, 34), Absence("B", "Questionable", 10, 25),
                          Absence("C", "Day-To-Day", 30, 30)], s)
    assert pen == 20 * 0.5 + 10 * 0.4 * 0.5


def test_back_to_back_and_verdict():
    lg = LeagueAvg(112, 100, 112)
    s = Settings(back_to_back_penalty=2.0)
    yesterday = [GameRecord(D - timedelta(days=1), False, 112, 112, 100, 100)]
    h = TeamSide(games(10, 112, 112) + yesterday, [])
    a = TeamSide(games(10, 112, 112), [])
    p = predict(h, a, D, lg, s)
    assert p.home_b2b and not p.away_b2b
    assert abs(p.total - (224 - 2)) < 1e-6
    assert verdict(230, 224) == "OVER" and verdict(219, 224) == "UNDER" and verdict(226, 224) == "skip"
