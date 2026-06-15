"""
collect_weather.py
==================
Fetches temperature, humidity, and wind at each match venue at kickoff.
Uses Open-Meteo historical archive API — free, no API key required.
Calculates an approximate WBGT (heat stress index) for each match.

Usage:
    python scripts/collect_weather.py

Runs daily. Retroactively fills in weather for any completed matches
that don't yet have weather data logged.

Why this matters:
    The 2026 WC spans venues from 2,240m altitude (Mexico City) to
    sea-level Miami at 31°C humidity. This data feeds the host-city
    scoring analysis (hierarchical Poisson model) in notebook 01.
"""

import requests, pandas as pd, json
from datetime import datetime, timezone
from pathlib import Path

# ── Venue registry ────────────────────────────────────────────────────────
# lat/lon used to fetch weather; alt_m used in Poisson model
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

PROCESSED_DIR = Path('data/processed')
RAW_DIR       = Path('data/raw/weather')

# ── Weather fetch ─────────────────────────────────────────────────────────
def fetch_weather(city, date_str, kickoff_hour_local):
    """
    Fetch hourly weather for a given venue and date.
    Returns dict with temp, humidity, wind, and WBGT at kickoff hour.

    Args:
        city:               key from VENUES dict
        date_str:           'YYYY-MM-DD'
        kickoff_hour_local: integer 0-23 (local time at venue)
    """
    v = VENUES[city]
    url = 'https://archive-api.open-meteo.com/v1/archive'
    params = {
        'latitude':  v['lat'],
        'longitude': v['lon'],
        'start_date': date_str,
        'end_date':   date_str,
        'hourly': 'temperature_2m,relativehumidity_2m,windspeed_10m,apparent_temperature',
        'timezone': v['tz'],
        'temperature_unit': 'celsius',
        'windspeed_unit': 'kmh',
    }

    r = requests.get(url, params=params, timeout=15)
    r.raise_for_status()
    data = r.json()

    h = data['hourly']
    idx = kickoff_hour_local  # index into hourly arrays

    temp_c    = h['temperature_2m'][idx]
    humidity  = h['relativehumidity_2m'][idx]
    wind_kph  = h['windspeed_10m'][idx]
    feels_c   = h['apparent_temperature'][idx]

    # WBGT approximation (simplified — adequate for field analysis)
    # Full Liljegren model requires solar radiation data
    wbgt = round(0.735 * temp_c + 0.0374 * humidity
                 + 0.00292 * temp_c * humidity
                 + 7.619 - 0.0557, 1)

    # Heat category (based on US Army/ACSM guidelines)
    if wbgt < 22:       heat_cat = 'Low'
    elif wbgt < 25:     heat_cat = 'Moderate'
    elif wbgt < 28:     heat_cat = 'High'
    else:               heat_cat = 'Very High'

    return {
        'city':             city,
        'date':             date_str,
        'kickoff_hour_local': kickoff_hour_local,
        'altitude_m':       v['alt_m'],
        'temp_c':           temp_c,
        'feels_like_c':     feels_c,
        'humidity_pct':     humidity,
        'wind_kph':         wind_kph,
        'wbgt_approx':      wbgt,
        'heat_category':    heat_cat,
        'collected_at':     datetime.now(timezone.utc).isoformat(),
    }

# ── Match-weather mapping ─────────────────────────────────────────────────
# Manual kickoff-hour lookup (local time) for each city's matches
# Derived from the ET schedule + timezone offsets
# ET → Local: NY/Boston/Philly/Miami/Atlanta = same; Chicago = -1hr; 
#             Mountain = -2hr; Pacific = -3hr; Mexico City = -1hr; Vancouver = -3hr

def et_to_local_hour(time_et_str, city):
    """Convert 'H:MM PM' ET string to local hour integer at venue."""
    from datetime import datetime
    offsets = {
        'New York/NJ': 0, 'Boston': 0, 'Philadelphia': 0,
        'Miami': 0, 'Atlanta': 0, 'Toronto': 0,
        'Dallas': -1, 'Houston': -1, 'Kansas City': -1,
        'Mexico City': -1, 'Monterrey': -1, 'Guadalajara': -1,
        'Los Angeles': -3, 'Seattle': -3, 'San Francisco': -3,
        'Vancouver': -3,
    }
    t = datetime.strptime(time_et_str.strip(), '%I:%M %p')
    local_hour = (t.hour + offsets.get(city, 0)) % 24
    return local_hour

# ── Build weather log for all played matches ──────────────────────────────
def build_weather_log():
    """
    Read latest_matches.json, fetch weather for any FINISHED match
    not already in the weather log.
    """
    matches_path = PROCESSED_DIR / 'latest_matches.json'
    weather_path = PROCESSED_DIR / 'venue_weather.csv'

    if not matches_path.exists():
        print("No matches data found. Run collect_matches.py first.")
        return

    matches = pd.read_json(matches_path)
    finished = matches[matches['status'] == 'FINISHED'].copy()

    # Load existing weather log (to avoid re-fetching)
    if weather_path.exists():
        existing = pd.read_csv(weather_path)
        done_ids = set(existing['match_id'])
    else:
        existing = pd.DataFrame()
        done_ids = set()

    new_rows = []
    for _, m in finished.iterrows():
        if m['match_id'] in done_ids:
            continue
        if not m['venue']:
            continue

        # Extract city from venue string (simple approach)
        city = None
        for c in VENUES:
            if c.lower() in str(m['venue']).lower():
                city = c
                break

        if not city:
            print(f"  Skipping match {m['match_id']} — venue '{m['venue']}' not mapped")
            continue

        print(f"  Fetching weather: {m['date']} {m['home_team']} vs {m['away_team']} @ {city}")

        try:
            # Default kickoff hour (noon local) if we can't determine exact time
            row = fetch_weather(city, m['date'], kickoff_hour_local=12)
            row['match_id'] = m['match_id']
            row['home_team'] = m['home_team']
            row['away_team'] = m['away_team']
            row['total_goals'] = (m['home_score'] or 0) + (m['away_score'] or 0)
            new_rows.append(row)
        except Exception as e:
            print(f"  Warning: Could not fetch weather for match {m['match_id']}: {e}")

    if new_rows:
        new_df = pd.DataFrame(new_rows)
        combined = pd.concat([existing, new_df], ignore_index=True) if not existing.empty else new_df
        combined.to_csv(weather_path, index=False)
        print(f"\n✓ Added weather for {len(new_rows)} new matches")
        print(f"  Total in log: {len(combined)}")
    else:
        print("No new matches to add weather for.")

# ── Main ──────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

    print("Collecting venue weather data...")
    print("Source: Open-Meteo archive (free, no key required)\n")

    build_weather_log()

    print("\nDone. Weather log saved to data/processed/venue_weather.csv")
    print("This feeds the scoring distribution analysis in notebooks/01_venue_scoring.ipynb")
