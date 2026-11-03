#!/usr/bin/env python3
"""
Builds docs/data.json for the Toddy Time Cup page.

Real run (needs ESPN_S2 and SWID env vars):   python update.py
Test run with fake stats (no ESPN needed):    python update.py --mock 6 --out /tmp/data.json
  (--mock N pretends matchup period N is in progress, with a few days played)

How a week is scored: every team's STARTERS (bench/IR excluded) are summed day by day from ESPN's
raw per-day stat lines. 9 categories; most categories wins; 5+ wins the matchup.
"""
import argparse
import itertools
import json
import os
import random
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent

# key, label, lower is better
CATS = [
    ("PTS", "Points", False),
    ("AST", "Assists", False),
    ("REB", "Rebounds", False),
    ("3PM", "3-pointers", False),
    ("STL", "Steals", False),
    ("BLK", "Blocks", False),
    ("FG%", "FG%", False),
    ("FT%", "FT%", False),
    ("TO", "Turnovers", True),
]

# ESPN raw stat ids -> names
RAW_IDS = {"0": "PTS", "1": "BLK", "2": "STL", "3": "AST", "4": "OREB", "5": "DREB",
           "11": "TO", "13": "FGM", "14": "FGA", "15": "FTM", "16": "FTA", "17": "3PM"}
BENCH_SLOTS = {12, 13}  # BE, IR


# --------------------------------------------------------------------------- data sources

class EspnSource:
    def __init__(self, cfg):
        from espn_api.basketball import League
        self.cfg = cfg
        self.league = League(league_id=cfg["league_id"], year=cfg["year"],
                             espn_s2=os.environ["ESPN_S2"], swid=os.environ["SWID"])
        self._periods = None

    def team_names(self):
        return {t.team_id: t.team_name for t in self.league.teams}

    def current_scoring_period(self):
        return int(self.league.current_week)

    def periods(self):
        if self._periods is not None:
            return self._periods
        raw = {}
        mp = getattr(self.league.settings, "matchup_periods", None) or {}
        for k, v in mp.items():
            raw[int(k)] = sorted(int(x) for x in v)
        for k, v in self.league.matchup_ids.items():  # started periods, fallback
            raw.setdefault(int(k), sorted(int(x) for x in v))
        # scoring period -> calendar date, anchored on NBA game timestamps (US Eastern)
        tz = ZoneInfo(self.cfg["timezone"])
        known = {}
        for games_by_sp in self.league.pro_schedule.values():
            for sp, games in games_by_sp.items():
                d = datetime.fromtimestamp(min(g["date"] for g in games) / 1000.0, tz).date()
                sp = int(sp)
                known[sp] = min(d, known.get(sp, d))
        if not known:
            raise RuntimeError("Could not work out calendar dates from ESPN's NBA schedule")
        anchor_sp = min(known)
        anchor = known[anchor_sp]

        def sp_date(sp):
            return anchor + timedelta(days=sp - anchor_sp)

        self._periods = {p: {"days": d, "start": sp_date(min(d)), "end": sp_date(max(d))}
                         for p, d in raw.items() if d}
        return self._periods

    def totals(self, period):
        """{team_id: {raw stat: total}} for starters, day by day, up to today's scoring period."""
        days = [d for d in self.periods()[period]["days"] if d <= self.current_scoring_period()]
        totals = defaultdict(lambda: defaultdict(float))
        for day in days:
            params = {"view": ["mMatchupScore", "mScoreboard"], "scoringPeriodId": day}
            filters = {"schedule": {"filterMatchupPeriodIds": {"value": [period]}}}
            data = self.league.espn_request.league_get(
                params=params, headers={"x-fantasy-filter": json.dumps(filters)})
            for matchup in data["schedule"]:
                for side in ("home", "away"):
                    if side in matchup:
                        add_side(totals, matchup[side])
        return {tid: dict(t) for tid, t in totals.items()}


def add_side(totals, side_data):
    """Add one team's starters for one day into totals[teamId]."""
    team_id = side_data["teamId"]
    _ = totals[team_id]  # make sure a team with no stats yet still appears
    for entry in side_data.get("rosterForCurrentScoringPeriod", {}).get("entries", []):
        if entry.get("lineupSlotId") in BENCH_SLOTS:
            continue
        for s in entry["playerPoolEntry"]["player"].get("stats", []):
            if s.get("statSourceId") == 0 and s.get("statSplitTypeId") == 5:  # actual, single day
                for k, name in RAW_IDS.items():
                    totals[team_id][name] += s.get("stats", {}).get(k, 0.0)


class MockSource:
    """Fake league for testing: period `now_period` is in progress (day 3 of 7)."""

    def __init__(self, cfg, now_period, seed=7):
        self.cfg, self.now = cfg, now_period
        self.rng = random.Random(seed)
        self.ids = {m["team_id"]: m["team_name"] for c in cfg["conferences"] for m in c["members"]}
        self._cache = {}

    def team_names(self):
        return dict(self.ids)

    def periods(self):
        base = date(2026, 11, 9)  # period 4 starts
        out = {}
        for p in range(1, 12):
            start = base + timedelta(days=7 * (p - 4))
            out[p] = {"days": list(range(7 * p - 6, 7 * p + 1)), "start": start, "end": start + timedelta(days=6)}
        return out

    def current_scoring_period(self):
        return 7 * self.now - 6 + 2  # third day of the current period

    def totals(self, period):
        if period in self._cache:
            return self._cache[period]
        rng = random.Random(period * 101 + 7)
        frac = 1.0 if period < self.now else 3 / 7
        out = {}
        for tid in self.ids:
            fga = rng.randint(380, 520) * frac
            fgm = fga * rng.uniform(0.43, 0.53)
            fta = rng.randint(100, 160) * frac
            ftm = fta * rng.uniform(0.72, 0.85)
            tpm = rng.randint(40, 65) * frac
            out[tid] = {
                "FGA": round(fga), "FGM": round(fgm), "FTA": round(fta), "FTM": round(ftm), "3PM": round(tpm),
                "OREB": round(rng.randint(40, 70) * frac), "DREB": round(rng.randint(120, 180) * frac),
                "AST": round(rng.randint(90, 140) * frac), "STL": round(rng.randint(20, 40) * frac),
                "BLK": round(rng.randint(10, 30) * frac), "TO": round(rng.randint(35, 70) * frac),
            }
            t = out[tid]
            t["PTS"] = 2 * t["FGM"] + t["3PM"] + t["FTM"]
        self._cache[period] = out
        return out


# --------------------------------------------------------------------------- scoring

def nine_cat(t):
    return {
        "PTS": t["PTS"], "AST": t["AST"], "REB": t["OREB"] + t["DREB"], "3PM": t["3PM"],
        "STL": t["STL"], "BLK": t["BLK"],
        "FG%": t["FGM"] / t["FGA"] if t["FGA"] else 0.0,
        "FT%": t["FTM"] / t["FTA"] if t["FTA"] else 0.0,
        "TO": t["TO"],
    }


def score_matchup(ta, tb):
    """Returns (per-category result {'a'|'b'|'tie'}, a_cats, b_cats, tied_cats, winner)."""
    a9, b9 = nine_cat(ta), nine_cat(tb)
    res, a_n, b_n, tie_n = {}, 0, 0, 0
    for key, _label, lower in CATS:
        x, y = a9[key], b9[key]
        if x == y:
            res[key] = "tie"; tie_n += 1
        elif (x < y) == lower:
            res[key] = "a"; a_n += 1
        else:
            res[key] = "b"; b_n += 1
    winner = "a" if a_n >= 5 else "b" if b_n >= 5 else "tie"
    return res, a_n, b_n, tie_n, winner


# --------------------------------------------------------------------------- standings

def rank_conference(mgrs, rec, games):
    """Wins, then head-to-head among teams tied on wins, then total categories won.
    games: list of {'a': mgr, 'b': mgr, 'winner': 'a'|'b'|'tie'} for completed matchups."""
    by_wins = defaultdict(list)
    for m in mgrs:
        by_wins[rec[m]["w"]].append(m)
    out = []
    for w in sorted(by_wins, reverse=True):
        g = by_wins[w]
        if len(g) > 1:
            h2h = {m: 0 for m in g}
            for gm in games:
                if gm["a"] in h2h and gm["b"] in h2h and gm["winner"] != "tie":
                    h2h[gm[gm["winner"]]] += 1
            g = sorted(g, key=lambda m: (-h2h[m], -rec[m]["cats"], m))
        out += g
    return out


def clinch_flags(mgrs, rec, remaining, advance):
    """Conservative clinch/elimination using only win totals over every possible outcome of the
    remaining games (including tied matchups). Ties on wins count against a team when testing a
    clinch, and for it when testing elimination."""
    clinched = {m: True for m in mgrs}
    eliminated = {m: True for m in mgrs}
    for outcome in itertools.product((0, 1, 2), repeat=len(remaining)):
        wins = {m: rec[m]["w"] for m in mgrs}
        for (a, b), o in zip(remaining, outcome):
            if o == 0: wins[a] += 1
            elif o == 1: wins[b] += 1
        for m in mgrs:
            at_least = sum(1 for o in mgrs if o != m and wins[o] >= wins[m])
            more = sum(1 for o in mgrs if o != m and wins[o] > wins[m])
            if at_least > advance - 1: clinched[m] = False
            if more < advance: eliminated[m] = False
        if not any(clinched.values()) and not any(eliminated.values()):
            break
    return clinched, eliminated


# --------------------------------------------------------------------------- build

def build(cfg, src, prev):
    tz = ZoneInfo(cfg["timezone"])
    names = src.team_names()
    pinfo = src.periods()
    cur_sp = src.current_scoring_period()
    advance = cfg.get("advance_per_conference", 2)

    members = {}
    for conf in cfg["conferences"]:
        for m in conf["members"]:
            members[m["manager"]] = {**m, "conference": conf["name"]}

    def ident(mgr):
        m = members[mgr]
        return {"manager": mgr, "team_id": m["team_id"], "conference": m["conference"],
                "team": names.get(m["team_id"], m["team_name"])}

    prev_periods = {p["period"]: p for p in (prev or {}).get("periods", [])}

    def status_of(p):
        days = pinfo[p]["days"]
        if cur_sp > max(days): return "final"
        if cur_sp >= min(days): return "live"
        return "upcoming"

    def get_totals(p, status):
        if status == "upcoming":
            return {}
        old = prev_periods.get(p)
        if old and old.get("frozen") and old.get("raw"):
            return {int(k): v for k, v in old["raw"].items()}
        return src.totals(p)

    def play(p, status, conf_name, pair, raw, projected=False):
        a, b = ident(pair[0]), ident(pair[1])
        mu = {"conference": conf_name, "projected": projected, "a": a, "b": b,
              "stats": None, "cat_results": None, "score": None, "winner": None}
        ta, tb = raw.get(a["team_id"]), raw.get(b["team_id"])
        if status != "upcoming" and ta is not None and tb is not None:
            res, an, bn, tn, win = score_matchup(ta, tb)
            keep = ("FGM", "FGA", "FTM", "FTA")
            mu["stats"] = {"a": {**nine_cat(ta), **{k: ta[k] for k in keep}},
                           "b": {**nine_cat(tb), **{k: tb[k] for k in keep}}}
            mu["cat_results"] = res
            mu["score"] = {"a": an, "b": bn, "ties": tn}
            mu["winner"] = win if status == "final" else None
            mu["leader"] = win  # who is ahead right now (same as winner once final)
        return mu

    periods_out, completed = [], defaultdict(list)  # completed[conf] = finished group games
    rec = {m: {"w": 0, "l": 0, "t": 0, "cats": 0} for m in members}
    remaining = defaultdict(list)

    def period_shell(p, kind, label):
        st = status_of(p)
        return {"period": p, "kind": kind, "label": label, "status": st,
                "frozen": cur_sp > max(pinfo[p]["days"]) + 1,
                "start": pinfo[p]["start"].isoformat(), "end": pinfo[p]["end"].isoformat(),
                "matchups": [], "byes": [], "note": None}

    # ---- group stage
    for p in cfg["group_periods"]:
        shell = period_shell(p, "group", f"Week {p}")
        raw = get_totals(p, shell["status"])
        shell["raw"] = {str(k): v for k, v in raw.items()}
        for conf in cfg["conferences"]:
            pairs = cfg["schedule"][str(p)][conf["name"]]
            playing = {m for pr in pairs for m in pr}
            for m in conf["members"]:
                if m["manager"] not in playing:
                    shell["byes"].append(ident(m["manager"]))
            for pr in pairs:
                mu = play(p, shell["status"], conf["name"], pr, raw)
                shell["matchups"].append(mu)
                if shell["status"] == "final" and mu["stats"]:
                    a, b = pr
                    rec[a]["cats"] += mu["score"]["a"]; rec[b]["cats"] += mu["score"]["b"]
                    if mu["winner"] == "tie":
                        rec[a]["t"] += 1; rec[b]["t"] += 1
                    else:
                        win, lose = (a, b) if mu["winner"] == "a" else (b, a)
                        rec[win]["w"] += 1; rec[lose]["l"] += 1
                    completed[conf["name"]].append({"a": a, "b": b, "winner": mu["winner"]})
                else:
                    remaining[conf["name"]].append(tuple(pr))
        periods_out.append(shell)

    group_done = all(s["status"] == "final" for s in periods_out)
    any_result = any(s["status"] == "final" for s in periods_out)

    # ---- standings
    conferences_out, seeds = [], {}
    for conf in cfg["conferences"]:
        mgrs = [m["manager"] for m in conf["members"]]
        order = rank_conference(mgrs, rec, completed[conf["name"]])
        if group_done:
            clin = {m: order.index(m) < advance for m in mgrs}
            elim = {m: order.index(m) >= advance for m in mgrs}
        else:
            clin, elim = clinch_flags(mgrs, rec, remaining[conf["name"]], advance)
        seeds[conf["name"]] = order
        rows = []
        for i, m in enumerate(order, 1):
            r = rec[m]
            rows.append({**ident(m), "rank": i, "w": r["w"], "l": r["l"], "t": r["t"],
                         "cats": r["cats"], "clinched": clin[m], "eliminated": elim[m]})
        conferences_out.append({"name": conf["name"], "complete": group_done, "standings": rows})

    # ---- semifinal (1st vs 2nd in each conference)
    sp = cfg["semifinal_period"]
    semi = period_shell(sp, "semifinal", "Semifinals")
    semi_raw = get_totals(sp, semi["status"]) if group_done else {}
    semi["raw"] = {str(k): v for k, v in semi_raw.items()}
    if any_result:
        for conf in cfg["conferences"]:
            order = seeds[conf["name"]]
            semi["matchups"].append(play(sp, semi["status"] if group_done else "upcoming", conf["name"],
                                         (order[0], order[1]), semi_raw, projected=not group_done))
        if not group_done:
            semi["note"] = "Projected from current standings. Matchups lock after Week 8."
    else:
        semi["note"] = "1st plays 2nd in each conference. Matchups are set after Week 8."
    periods_out.append(semi)

    # ---- final (winners of the two semifinals)
    fp = cfg["final_period"]
    fin = period_shell(fp, "final", "Final")
    fin_raw = {}
    semi_winners = []
    if group_done and semi["status"] == "final":
        for mu in semi["matchups"]:
            if mu["winner"] in ("a", "b"):
                semi_winners.append(mu[mu["winner"]]["manager"])
    if len(semi_winners) == 2:
        fin_raw = get_totals(fp, fin["status"])
        fin["matchups"].append(play(fp, fin["status"], "Final", tuple(semi_winners), fin_raw))
    else:
        fin["note"] = ("A semifinal ended in a tie and needs a ruling." if group_done and semi["status"] == "final"
                       else "The two semifinal winners play for the cup.")
    fin["raw"] = {str(k): v for k, v in fin_raw.items()}
    periods_out.append(fin)

    now = datetime.now(tz)
    cur_period = next((s["period"] for s in periods_out if s["status"] == "live"), None)
    return {
        "title": cfg["title"], "season": cfg["season_label"],
        "updated": now.isoformat(timespec="seconds"),
        "current_period": cur_period,
        "cats": [{"key": k, "label": l, "lower_better": lo} for k, l, lo in CATS],
        "conferences": conferences_out,
        "periods": periods_out,
    }


def strip_volatile(d):
    d = dict(d or {})
    d.pop("updated", None)
    return d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mock", type=int, help="use fake stats; N = matchup period in progress")
    ap.add_argument("--out", default=str(ROOT / "docs" / "data.json"))
    args = ap.parse_args()

    cfg = json.loads((ROOT / "config.json").read_text())
    src = MockSource(cfg, args.mock) if args.mock else EspnSource(cfg)
    out = Path(args.out)
    prev = json.loads(out.read_text()) if out.exists() and not args.mock else None

    data = build(cfg, src, prev)
    if prev and strip_volatile(prev) == strip_volatile(data):
        data["updated"] = prev["updated"]  # nothing changed: keep timestamp so no useless commit
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, indent=1))
    print(f"Wrote {out}  (live period: {data['current_period']})")


if __name__ == "__main__":
    sys.exit(main())
