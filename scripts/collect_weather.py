"""
collect_weather.py
==================
Fetches temperature, humidity, and wind at each match venue at kickoff.
Uses Open-Meteo historical archive API — free, no API key required.

KEY FIX: Instead of relying on football-data.org's venue field (which is
empty on the free tier), we map each match to its stadium using our own
hardcoded 2026 schedule (match_schedule.py). Matching is done by date +
team names, which we control.

Usage:
    python scripts/collect_weather.py

Why this matters:
    The 2026 WC spans venues from 2,240m altitude (Mexico City) to
    sea-level Miami at 31°C humidity. This data feeds the host-city
    scoring analysis (hierarchical Poisson model) in notebook 01.
"""

import requests, pandas as pd, json
from datetime import datetime, timezone
from pathlib import Path

# ── Venue registry: coordinates + altitude ────────────────────────────────
VENUES = {
    'Mexico City':  {'lat':19.302, 'lon':-99.151,  'alt_m':2240, 'tz':'America/Mexico_City'},
    'Guadalajara':  {'lat':20.646, 'lon':-103.462, 'alt_m':1566, 'tz':'America/Mexico_City'},
    'Monterrey':    {'lat':25.669, 'lon':-100.310, 'alt_m':538,  'tz':'America/Monterrey'},
    'Toronto':      {'lat':43.633, 'lon':-79.419,  'alt_m':76,   'tz':'America/Toronto'},
    'Vancouver':    {'lat':49.277, 'lon':-123.112, 'alt_m':3,    'tz':'America/Vancouver'},
    'Los Angeles':  {'lat':33.953, 'lon':-118.339, 'alt_m':71,   'tz':'America/Los_Angeles'},
    'New York/NJ':  {'lat':40.813, 'lon':-74.074,  'alt_m':9,    'tz':'America/New_York'},
    'Dallas':       {'lat':32.748, 'lon':-97.093,  'alt_m':195,  'tz':'America/Chicago'},
    'Houston':      {'lat':29.685, 'lon':-95.411,  'alt_m':13,   'tz':'America/Chicago'},
    'Philadelphia': {'lat':39.901, 'lon':-75.167,  'alt_m':4,    'tz':'America/New_York'},
    'Seattle':      {'lat':47.595, 'lon':-122.332, 'alt_m':11,   'tz':'America/Los_Angeles'},
    'Miami':        {'lat':25.958, 'lon':-80.239,  'alt_m':2,    'tz':'America/New_York'},
    'Atlanta':      {'lat':33.755, 'lon':-84.401,  'alt_m':294,  'tz':'America/New_York'},
    'Kansas City':  {'lat':39.049, 'lon':-94.484,  'alt_m':265,  'tz':'America/Chicago'},
    'San Francisco':{'lat':37.403, 'lon':-121.970, 'alt_m':15,   'tz':'America/Los_Angeles'},
    'Boston':       {'lat':42.091, 'lon':-71.264,  'alt_m':17,   'tz':'America/New_York'},
}

# ── 2026 match → venue schedule ───────────────────────────────────────────
# Maps each match by (date, team) → city. The API gives us teams + date
# reliably even on free tier; we supply the venue ourselves.
# Format: 'YYYY-MM-DD': { 'TeamName': 'City', ... }
# We index by date + either team appearing, so partial name matches work.
SCHEDULE = {
    '2026-06-11': {'Mexico':'Mexico City','South Africa':'Mexico City',
                   'Korea':'Guadalajara','Czech':'Guadalajara'},
    '2026-06-12': {'Canada':'Toronto','Bosnia':'Toronto',
                   'United States':'Los Angeles','USA':'Los Angeles','Paraguay':'Los Angeles'},
    '2026-06-13': {'Qatar':'San Francisco','Switzerland':'San Francisco',
                   'Brazil':'New York/NJ','Morocco':'New York/NJ',
                   'Haiti':'Boston','Scotland':'Boston'},
    '2026-06-14': {'Australia':'Vancouver','Türkiye':'Vancouver','Turkey':'Vancouver',
                   'Germany':'Houston','Curaçao':'Houston','Curacao':'Houston',
                   'Netherlands':'Dallas','Japan':'Dallas',
                   'Ivory Coast':'Philadelphia',"Côte d'Ivoire":'Philadelphia','Ecuador':'Philadelphia',
                   'Sweden':'Monterrey','Tunisia':'Monterrey'},
    '2026-06-15': {'Spain':'Atlanta','Cape Verde':'Atlanta','Cabo Verde':'Atlanta',
                   'Belgium':'Seattle','Egypt':'Seattle',
                   'Saudi Arabia':'Miami','Uruguay':'Miami',
                   'Iran':'Los Angeles','New Zealand':'Los Angeles'},
    '2026-06-16': {'France':'New York/NJ','Senegal':'New York/NJ',
                   'Iraq':'Boston','Norway':'Boston',
                   'Argentina':'Kansas City','Algeria':'Kansas City'},
    '2026-06-17': {'Austria':'San Francisco','Jordan':'San Francisco',
                   'Portugal':'Houston','DR Congo':'Houston','Congo DR':'Houston',
                   'England':'Dallas','Croatia':'Dallas',
                   'Ghana':'Toronto','Panama':'Toronto',
                   'Uzbekistan':'Mexico City','Colombia':'Mexico City'},
    '2026-06-18': {'Czech':'Atlanta','South Africa':'Atlanta',
                   'Switzerland':'Los Angeles','Bosnia':'Los Angeles',
                   'Canada':'Vancouver','Qatar':'Vancouver',
                   'Mexico':'Guadalajara','Korea':'Guadalajara'},
    '2026-06-19': {'Türkiye':'San Francisco','Turkey':'San Francisco','Paraguay':'San Francisco',
                   'United States':'Seattle','USA':'Seattle','Australia':'Seattle',
                   'Scotland':'Boston','Morocco':'Boston',
                   'Brazil':'Philadelphia','Haiti':'Philadelphia'},
    '2026-06-20': {'Netherlands':'Houston','Sweden':'Houston',
                   'Germany':'Toronto','Ivory Coast':'Toronto',"Côte d'Ivoire":'Toronto',
                   'Ecuador':'Kansas City','Curaçao':'Kansas City','Curacao':'Kansas City'},
    '2026-06-21': {'Tunisia':'Monterrey','Japan':'Monterrey',
                   'Spain':'Atlanta','Saudi Arabia':'Atlanta',
                   'Belgium':'Los Angeles','Iran':'Los Angeles',
                   'Uruguay':'Miami','Cape Verde':'Miami','Cabo Verde':'Miami',
                   'New Zealand':'Vancouver','Egypt':'Vancouver'},
    '2026-06-22': {'Argentina':'Dallas','Austria':'Dallas',
                   'France':'Philadelphia','Iraq':'Philadelphia',
                   'Norway':'New York/NJ','Senegal':'New York/NJ',
                   'Jordan':'San Francisco','Algeria':'San Francisco'},
    '2026-06-23': {'Portugal':'Houston','Uzbekistan':'Houston',
                   'England':'Boston','Ghana':'Boston',
                   'Panama':'Toronto','Croatia':'Toronto',
                   'Colombia':'Guadalajara','DR Congo':'Guadalajara','Congo DR':'Guadalajara'},
    '2026-06-24': {'Switzerland':'Vancouver','Canada':'Vancouver',
                   'Bosnia':'Seattle','Qatar':'Seattle',
                   'Scotland':'Miami','Brazil':'Miami',
                   'Morocco':'Atlanta','Haiti':'Atlanta',
                   'Czech':'Mexico City','Mexico':'Mexico City',
                   'South Africa':'Monterrey','Korea':'Monterrey'},
    '2026-06-25': {'Curaçao':'Philadelphia','Curacao':'Philadelphia','Ivory Coast':'Philadelphia',"Côte d'Ivoire":'Philadelphia',
                   'Ecuador':'New York/NJ','Germany':'New York/NJ',
                   'Japan':'Dallas','Sweden':'Dallas',
                   'Tunisia':'Kansas City','Netherlands':'Kansas City',
                   'Türkiye':'Los Angeles','Turkey':'Los Angeles','United States':'Los Angeles','USA':'Los Angeles',
                   'Paraguay':'San Francisco','Australia':'San Francisco'},
    '2026-06-26': {'Norway':'Boston','France':'Boston',
                   'Senegal':'Toronto','Iraq':'Toronto',
                   'Cape Verde':'Houston','Cabo Verde':'Houston','Saudi Arabia':'Houston',
                   'Uruguay':'Guadalajara','Spain':'Guadalajara',
                   'Egypt':'Seattle','Iran':'Seattle',
                   'New Zealand':'Vancouver','Belgium':'Vancouver'},
    '2026-06-27': {'Panama':'New York/NJ','England':'New York/NJ',
                   'Croatia':'Philadelphia','Ghana':'Philadelphia',
                   'Colombia':'Miami','Portugal':'Miami',
                   'DR Congo':'Atlanta','Congo DR':'Atlanta','Uzbekistan':'Atlanta',
                   'Algeria':'Kansas City','Austria':'Kansas City',
                   'Jordan':'Dallas','Argentina':'Dallas'},
}

PROCESSED_DIR = Path('data/processed')
RAW_DIR       = Path('data/raw/weather')

# ── Resolve venue from schedule ────────────────────────────────────────────
def _venue_on_day(day, home_team, away_team):
    for team in [home_team, away_team]:
        if not team:
            continue
        for sched_team, city in day.items():
            if sched_team.lower() in team.lower() or team.lower() in sched_team.lower():
                return city
    return None

def resolve_venue(date_str, home_team, away_team):
    """Find the city for a match using our schedule (date + team match).
    API kickoff dates can be +/- 1 day off our schedule, so check neighbours too."""
    from datetime import datetime, timedelta
    city = _venue_on_day(SCHEDULE.get(date_str, {}), home_team, away_team)
    if city:
        return city
    try:
        d = datetime.strptime(date_str, '%Y-%m-%d')
    except Exception:
        return None
    for delta in (-1, 1):
        alt = (d + timedelta(days=delta)).strftime('%Y-%m-%d')
        city = _venue_on_day(SCHEDULE.get(alt, {}), home_team, away_team)
        if city:
            return city
    return None

# ── Weather fetch ─────────────────────────────────────────────────────────
def fetch_weather(city, date_str, kickoff_hour_local=18):
    """Fetch hourly weather at venue, return conditions at kickoff hour."""
    v = VENUES[city]
    url = 'https://archive-api.open-meteo.com/v1/archive'
    params = {
        'latitude':  v['lat'], 'longitude': v['lon'],
        'start_date': date_str, 'end_date': date_str,
        'hourly': 'temperature_2m,relativehumidity_2m,windspeed_10m,apparent_temperature',
        'timezone': v['tz'],
        'temperature_unit': 'celsius', 'windspeed_unit': 'kmh',
    }
    r = requests.get(url, params=params, timeout=15)
    r.raise_for_status()
    h = r.json()['hourly']
    idx = min(kickoff_hour_local, len(h['temperature_2m'])-1)

    temp_c   = h['temperature_2m'][idx]
    humidity = h['relativehumidity_2m'][idx]
    wind_kph = h['windspeed_10m'][idx]
    feels_c  = h['apparent_temperature'][idx]

    # WBGT approximation
    wbgt = round(0.735*temp_c + 0.0374*humidity + 0.00292*temp_c*humidity
                 + 7.619 - 0.0557, 1)
    if wbgt < 22:    heat_cat = 'Low'
    elif wbgt < 25:  heat_cat = 'Moderate'
    elif wbgt < 28:  heat_cat = 'High'
    else:            heat_cat = 'Very High'

    return {
        'city': city, 'date': date_str, 'altitude_m': v['alt_m'],
        'temp_c': temp_c, 'feels_like_c': feels_c, 'humidity_pct': humidity,
        'wind_kph': wind_kph, 'wbgt_approx': wbgt, 'heat_category': heat_cat,
        'collected_at': datetime.now(timezone.utc).isoformat(),
    }

# ── Build weather log ──────────────────────────────────────────────────────
def build_weather_log():
    matches_path = PROCESSED_DIR / 'latest_matches.json'
    weather_path = PROCESSED_DIR / 'venue_weather.csv'

    if not matches_path.exists():
        print("No matches data found. Run collect_matches.py first.")
        return

    matches = pd.read_json(matches_path)
    finished = matches[matches['status'] == 'FINISHED'].copy()

    if weather_path.exists():
        existing = pd.read_csv(weather_path)
        done_ids = set(existing['match_id'])
    else:
        existing = pd.DataFrame()
        done_ids = set()

    new_rows = []
    skipped = 0
    for _, m in finished.iterrows():
        if m['match_id'] in done_ids:
            continue

        # Resolve venue from OUR schedule, not the API
        # Force date to string (pandas may parse it as Timestamp)
        date_str = str(m['date'])[:10]
        city = resolve_venue(date_str, m.get('home_team',''), m.get('away_team',''))

        if not city or city not in VENUES:
            skipped += 1
            continue

        print(f"  {date_str} {m['home_team']} vs {m['away_team']} @ {city}")
        try:
            row = fetch_weather(city, date_str)
            row['match_id']    = m['match_id']
            row['home_team']   = m['home_team']
            row['away_team']   = m['away_team']
            row['total_goals'] = int((m['home_score'] or 0) + (m['away_score'] or 0))
            new_rows.append(row)
        except Exception as e:
            print(f"    Warning: weather fetch failed for match {m['match_id']}: {e}")

    if new_rows:
        new_df = pd.DataFrame(new_rows)
        combined = pd.concat([existing, new_df], ignore_index=True) if not existing.empty else new_df
        combined.to_csv(weather_path, index=False)
        print(f"\n✓ Added weather for {len(new_rows)} matches")
        print(f"  Total in log: {len(combined)}")
        if skipped:
            print(f"  Skipped {skipped} (date not yet in schedule — update SCHEDULE for knockout rounds)")
    else:
        print("No new matches to add weather for.")
        if skipped:
            print(f"  ({skipped} matches skipped — dates not in schedule lookup)")

# ── Main ───────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    print("Collecting venue weather data...")
    print("Source: Open-Meteo archive (free) · Venues resolved from 2026 schedule\n")
    build_weather_log()
    print("\nDone. Weather log → data/processed/venue_weather.csv")
    print("Feeds the scoring analysis in notebooks/01_venue_scoring.ipynb")
