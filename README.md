# 2026 FIFA World Cup Analytics Hub
**The Beautiful Data · Signal & Structure by Krystie Dickson**

A live data pipeline collecting match results, venue weather, and performance metrics for the 2026 FIFA World Cup — powering the analytics behind The Beautiful Data series on Substack.

---

## What this repo does

| Script | What it collects | When to run |
|--------|-----------------|-------------|
| `collect_matches.py` | Match results, scores, status | Daily during tournament |
| `collect_weather.py` | Temp, humidity, WBGT at each venue at kickoff | Daily (retroactive) |
| `update_scoreboard.py` | Confederation points, wins, performance vs expected | After each matchday |

GitHub Actions automates all three daily. You can also run any script manually.

---

## Setup (do this once, takes ~15 minutes)

### Step 1 — Clone the repo
```bash
git clone https://github.com/YOUR_USERNAME/world-cup-2026-hub.git
cd world-cup-2026-hub
```

### Step 2 — Install Python dependencies
```bash
pip install -r requirements.txt
```

### Step 3 — Get your free football-data.org API key
1. Go to https://www.football-data.org/client/register
2. Register (free, instant)
3. Copy your API key from the dashboard

### Step 4 — Create your .env file
```bash
cp .env.example .env
# Then open .env and paste your API key
```

### Step 5 — Download historical data (1930–2022)
1. Go to https://www.kaggle.com/datasets/abecklas/fifa-world-cup
2. Download `WorldCupMatches.csv` and `WorldCups.csv`
3. Place both files in `data/static/`

### Step 6 — Run scripts manually to verify
```bash
python scripts/collect_matches.py
python scripts/collect_weather.py
python scripts/update_scoreboard.py
```

### Step 7 — Enable GitHub Actions automation
1. In your GitHub repo: Settings → Secrets → Actions
2. Add secret: `FOOTBALL_DATA_API_KEY` = your API key
3. The workflow runs automatically at 10am ET every day

---

## Repo structure

```
world-cup-2026-hub/
├── scripts/
│   ├── collect_matches.py      # Match results from football-data.org
│   ├── collect_weather.py      # Venue weather from Open-Meteo
│   └── update_scoreboard.py    # Confederation performance tracker
├── data/
│   ├── raw/
│   │   ├── matches/            # Daily match CSVs (timestamped)
│   │   └── weather/            # Daily weather CSVs (timestamped)
│   ├── processed/
│   │   ├── latest_matches.json # Clean match data (updated daily)
│   │   ├── scoreboard.json     # Confederation scoreboard
│   │   └── venue_weather.csv   # Cumulative venue weather log
│   └── static/
│       ├── venues.csv          # Venue info: altitude, coordinates, capacity
│       ├── WorldCupMatches.csv # Historical 1930-2022 (from Kaggle)
│       └── WorldCups.csv       # Tournament-level historical data
├── notebooks/
│   ├── 01_venue_scoring.ipynb          # Scoring by host city (Poisson model)
│   ├── 02_confederation_performance.ipynb  # CONMEBOL vs UEFA cycles
│   ├── 03_brazil_tracker.ipynb         # Brazil match-by-match analysis
│   └── 04_host_country_paper.ipynb     # Academic: time-varying HA (Kalman filter)
├── .github/
│   └── workflows/
│       └── daily_update.yml    # Automated daily data collection
├── .env.example                # Copy to .env and add your API key
├── .gitignore
├── requirements.txt
└── README.md
```

---

## Data sources

| Source | What | Cost | Key needed |
|--------|------|------|-----------|
| football-data.org | Live match results, standings | Free tier | Yes (free) |
| Open-Meteo | Venue weather at kickoff | Free | No |
| Kaggle (abecklas) | Historical WC data 1930–2022 | Free | Kaggle account |
| eloratings.net | National team Elo ratings | Free | No (scrape) |
| FBref.com | xG, advanced stats | Free | No (scrape) |

---

## Analyses powering The Beautiful Data

**Live (updating throughout tournament)**
- Confederation scoreboard: actual vs expected points by confederation
- Venue scoring model: do altitude/heat/humidity predict goals?
- Brazil tracker: xG vs actual, performance trend

**Academic papers (post-tournament)**
- Host-country advantage decay: DLM/Kalman filter on 1930–2026 data
- CONMEBOL vs UEFA dominance cycles: regime-switching model
- Target journal: Journal of Quantitative Analysis in Sports (JQAS)

---

## Links
- Substack: https://signalandstructurehq.substack.com
- The Beautiful Data section: https://signalandstructurehq.substack.com/s/the-beautiful-data
