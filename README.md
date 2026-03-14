# ⚽ Football Match Predictor

A production-grade, locally-run football match prediction system. Enter any two team names and receive AI-powered predictions with probability scores and key factor analysis.

## Features

- **Automatic data collection** from football-data.org (free API), FBref (scraping), and Transfermarkt (scraping)
- **50+ engineered features** including rolling form, xG trends, head-to-head, squad availability, schedule load
- **Multiple ML models**: Logistic Regression, Random Forest, XGBoost, LightGBM + Ensemble
- **SHAP explainability** for human-readable prediction reasoning
- **Team name normalization**: supports English and Hebrew team names
- **Browser UI**: simple interface at `http://localhost:8000`
- **Fully local**: no cloud services, no paid APIs, runs on your machine

---

## Quick Start

### 1. Prerequisites

- Python 3.10+
- A free API key from [football-data.org](https://www.football-data.org/) (takes 1 minute to register)

### 2. Setup

```bash
cd football-predictor
bash setup.sh
```

### 3. Configure

Edit `.env` and add your API key:

```
FOOTBALL_DATA_API_KEY=your_key_here
```

### 4. Fetch Data

```bash
source venv/bin/activate

# Fetch current season + 3 past seasons (needed for training)
python scripts/fetch_data.py --historical

# Optional: only one source
python scripts/fetch_data.py --source football_data
```

### 5. Compute Features

```bash
python scripts/compute_features.py
```

### 6. Train Models

```bash
python scripts/train_model.py
```

### 7. Start Server

```bash
python scripts/run_server.py
```

Open [http://localhost:8000](http://localhost:8000) in your browser.

---

## Usage

### Web Interface

Enter matches in the text box (one per line):

```
Liverpool vs Arsenal
Barcelona - Real Madrid
Bayern Munich vs Dortmund
```

Click **Predict** to receive predictions for all matches at once.

### API (direct)

**Single match:**
```bash
curl -X POST http://localhost:8000/api/predictions/single \
  -H "Content-Type: application/json" \
  -d '{"home": "Liverpool", "away": "Arsenal"}'
```

**Batch:**
```bash
curl -X POST http://localhost:8000/api/predictions/batch \
  -H "Content-Type: application/json" \
  -d '{"matches": [{"home": "Liverpool", "away": "Arsenal"}, {"home": "Barcelona", "away": "Real Madrid"}]}'
```

**API docs:** `http://localhost:8000/docs`

---

## Data Sources

| Source | Data | Cost |
|--------|------|------|
| [football-data.org](https://www.football-data.org/) | Fixtures, results, standings, squads | Free (API key) |
| [FBref](https://fbref.com/) | xG, shots, possession, advanced stats | Free (web scraping) |
| [Transfermarkt](https://www.transfermarkt.com/) | Injuries, suspensions, market values | Free (web scraping) |

---

## Leagues Supported (Default)

| Code | League |
|------|--------|
| PL | Premier League (England) |
| PD | La Liga (Spain) |
| BL1 | Bundesliga (Germany) |
| SA | Serie A (Italy) |
| FL1 | Ligue 1 (France) |

Add more leagues in `.env` via `TRACKED_LEAGUES=PL,PD,BL1,SA,FL1,DED`.

---

## Architecture

```
football-predictor/
├── config/           # Settings (pydantic-settings, .env)
├── database/         # SQLAlchemy models + session management
├── ingestion/        # Data connectors (football-data.org, FBref, Transfermarkt)
├── features/         # Feature engineering pipeline
├── models/           # ML training, prediction, explainability
├── api/              # FastAPI app + REST endpoints
├── static/           # Web UI (HTML/CSS/JS)
└── scripts/          # CLI scripts for setup, data fetch, training
```

**Database:** SQLite by default (no server needed). Switch to PostgreSQL via `DATABASE_URL` in `.env`.

---

## Feature Engineering

Over 50 features computed per match:

- Rolling form (last 5, 10 matches)
- Goals scored/conceded averages
- xG and xGA trends
- Shots, possession, pass accuracy averages
- Home/away specific win rates
- League table position and points per game
- Squad availability (injuries + suspensions)
- Schedule load (days rest, matches last 7 days)
- Head-to-head historical record
- Market value comparison
- Derived differentials (xG diff, form diff, position diff)

---

## Model Performance

After training on 3+ seasons of data (1,000–2,000+ matches), expected accuracy:

- Logistic Regression: ~52–54%
- Random Forest: ~53–55%
- XGBoost: ~54–56%
- LightGBM: ~54–57%
- Ensemble (Voting): ~55–57%

*Football is inherently unpredictable. The model captures statistical patterns but cannot predict every upset.*

---

## Data Updates

The system auto-updates daily at 3 AM UTC. Trigger manually:

- Via UI: System tab → "Sync Data Now"
- Via script: `python scripts/fetch_data.py`
- Via API: `POST /api/data/sync`

---

## Team Name Support

Team names can be typed in English or Hebrew. The resolver handles:

- Exact matches
- Common abbreviations (PSG, Man City, Barca, etc.)
- Hebrew transliterations (ליברפול → Liverpool)
- Fuzzy matching for typos

---

## Troubleshooting

**"No trained model found"** — Run `python scripts/train_model.py`

**"Could not find team"** — The team may not be in the DB yet. Run `python scripts/fetch_data.py`

**"Not enough training data"** — Run `python scripts/fetch_data.py --historical` to fetch past seasons

**Rate limiting** — The scraper automatically waits between requests. FBref and Transfermarkt may be slow.

**API key errors** — Ensure `FOOTBALL_DATA_API_KEY` is set in `.env`
