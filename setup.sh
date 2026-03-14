#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────
# Football Predictor — First-time setup script
# ──────────────────────────────────────────────────────────────
set -e

echo ""
echo "⚽  Football Match Predictor — Setup"
echo "────────────────────────────────────"

# 1. Python check
python3 --version 2>/dev/null || { echo "ERROR: Python 3 not found"; exit 1; }

# 2. Virtual environment
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi
source venv/bin/activate

# 3. Install dependencies
echo "Installing Python dependencies..."
pip install --upgrade pip -q
pip install -r requirements.txt -q
echo "Dependencies installed."

# 4. Create .env from example if not present
if [ ! -f ".env" ]; then
    cp .env.example .env
    echo ""
    echo "⚠️  Created .env from .env.example"
    echo "   → Add your football-data.org API key to .env"
    echo "   → Register free at: https://www.football-data.org/"
fi

# 5. Initialize database
echo ""
echo "Initializing database..."
python scripts/init_db.py

echo ""
echo "✅ Setup complete!"
echo ""
echo "Next steps:"
echo "  1. Add your API key to .env  (FOOTBALL_DATA_API_KEY=...)"
echo "  2. Run: source venv/bin/activate"
echo "  3. Run: python scripts/fetch_data.py --historical"
echo "  4. Run: python scripts/compute_features.py"
echo "  5. Run: python scripts/train_model.py"
echo "  6. Run: python scripts/run_server.py"
echo "  7. Open: http://localhost:8000"
echo ""
