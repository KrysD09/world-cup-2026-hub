"""
collect_matches.py
==================
Fetches 2026 FIFA World Cup match results from football-data.org API.
Saves raw timestamped CSV + clean processed JSON updated daily.

Usage:
    python scripts/collect_matches.py

Requires:
    FOOTBALL_DATA_API_KEY in .env file
    Free key at: https://www.football-data.org/client/register
"""

import os, json, requests, pandas as pd
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────
API_KEY  = os.getenv('FOOTBALL_DATA_API_KEY', '')
BASE_URL = 'https://api.football-data.org/v4'
WC_CODE  = 'WC'   # football-data.org code for FIFA World Cup

RAW_DIR       = Path('data/raw/matches')
PROCESSED_DIR = Path('data/processed')
VENUES_FILE   = Path('data/processed/venues.json')   # shared schedule (also used by weather)

# ── Venue resolution ───────────────────────────────────────────────────────
# football-data.org's free tier does NOT return venue for the World Cup, so we
# supply it ourselves from venues.json (the same authoritative schedule the
# weather collector uses). Matching is by date + either team name (partial).
def load_venue_schedule():
    if not VENUES_FILE.exists():
        print(f"  ! {VENUES_FILE} not found — venue/city will be null")
        return {}, {}
    data = json.loads(VENUES_FILE.read_text(encoding='utf-8'))
    return data.get('schedule', {}), data.get('venues', {})

def _match_on_day(day, home_team, away_team):
    for sched_team, city in day.items():
        if sched_team and (sched_team in (home_team or '') or
                           sched_team in (away_team or '')):
            return city
    return None

def resolve_city(schedule, date_str, home_team, away_team):
    """Find the host city for a match using date + (partial) team-name match.
    The API's kickoff date can differ from our schedule by a day (timezone /
    late-night games), so we also check +/- 1 day before giving up."""
    from datetime import datetime, timedelta
    # exact day first
    city = _match_on_day(schedule.get(date_str, {}), home_team, away_team)
    if city:
        return city
    # +/- 1 day fallback
    try:
        d = datetime.strptime(date_str, '%Y-%m-%d')
    except Exception:
        return None
    for delta in (-1, 1):
        alt = (d + timedelta(days=delta)).strftime('%Y-%m-%d')
        city = _match_on_day(schedule.get(alt, {}), home_team, away_team)
        if city:
            return city
    return None

# Confederation lookup (for scoreboard)
CONFEDERATION = {
    # UEFA
    'Czech Republic':'UEFA','Bosnia and Herzegovina':'UEFA','Switzerland':'UEFA',
    'Scotland':'UEFA','Türkiye':'UEFA','Germany':'UEFA','Netherlands':'UEFA',
    'Sweden':'UEFA','Belgium':'UEFA','Spain':'UEFA','France':'UEFA',
    'Norway':'UEFA','Austria':'UEFA','Portugal':'UEFA','England':'UEFA',
    'Croatia':'UEFA',
    # CONMEBOL
    'Brazil':'CONMEBOL','Paraguay':'CONMEBOL','Ecuador':'CONMEBOL',
    'Uruguay':'CONMEBOL','Argentina':'CONMEBOL','Colombia':'CONMEBOL',
    # CAF
    'South Africa':'CAF','Morocco':'CAF','Côte d\'Ivoire':'CAF','Tunisia':'CAF',
    'Egypt':'CAF','Cabo Verde':'CAF','Senegal':'CAF','Algeria':'CAF',
    'DR Congo':'CAF','Ghana':'CAF',
    # AFC
    'Korea Republic':'AFC','Qatar':'AFC','Australia':'AFC','Japan':'AFC',
    'Iran':'AFC','Saudi Arabia':'AFC','Iraq':'AFC','Jordan':'AFC',
    'Uzbekistan':'AFC',
    # CONCACAF
    'Mexico':'CONCACAF','Canada':'CONCACAF','USA':'CONCACAF',
    'Haiti':'CONCACAF','Curaçao':'CONCACAF','Panama':'CONCACAF',
    # OFC
    'New Zealand':'OFC',
}

# ── API helpers ───────────────────────────────────────────────────────────
def api_get(endpoint):
    """Make authenticated GET request to football-data.org."""
    headers = {'X-Auth-Token': API_KEY}
    url = f'{BASE_URL}/{endpoint}'
    r = requests.get(url, headers=headers, timeout=10)
    r.raise_for_status()
    return r.json()

# ── Collection ────────────────────────────────────────────────────────────
def fetch_matches():
    """Fetch all WC 2026 matches from API."""
    print("Fetching matches from football-data.org...")
    data = api_get(f'competitions/{WC_CODE}/matches')
    return data.get('matches', [])

def fetch_standings():
    """Fetch group stage standings."""
    print("Fetching standings...")
    data = api_get(f'competitions/{WC_CODE}/standings')
    return data.get('standings', [])

def parse_matches(raw_matches, schedule=None, venues=None):
    """Convert raw API response to clean DataFrame."""
    records = []
    schedule = schedule or {}
    venues = venues or {}
    for m in raw_matches:
        home = m['homeTeam'].get('name', 'TBD')
        away = m['awayTeam'].get('name', 'TBD')
        home_score = m['score']['fullTime'].get('home')
        away_score = m['score']['fullTime'].get('away')

        # Determine result from home team perspective
        if home_score is not None and away_score is not None:
            if home_score > away_score:   result = 'W'
            elif home_score < away_score: result = 'L'
            else:                         result = 'D'
        else:
            result = None

        records.append({
            'match_id':    m['id'],
            'date':        m['utcDate'][:10],
            'status':      m['status'],           # TIMED / IN_PLAY / FINISHED
            'stage':       m['stage'],            # GROUP_STAGE / ROUND_OF_32 / etc
            'group':       m.get('group'),        # GROUP_A through GROUP_L
            'matchday':    m.get('matchday'),     # 1, 2, 3 for group stage
            'home_team':   home,
            'away_team':   away,
            'home_score':  home_score,
            'away_score':  away_score,
            'home_result': result,
            'home_conf':   CONFEDERATION.get(home, 'Unknown'),
            'away_conf':   CONFEDERATION.get(away, 'Unknown'),
            'city':        resolve_city(schedule, m['utcDate'][:10], home, away),
            'venue':       (venues.get(resolve_city(schedule, m['utcDate'][:10], home, away) or '', {}) or {}).get('stadium'),
            'collected_at': datetime.now(timezone.utc).isoformat(),
        })

    return pd.DataFrame(records)

def parse_standings(raw_standings):
    """Convert group standings to clean DataFrame."""
    records = []
    for s in raw_standings:
        group = s.get('group', '')
        for entry in s.get('table', []):
            team = entry['team']['name']
            records.append({
                'group':           group,
                'position':        entry['position'],
                'team':            team,
                'confederation':   CONFEDERATION.get(team, 'Unknown'),
                'played':          entry['playedGames'],
                'won':             entry['won'],
                'drawn':           entry['draw'],
                'lost':            entry['lost'],
                'goals_for':       entry['goalsFor'],
                'goals_against':   entry['goalsAgainst'],
                'goal_diff':       entry['goalDifference'],
                'points':          entry['points'],
            })
    return pd.DataFrame(records)

# ── Save ──────────────────────────────────────────────────────────────────
def save_all(matches_df, standings_df):
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

    today = datetime.now().strftime('%Y%m%d')

    # Raw timestamped CSVs (keep history)
    matches_df.to_csv(RAW_DIR / f'matches_{today}.csv', index=False)

    # Processed: always-current files (overwrite daily)
    matches_df.to_json(PROCESSED_DIR / 'latest_matches.json',
                       orient='records', indent=2)

    if not standings_df.empty:
        standings_df.to_json(PROCESSED_DIR / 'latest_standings.json',
                             orient='records', indent=2)

    # Summary stats for quick reporting
    finished = matches_df[matches_df.status == 'FINISHED']
    summary = {
        'last_updated': datetime.now(timezone.utc).isoformat(),
        'total_matches': len(matches_df),
        'played': len(finished),
        'remaining': len(matches_df) - len(finished),
        'total_goals': int(finished['home_score'].fillna(0).sum() +
                          finished['away_score'].fillna(0).sum()),
        'goals_per_game': round(
            (finished['home_score'].fillna(0).sum() +
             finished['away_score'].fillna(0).sum()) / max(len(finished), 1), 2
        ),
    }
    with open(PROCESSED_DIR / 'summary.json', 'w') as f:
        json.dump(summary, f, indent=2)

    print(f"\n✓ Saved {len(matches_df)} matches ({len(finished)} played)")
    print(f"  Goals scored: {summary['total_goals']} "
          f"({summary['goals_per_game']} per game)")

# ── Main ──────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    if not API_KEY:
        print("ERROR: FOOTBALL_DATA_API_KEY not set.")
        print("Get your free key at: https://www.football-data.org/client/register")
        print("Then add it to your .env file.")
        raise SystemExit(1)

    raw_matches   = fetch_matches()
    raw_standings = fetch_standings()

    schedule, venues = load_venue_schedule()
    matches_df   = parse_matches(raw_matches, schedule, venues)
    standings_df = parse_standings(raw_standings)

    save_all(matches_df, standings_df)
    print("\nDone. Data saved to data/processed/")
