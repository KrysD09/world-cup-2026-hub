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

import os, json, time, requests, pandas as pd
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
VENUE_CACHE   = PROCESSED_DIR / 'venues_by_match.json'   # match_id -> venue, persisted

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

def load_venue_cache():
    """Load previously-fetched venues so we never re-fetch the same match."""
    if VENUE_CACHE.exists():
        try:
            return json.loads(VENUE_CACHE.read_text())
        except Exception:
            return {}
    return {}

def save_venue_cache(cache):
    VENUE_CACHE.write_text(json.dumps(cache, indent=2))

def enrich_venues(raw_matches, cache, max_fetch=8, throttle=7.0):
    """
    The list endpoint does NOT return venue (v4 folds 'deep' fields out of
    list views). Venue is only available on the single-match resource
    /matches/{id}. So for FINISHED matches we don't already have cached,
    fetch them individually, gently, to respect the 10 req/min free tier.

    max_fetch caps how many NEW matches we look up per run, so a backlog
    gets filled in over a few runs rather than blowing the rate limit.
    """
    finished = [m for m in raw_matches
                if m.get('status') == 'FINISHED' and str(m['id']) not in cache]
    if not finished:
        print("Venues: all finished matches already cached.")
        return cache

    to_fetch = finished[:max_fetch]
    print(f"Venues: fetching {len(to_fetch)} new match(es) "
          f"({len(finished)-len(to_fetch)} will fill in on later runs)...")
    for m in to_fetch:
        mid = m['id']
        try:
            detail = api_get(f'matches/{mid}')
            cache[str(mid)] = detail.get('venue')   # may be None if API lacks it
            print(f"  {mid}: {cache[str(mid)] or 'no venue listed'}")
        except Exception as e:
            print(f"  {mid}: fetch failed ({e})")
        time.sleep(throttle)   # stay under 10 req/min
    save_venue_cache(cache)
    return cache

# ── Processing ────────────────────────────────────────────────────────────
def parse_matches(raw_matches, venue_cache=None):
    """Convert raw API response to clean DataFrame."""
    records = []
    venue_cache = venue_cache or {}
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
            'venue':       venue_cache.get(str(m['id'])) or m.get('venue'),
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

    venue_cache  = load_venue_cache()
    venue_cache  = enrich_venues(raw_matches, venue_cache)

    matches_df   = parse_matches(raw_matches, venue_cache)
    standings_df = parse_standings(raw_standings)

    save_all(matches_df, standings_df)
    print("\nDone. Data saved to data/processed/")
