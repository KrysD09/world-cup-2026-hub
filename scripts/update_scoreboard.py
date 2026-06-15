"""
update_scoreboard.py
====================
Builds the confederation performance scoreboard from match results.
Compares actual points to Elo-based expected points.
Outputs scoreboard.json used by The Beautiful Data dashboard.

Usage:
    python scripts/update_scoreboard.py

Runs daily. Reads latest_matches.json and latest_standings.json.
"""

import json, pandas as pd
from datetime import datetime, timezone
from pathlib import Path

PROCESSED_DIR = Path('data/processed')

# ── Pre-tournament Elo ratings (from eloratings.net, Dec 2025) ────────────
# Used to calculate "expected" group stage points per team
ELO = {
    # S tier ~1900+
    'France':1996,'England':1989,'Spain':1988,'Brazil':1970,'Argentina':1963,
    'Portugal':1954,'Germany':1939,'Netherlands':1922,
    # A tier 1800-1900
    'Belgium':1881,'Uruguay':1868,'Croatia':1858,'Colombia':1841,
    'Morocco':1835,'Japan':1822,'Mexico':1810,'USA':1808,
    'Austria':1805,'Switzerland':1800,
    # B tier 1700-1800
    'Ecuador':1795,'Australia':1793,'Senegal':1790,'Norway':1785,
    'Canada':1782,'South Korea':1778,'Turkey':1775,'Denmark':1770,
    'Sweden':1765,'Ivory Coast':1762,'Serbia':1758,'Algeria':1750,
    'Iran':1742,'Ghana':1738,'Tunisia':1735,'Egypt':1732,
    # C tier below 1700
    'Scotland':1695,'Saudi Arabia':1688,'Paraguay':1685,'Iraq':1672,
    'Qatar':1660,'Bosnia and Herzegovina':1655,'Czech Republic':1650,
    'Jordan':1640,'New Zealand':1635,'Uzbekistan':1630,'Panama':1620,
    'Cape Verde':1612,'DR Congo':1608,'South Africa':1598,
    'Haiti':1560,'Curaçao':1545,'Cabo Verde':1612,
}

# Expected points formula: win probability from Elo difference
# P(win) = 1 / (1 + 10^(-EloD/400))
def elo_win_prob(elo_a, elo_b):
    return 1 / (1 + 10**((elo_b - elo_a) / 400))

def expected_group_points(team, group_opponents):
    """Estimate expected points for a team across 3 group games."""
    if team not in ELO:
        return 4.5  # fallback: league average
    total = 0
    for opp in group_opponents:
        opp_elo = ELO.get(opp, 1700)
        wp = elo_win_prob(ELO[team], opp_elo)
        dp = 2 * wp * (1 - wp)  # rough draw probability
        total += wp * 3 + dp * 1
    return round(total, 1)

CONFEDERATION = {
    'France':'UEFA','England':'UEFA','Spain':'UEFA','Germany':'UEFA',
    'Netherlands':'UEFA','Belgium':'UEFA','Portugal':'UEFA','Austria':'UEFA',
    'Norway':'UEFA','Sweden':'UEFA','Switzerland':'UEFA','Croatia':'UEFA',
    'Czech Republic':'UEFA','Bosnia and Herzegovina':'UEFA','Scotland':'UEFA',
    'Turkey':'UEFA',
    'Brazil':'CONMEBOL','Argentina':'CONMEBOL','Colombia':'CONMEBOL',
    'Uruguay':'CONMEBOL','Ecuador':'CONMEBOL','Paraguay':'CONMEBOL',
    'Morocco':'CAF','Senegal':'CAF','Ivory Coast':'CAF','Ghana':'CAF',
    'Egypt':'CAF','Algeria':'CAF','Tunisia':'CAF','DR Congo':'CAF',
    'Cape Verde':'CAF','Cabo Verde':'CAF','South Africa':'CAF',
    'Japan':'AFC','South Korea':'AFC','Australia':'AFC','Saudi Arabia':'AFC',
    'Iran':'AFC','Iraq':'AFC','Qatar':'AFC','Jordan':'AFC','Uzbekistan':'AFC',
    'Mexico':'CONCACAF','USA':'CONCACAF','Canada':'CONCACAF',
    'Panama':'CONCACAF','Haiti':'CONCACAF','Curaçao':'CONCACAF',
    'New Zealand':'OFC',
}

def build_scoreboard():
    """Build confederation-level performance scoreboard."""

    matches_path   = PROCESSED_DIR / 'latest_matches.json'
    standings_path = PROCESSED_DIR / 'latest_standings.json'

    if not matches_path.exists():
        print("No match data. Run collect_matches.py first.")
        return

    matches   = pd.read_json(matches_path)
    finished  = matches[matches['status'] == 'FINISHED']

    # Build team-level stats from standings if available
    team_stats = {}
    if standings_path.exists():
        standings = pd.read_json(standings_path)
        for _, row in standings.iterrows():
            team_stats[row['team']] = {
                'group':    row['group'],
                'played':   row['played'],
                'won':      row['won'],
                'drawn':    row['drawn'],
                'lost':     row['lost'],
                'gf':       row['goals_for'],
                'ga':       row['goals_against'],
                'points':   row['points'],
                'conf':     CONFEDERATION.get(row['team'], 'Unknown'),
            }

    # Confederation aggregates
    conf_data = {}
    for conf in ['UEFA','CONMEBOL','CAF','AFC','CONCACAF','OFC']:
        teams = [t for t, c in CONFEDERATION.items() if c == conf]
        pts = sum(team_stats.get(t, {}).get('points', 0) for t in teams)
        wins = sum(team_stats.get(t, {}).get('won', 0) for t in teams)
        played = sum(team_stats.get(t, {}).get('played', 0) for t in teams)
        gf = sum(team_stats.get(t, {}).get('gf', 0) for t in teams)
        ga = sum(team_stats.get(t, {}).get('ga', 0) for t in teams)
        conf_data[conf] = {
            'teams':  len(teams),
            'played': played,
            'wins':   wins,
            'points': pts,
            'gf':     gf,
            'ga':     ga,
            'gd':     gf - ga,
        }

    scoreboard = {
        'last_updated': datetime.now(timezone.utc).isoformat(),
        'matches_played': int(len(finished)),
        'total_matches':  int(len(matches)),
        'total_goals':    int(
            finished['home_score'].fillna(0).sum() +
            finished['away_score'].fillna(0).sum()
        ),
        'confederations': conf_data,
        'team_standings': team_stats,
    }

    out_path = PROCESSED_DIR / 'scoreboard.json'
    with open(out_path, 'w') as f:
        json.dump(scoreboard, f, indent=2)

    print(f"✓ Scoreboard updated: {len(finished)} matches played")
    print("\nConfederation points:")
    for conf, d in sorted(conf_data.items(), key=lambda x: -x[1]['points']):
        bar = '█' * d['points']
        print(f"  {conf:<10} {d['points']:>3} pts  {d['wins']} W  {d['gd']:+d} GD   {bar}")

if __name__ == '__main__':
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    print("Updating confederation scoreboard...")
    build_scoreboard()
    print("\nDone. Saved to data/processed/scoreboard.json")
