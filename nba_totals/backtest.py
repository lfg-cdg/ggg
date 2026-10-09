"""Walk through a past season game by game, predicting each one only from earlier games."""
from dataclasses import dataclass, replace

from .config import Settings
from .data import Store, all_team_ids, league_avg, roster_from_box
from .model import TeamSide, predict


@dataclass
class Row:
    day: object
    title: str
    pred: float
    pred_no_inj: float
    line: float | None
    actual: int


def collect(season: int, s: Settings, min_games: int) -> list[Row]:
    store = Store(workers=12)
    teams = all_team_ids()
    games = {}
    for tid in teams:
        for g in store.schedule(tid, season):
            if g.completed and g.is_home:
                games[g.event_id] = (g.day, tid, g.opp_id, g.pts_for + g.pts_against)
    print(f"Season {season - 1}-{str(season)[2:]}: {len(games)} regular-season games. "
          f"Loading box scores (first run takes a few minutes)...")
    store.summaries(games)

    no_inj = replace(s, injury_loss_factor=0.0)
    league_by_day = {}
    rows = []
    for eid, (day, home, away, actual) in sorted(games.items(), key=lambda kv: kv[1][0]):
        played_before = {t: [g for g in store.schedule(t, season) if g.completed and g.day < day] for t in (home, away)}
        if min(len(v) for v in played_before.values()) < min_games:
            continue
        if day not in league_by_day:
            recs = [store.records(t, store.last_games(t, day, s.last_n_games, [season])) for t in teams]
            league_by_day[day] = league_avg([r for r in recs if r])
        box = store.summary(eid)
        sides = {}
        for t in (home, away):
            last = sorted(played_before[t], key=lambda g: g.day)[-s.last_n_games:]
            sides[t] = TeamSide(store.records(t, last), roster_from_box(t, box, store.player_avgs(t, last)))
        p = predict(sides[home], sides[away], day, league_by_day[day], s)
        p0 = predict(sides[home], sides[away], day, league_by_day[day], no_inj)
        if p is None:
            continue
        rows.append(Row(day, eid, p.total, p0.total, box.line, actual))
    return rows


def _bets(rows: list[Row], thr: float, attr: str = "pred"):
    win = loss = push = 0
    for r in rows:
        if r.line is None:
            continue
        diff = getattr(r, attr) - r.line
        if abs(diff) < thr:
            continue
        if r.actual == r.line:
            push += 1
        elif (r.actual > r.line) == (diff > 0):
            win += 1
        else:
            loss += 1
    return win, loss, push


def report(rows: list[Row], s: Settings) -> None:
    n = len(rows)
    with_line = [r for r in rows if r.line is not None]

    def mae(xs):
        return sum(abs(x) for x in xs) / len(xs)

    def mean(xs):
        return sum(xs) / len(xs)

    print(f"\nGames predicted: {n} (with bookmaker line: {len(with_line)})")
    print(f"  MAE model total          : {mae([r.pred - r.actual for r in rows]):.2f}")
    print(f"  MAE model, no injuries   : {mae([r.pred_no_inj - r.actual for r in rows]):.2f}")
    if with_line:
        print(f"  MAE bookmaker line       : {mae([r.line - r.actual for r in with_line]):.2f}   (same games)")
        print(f"  MAE model on those games : {mae([r.pred - r.actual for r in with_line]):.2f}")
    print(f"  Bias model (pred-actual) : {mean([r.pred - r.actual for r in rows]):+.2f}")
    if with_line:
        print(f"  Bias line (line-actual)  : {mean([r.line - r.actual for r in with_line]):+.2f}")
        print(f"  Mean (model - line)      : {mean([r.pred - r.line for r in with_line]):+.2f}")

    print("\nBets: OVER if prediction > line + threshold, UNDER if below line - threshold. "
          "Odds -110 => need 52.4% to break even.")
    print(f"{'threshold':>9} {'bets':>6} {'won':>5} {'lost':>5} {'push':>5} {'hit %':>6} {'ROI %':>6}   no-injury model hit %")
    for thr in sorted({0.0, 2.0, 3.0, 5.0, 7.0, 10.0, s.edge_threshold}):
        w, l, p = _bets(rows, thr)
        w0, l0, _ = _bets(rows, thr, "pred_no_inj")
        hit = 100 * w / (w + l) if w + l else 0
        roi = 100 * (w * 100 / 110 - l) / (w + l + p) if w + l + p else 0
        hit0 = 100 * w0 / (w0 + l0) if w0 + l0 else 0
        mark = "  <- current setting" if thr == s.edge_threshold else ""
        print(f"{thr:9.1f} {w + l + p:6d} {w:5d} {l:5d} {p:5d} {hit:6.1f} {roi:+6.1f}   {hit0:6.1f}{mark}")


def run_backtest(season: int, s: Settings, min_games: int) -> None:
    rows = collect(season, s, min_games)
    if not rows:
        print("No games to test.")
        return
    report(rows, s)
