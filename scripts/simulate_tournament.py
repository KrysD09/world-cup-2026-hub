#!/usr/bin/env python3
"""
simulate_tournament.py — Monte Carlo simulator for the 2026 World Cup.

Runs the full remaining tournament N times (hybrid Elo + Poisson engine) and
writes data/processed/sim_probabilities.json for the dashboard to render.

Designed to run hands-free in GitHub Actions after the daily data collection.
Reads the same files the rest of the pipeline produces; degrades gracefully
to Elo priors if live results aren't present yet.

Usage:
    python scripts/simulate_tournament.py            # 10,000 sims
    python scripts/simulate_tournament.py --sims 50000
    python scripts/simulate_tournament.py --seed 7
"""
import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

# ── Paths (script lives in scripts/, data in data/) ─────────────────────────
ROOT = Path(__file__).resolve().parent.parent
PROCESSED = ROOT / "data" / "processed"
OUT_FILE = PROCESSED / "sim_probabilities.json"

# ── Model constants ─────────────────────────────────────────────────────────
HOME_ADV = 65          # Elo bonus for host nations on home soil
ELO_DIV = 400.0        # standard Elo logistic divisor
BASE_GOALS = 1.35      # league-avg goals per team per match
GOAL_TILT = 0.35       # how strongly Elo gap skews the goal split

HOSTS = {"USA", "United States", "Mexico", "Canada"}

ROUND_ORDER = ["R32", "R16", "QF", "SF", "Final", "Champion"]
ROUND_RANK = {r: i for i, r in enumerate(ROUND_ORDER)}

# ── Fallback Elo snapshot (approximate, pre-tournament 2026) ────────────────
# Update occasionally; live results adjust the *story* via locked matches.
FALLBACK_ELO = {
    "Argentina": 2120, "France": 2080, "Brazil": 2060, "England": 2040,
    "Spain": 2035, "Netherlands": 2000, "Portugal": 1995, "Belgium": 1950,
    "Germany": 1965, "Italy": 1940, "Croatia": 1920, "Uruguay": 1915,
    "Colombia": 1900, "Morocco": 1890, "USA": 1820, "United States": 1820,
    "Mexico": 1800, "Japan": 1830, "Senegal": 1815, "Switzerland": 1860,
    "Denmark": 1855, "Ecuador": 1790, "Canada": 1760, "South Korea": 1775,
    "Australia": 1740, "Iran": 1770, "Serbia": 1810, "Poland": 1790,
    "Austria": 1830, "Nigeria": 1780, "Ivory Coast": 1745, "Ghana": 1720,
    "Cameroon": 1730, "Saudi Arabia": 1640, "Qatar": 1660, "Tunisia": 1700,
    "Egypt": 1740, "Norway": 1880, "Sweden": 1820, "Ukraine": 1830,
    "Turkey": 1825, "Paraguay": 1730, "Peru": 1740, "Chile": 1770,
    "Panama": 1680, "Costa Rica": 1680, "Jamaica": 1660, "Haiti": 1560,
    "New Zealand": 1600, "Algeria": 1760, "South Africa": 1700,
    "Scotland": 1810, "Wales": 1790, "Uzbekistan": 1680, "Iraq": 1660,
    "Jordan": 1640, "Curaçao": 1560, "Cape Verde Islands": 1600,
    "Bosnia-Herzegovina": 1700, "Czechia": 1780, "Congo DR": 1680,
}

# ── Default group layout — EDIT to the official draw ────────────────────────
# Auto-overridden if scoreboard.json encodes a {"groups": {...}} field.
DEFAULT_GROUPS = {
    "A": ["Mexico", "South Africa", "South Korea", "Czechia"],
    "B": ["Canada", "Bosnia-Herzegovina", "Qatar", "Switzerland"],
    "C": ["Brazil", "Morocco", "Haiti", "Scotland"],
    "D": ["United States", "Paraguay", "Australia", "Turkey"],
    "E": ["Germany", "Curaçao", "Ivory Coast", "Ecuador"],
    "F": ["Netherlands", "Japan", "Sweden", "Tunisia"],
    "G": ["Belgium", "Egypt", "Iran", "New Zealand"],
    "H": ["Spain", "Cape Verde Islands", "Saudi Arabia", "Uruguay"],
    "I": ["France", "Senegal", "Norway", "Iraq"],
    "J": ["Argentina", "Algeria", "Austria", "Jordan"],
    "K": ["Portugal", "Congo DR", "England", "Ghana"],
    "L": ["Panama", "Croatia", "Uzbekistan", "Colombia"],
}

# Field-name candidates for parsing latest_matches.json
FIELD_MAP = {
    "home": ["home", "homeTeam", "home_team", "team_home"],
    "away": ["away", "awayTeam", "away_team", "team_away"],
    "hg": ["home_goals", "homeGoals", "hg", "score_home", "home_score"],
    "ag": ["away_goals", "awayGoals", "ag", "score_away", "away_score"],
    "status": ["status", "state", "played"],
}


# ── Helpers ─────────────────────────────────────────────────────────────────
def log(msg):
    print(f"[simulate] {msg}", flush=True)


def safe_load_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        log(f"WARN {path.name} not found — continuing with fallback.")
        return None
    except json.JSONDecodeError as e:
        log(f"WARN {path.name} invalid JSON ({e}) — continuing with fallback.")
        return None


def pick(d, keys):
    for k in keys:
        if isinstance(d, dict) and k in d and d[k] is not None:
            return d[k]
    return None


def name_of(x):
    if isinstance(x, dict):
        return x.get("name") or x.get("team") or str(x)
    return x


def load_groups(scoreboard):
    if isinstance(scoreboard, dict) and isinstance(scoreboard.get("groups"), dict):
        g = {k: list(v) for k, v in scoreboard["groups"].items()}
        log(f"Groups loaded from scoreboard.json ({len(g)} groups).")
        return g
    log(f"Using built-in group layout ({len(DEFAULT_GROUPS)} groups) — "
        f"verify against the official draw.")
    return {k: list(v) for k, v in DEFAULT_GROUPS.items()}


def parse_played(matches_raw):
    played = []
    if not matches_raw:
        return played
    rows = (matches_raw["matches"]
            if isinstance(matches_raw, dict) and "matches" in matches_raw
            else matches_raw)
    if not isinstance(rows, list):
        return played
    for m in rows:
        h = name_of(pick(m, FIELD_MAP["home"]))
        a = name_of(pick(m, FIELD_MAP["away"]))
        hg = pick(m, FIELD_MAP["hg"])
        ag = pick(m, FIELD_MAP["ag"])
        status = pick(m, FIELD_MAP["status"])
        done = ((hg is not None and ag is not None)
                or str(status).upper() in ("FINISHED", "FT", "COMPLETE", "PLAYED")
                or status is True)
        if h and a and done and hg is not None and ag is not None:
            played.append((h, a, int(hg), int(ag)))
    return played


# ── Engine ──────────────────────────────────────────────────────────────────
class Simulator:
    def __init__(self, groups, played, elo, rng):
        self.groups = groups
        self.played = played
        self.elo = elo
        self.rng = rng
        self.all_teams = [t for g in groups.values() for t in g]
        self._unknown_warned = set()

    def get_elo(self, team):
        if team in self.elo:
            return self.elo[team]
        if team not in self._unknown_warned:
            log(f"WARN no Elo for '{team}', defaulting to 1700.")
            self._unknown_warned.add(team)
        return 1700

    def win_exp(self, a, b):
        return 1.0 / (1.0 + 10 ** ((self.get_elo(b) - self.get_elo(a)) / ELO_DIV))

    def lambdas(self, a, b, neutral=True):
        ea = self.get_elo(a) + (HOME_ADV if (not neutral and a in HOSTS) else 0)
        eb = self.get_elo(b) + (HOME_ADV if (not neutral and b in HOSTS) else 0)
        we = 1.0 / (1.0 + 10 ** ((eb - ea) / ELO_DIV))
        tilt = (we - 0.5) * 2.0
        total = BASE_GOALS * 2.0
        la = total * (0.5 + GOAL_TILT * tilt)
        lb = total * (0.5 - GOAL_TILT * tilt)
        return max(la, 0.15), max(lb, 0.15)

    def played_result(self, a, b):
        for h, aw, hg, ag in self.played:
            if {h, aw} == {a, b}:
                return (hg, ag) if h == a else (ag, hg)
        return None

    def sim_group_match(self, a, b):
        la, lb = self.lambdas(a, b)
        return self.rng.poisson(la), self.rng.poisson(lb)

    def sim_knockout(self, a, b):
        la, lb = self.lambdas(a, b)
        ga, gb = self.rng.poisson(la), self.rng.poisson(lb)
        if ga > gb:
            return a
        if gb > ga:
            return b
        return a if self.rng.random() < self.win_exp(a, b) else b

    def simulate_group(self, teams):
        tbl = {t: {"pts": 0, "gf": 0, "ga": 0} for t in teams}
        for i in range(len(teams)):
            for j in range(i + 1, len(teams)):
                a, b = teams[i], teams[j]
                res = self.played_result(a, b)
                ga, gb = res if res else self.sim_group_match(a, b)
                tbl[a]["gf"] += ga; tbl[a]["ga"] += gb
                tbl[b]["gf"] += gb; tbl[b]["ga"] += ga
                if ga > gb:
                    tbl[a]["pts"] += 3
                elif gb > ga:
                    tbl[b]["pts"] += 3
                else:
                    tbl[a]["pts"] += 1; tbl[b]["pts"] += 1
        ranked = sorted(
            teams,
            key=lambda t: (tbl[t]["pts"], tbl[t]["gf"] - tbl[t]["ga"],
                           tbl[t]["gf"], self.rng.random()),
            reverse=True)
        return ranked, tbl

    def simulate_group_stage(self):
        firsts, seconds, thirds = {}, {}, []
        for g, teams in self.groups.items():
            ranked, tbl = self.simulate_group(teams)
            firsts[g] = ranked[0]
            seconds[g] = ranked[1]
            t = ranked[2]
            thirds.append((t, tbl[t]["pts"], tbl[t]["gf"] - tbl[t]["ga"], tbl[t]["gf"]))
        thirds.sort(key=lambda x: (x[1], x[2], x[3], self.rng.random()), reverse=True)
        best_thirds = [t[0] for t in thirds[:8]]
        qualifiers = set(firsts.values()) | set(seconds.values()) | set(best_thirds)
        return qualifiers

    def simulate_knockout(self, qualifiers):
        teams = list(qualifiers)
        self.rng.shuffle(teams)
        teams = teams[:32]
        while len(teams) < 32:
            teams.append(self.rng.choice(list(qualifiers)))
        reached = {t: "R32" for t in teams}
        current = teams
        for rname in ROUND_ORDER[1:]:
            nxt = []
            for k in range(0, len(current), 2):
                w = self.sim_knockout(current[k], current[k + 1])
                reached[w] = rname
                nxt.append(w)
            current = nxt
            if len(current) == 1:
                break
        return reached

    def run(self, n_sims):
        qualify = defaultdict(int)
        reach = {r: defaultdict(int) for r in ROUND_ORDER}
        for _ in range(n_sims):
            qset = self.simulate_group_stage()
            for t in qset:
                qualify[t] += 1
            reached = self.simulate_knockout(qset)
            for t, r in reached.items():
                for rr in ROUND_ORDER[:ROUND_RANK[r] + 1]:
                    reach[rr][t] += 1
        return qualify, reach


# ── Main ────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="Monte Carlo WC2026 simulator")
    ap.add_argument("--sims", type=int, default=10000, help="number of simulations")
    ap.add_argument("--seed", type=int, default=42, help="RNG seed")
    args = ap.parse_args()

    PROCESSED.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(args.seed)

    scoreboard = safe_load_json(PROCESSED / "scoreboard.json")
    matches_raw = safe_load_json(PROCESSED / "latest_matches.json")

    groups = load_groups(scoreboard)
    played = parse_played(matches_raw)
    log(f"{len(played)} completed matches locked in.")

    elo = dict(FALLBACK_ELO)

    sim = Simulator(groups, played, elo, rng)
    log(f"Running {args.sims:,} simulations (seed={args.seed})...")
    qualify, reach = sim.run(args.sims)
    log("Simulations complete.")

    n = args.sims
    teams_out = []
    for t in sim.all_teams:
        teams_out.append({
            "team": t,
            "elo": sim.get_elo(t),
            "qualify": round(100 * qualify[t] / n, 2),
            "reach_qf": round(100 * reach["QF"][t] / n, 2),
            "reach_sf": round(100 * reach["SF"][t] / n, 2),
            "final": round(100 * reach["Final"][t] / n, 2),
            "champion": round(100 * reach["Champion"][t] / n, 2),
        })
    teams_out.sort(key=lambda x: x["champion"], reverse=True)

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "n_sims": n,
        "matches_played": len(played),
        "seed": args.seed,
        "teams": teams_out,
    }

    with open(OUT_FILE, "w") as f:
        json.dump(payload, f, indent=2)
    log(f"Wrote {OUT_FILE} ({len(teams_out)} teams).")

    # console preview
    log("Top 8 title odds:")
    for r in teams_out[:8]:
        log(f"  {r['team']:<16} {r['champion']:5.1f}%  (QF {r['reach_qf']:.0f}%)")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"FATAL {e}")
        sys.exit(1)
