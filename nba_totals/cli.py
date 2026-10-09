"""Command line: `python -m nba_totals predict` and `python -m nba_totals backtest`."""
import argparse
from dataclasses import replace
from datetime import date, datetime

from . import espn
from .config import SETTINGS, Settings
from .data import Store, all_team_ids, league_avg, roster_from_box, roster_from_report
from .model import TeamSide, blend, predict, team_form, verdict

SHOWN_STATUSES = ("Out", "Doubtful", "Questionable", "Day-To-Day")


def _f(x, fmt="{:.1f}"):
    return "-" if x is None else fmt.format(x)


def parse_lines(items: list[str]) -> dict[str, float]:
    """['CHI=239.5', 'MEM@CHI=231'] -> {'CHI': 239.5, 'MEM@CHI': 231.0}"""
    out = {}
    for it in items or []:
        key, _, val = it.partition("=")
        out[key.strip().upper()] = float(val)
    return out


def _manual_line(g: espn.Game, lines: dict[str, float]) -> float | None:
    for key in (f"{g.away_abbr}@{g.home_abbr}", g.home_abbr, g.away_abbr):
        if key in lines:
            return lines[key]
    return None


def _form_text(recs, day, n, home):
    allf = team_form(recs, day, n)
    split = team_form(recs, day, n, home=home)
    where = "home" if home else "away"
    s = (f"last {allf.games}: scored {_f(allf.pts_for)}, allowed {_f(allf.pts_against)}, "
         f"pace {_f(allf.pace)}, ORtg {_f(allf.ortg)}, DRtg {_f(allf.drtg)}")
    if split:
        s += f" | {where} ({split.games}): {_f(split.pts_for)} / {_f(split.pts_against)}"
    return s


def cmd_predict(args, s: Settings) -> None:
    day = args.date or datetime.now(espn.ET).date()
    today = datetime.now(espn.ET).date()
    season = espn.season_of(day)
    seasons = [season - 1, season]
    games = espn.scoreboard(day)
    if not games:
        print(f"No NBA games on {day}.")
        return
    if any(g.season_type == 1 for g in games):
        print("Note: preseason games. Form is taken from regular-season games, so treat the output with care.\n")

    store = Store()
    print(f"Loading data for {day} (first run downloads box scores, then everything comes from cache/)...")
    all_recs = {}
    last = {}
    for tid in all_team_ids():
        last[tid] = store.last_games(tid, day, s.last_n_games, seasons)
        all_recs[tid] = store.records(tid, last[tid])
    league = league_avg(list(all_recs.values()))
    print(f"League average (last {s.last_n_games} games of every team): {league.ppg:.1f} pts, "
          f"pace {_f(league.pace)}, rating {_f(league.rtg)}\n")

    injury_report = espn.injuries() if day >= today else {}
    if day < today:
        print("Past date: ESPN has no injury report for that day, so 'not playing' = regulars "
              "missing from the box score (known only after the game).\n")
    manual = parse_lines(args.line)
    stale_note = False
    rows = []
    for g in games:
        finished = g.status == "STATUS_FINAL"
        box = store.summary(g.event_id) if finished else None
        sides = {}
        out_text = {}
        for tid, abbr in ((g.home_id, g.home_abbr), (g.away_id, g.away_abbr)):
            avgs = store.player_avgs(tid, last[tid])
            if day < today and box:
                roster = roster_from_box(tid, box, avgs)
                recent = {a.name for a in avgs.values() if a.recent}
                # regulars only: traded-away players are also missing from the box score
                out_text[abbr] = [r for r in roster if r.status and r.name in recent and r.share >= 0.3 and r.ppg >= 3]
            else:
                report = [i for i in injury_report.get(tid, []) if i.status in SHOWN_STATUSES]
                roster = roster_from_report(report, avgs)
                out_text[abbr] = [r for r in roster if r.status]
            if last[tid] and (day - last[tid][-1].day).days > 30:
                # form window is from last season: rosters changed, lineup adjustment would be noise
                stale_note = True
                roster = []
            sides[tid] = TeamSide(all_recs[tid], roster)

        p = predict(sides[g.home_id], sides[g.away_id], day, league, s)
        line = _manual_line(g, manual)
        line = line if line is not None else g.line
        if line is None and box:
            line = box.line
        title = f"{g.away_abbr} @ {g.home_abbr}"
        print(f"=== {title}  ({g.status.replace('STATUS_', '').lower()})")
        print(f"  {g.home_abbr} {_form_text(all_recs[g.home_id], day, s.last_n_games, True)}")
        print(f"  {g.away_abbr} {_form_text(all_recs[g.away_id], day, s.last_n_games, False)}")
        if p is None:
            print("  not enough games to predict\n")
            continue
        for abbr, b2b, lu in ((g.home_abbr, p.home_b2b, p.home_lineup), (g.away_abbr, p.away_b2b, p.away_lineup)):
            missing = ", ".join(f"{a.name} ({a.status}, " + (f"{a.ppg:.1f} pts / {a.mpg:.0f} min)" if a.share
                                                             else "no games in window)") for a in out_text[abbr])
            print(f"  {abbr}: lineup {lu:+.1f} pts{'; back-to-back -' + str(s.back_to_back_penalty) if b2b else ''}"
                  f"{'; ' + missing if missing else ''}")
        print(f"  Prediction: {g.home_abbr} {p.home_pts:.1f} + {g.away_abbr} {p.away_pts:.1f} = {p.total:.1f}"
              f"{'' if g.status != 'STATUS_FINAL' else f'   (actual {g.home_score + g.away_score})'}\n")
        missing = "; ".join(f"{a.name} ({a.status[0] if a.status != 'Day-To-Day' else 'DTD'})"
                            for abbr in (g.home_abbr, g.away_abbr) for a in out_text[abbr]
                            if a.status in ("Out", "Doubtful") or a.ppg >= 10)
        rows.append((title, p.total, line, missing, verdict(p.total, line, s)))

    print(f"{'Match':<11} {'Pred':>6} {'Line':>6} {'Diff':>6}  {'Bet':<7} Not playing / doubtful")
    print("-" * 90)
    for title, pred, line, missing, v in rows:
        diff = None if line is None else pred - line
        print(f"{title:<11} {pred:6.1f} {_f(line):>6} {_f(diff, '{:+.1f}'):>6}  {v:<7} {missing}")
    if stale_note:
        print("\nWarning: the last games of some teams are more than 30 days old (off-season). Form comes "
              "from last season and the lineup adjustment is switched off - wait ~10 games of the new season.")
    print(f"\nBet rule: |prediction - line| >= {s.edge_threshold}. "
          f"Use --line CHI=231.5 to enter a line by hand.")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="nba_totals", description="NBA totals (over/under) analyzer")
    sub = ap.add_subparsers(dest="cmd", required=True)

    pp = sub.add_parser("predict", help="predict totals for one day")
    pp.add_argument("--date", type=date.fromisoformat, help="YYYY-MM-DD (US Eastern), default today")
    pp.add_argument("--line", action="append", help="manual line: CHI=231.5 or MEM@CHI=231.5 (repeatable)")

    bp = sub.add_parser("backtest", help="test the model on a past season")
    bp.add_argument("--season", type=int, default=espn.season_of(datetime.now(espn.ET).date()) - 1,
                    help="ESPN season year, 2026 = 2025-26 (default: last season)")
    bp.add_argument("--min-games", type=int, default=5, help="skip games until both teams played this many")

    for p in (pp, bp):
        p.add_argument("--last-n", type=int, help=f"games in form window (default {SETTINGS.last_n_games})")
        p.add_argument("--injury-factor", type=float, help=f"default {SETTINGS.injury_loss_factor}")
        p.add_argument("--b2b", type=float, help=f"back-to-back penalty (default {SETTINGS.back_to_back_penalty})")
        p.add_argument("--threshold", type=float, help=f"bet threshold (default {SETTINGS.edge_threshold})")
    args = ap.parse_args(argv)

    s = SETTINGS
    for arg, name in (("last_n", "last_n_games"), ("injury_factor", "injury_loss_factor"),
                      ("b2b", "back_to_back_penalty"), ("threshold", "edge_threshold")):
        if getattr(args, arg) is not None:
            s = replace(s, **{name: getattr(args, arg)})

    if args.cmd == "predict":
        cmd_predict(args, s)
    else:
        from .backtest import run_backtest
        run_backtest(args.season, s, args.min_games)
