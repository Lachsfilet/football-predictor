"""
Initialize the database: create all tables.
Run once before first use.
"""
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from loguru import logger
from database.session import init_db, engine
from database.models import Base

if __name__ == "__main__":
    logger.info("Initializing database...")
    init_db()
    logger.info(f"Database created at: {engine.url}")
    logger.info("Tables created successfully.")
    logger.info("")
    logger.info("Next steps:")
    logger.info("  1. Copy .env.example to .env and add your football-data.org API key")
    logger.info("  2. Run: python scripts/fetch_data.py")
    logger.info("  3. Run: python scripts/compute_features.py")
    logger.info("  4. Run: python scripts/train_model.py")
    logger.info("  5. Run: python -m uvicorn api.main:app --host 0.0.0.0 --port 8000")
