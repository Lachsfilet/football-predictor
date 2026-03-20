"""
Full data ingestion script.
Fetches from all configured sources.

Usage:
    python scripts/fetch_data.py
    python scripts/fetch_data.py --historical   # also fetch 3 past seasons
"""
import sys
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from loguru import logger
from database.session import init_db
from ingestion.football_data_org import FootballDataOrgConnector
from ingestion.transfermarkt import TransfermarktConnector
from config.settings import settings

def main():
    parser = argparse.ArgumentParser(description="Fetch football data")
    parser.add_argument("--source", choices=["football_data", "transfermarkt"],
                        help="Run only one specific source")
    parser.add_argument("--historical", action="store_true",
                        help="Also fetch historical seasons (last 3 years)")
    parser.add_argument("--seasons", type=int, default=3,
                        help="Number of historical seasons to fetch (default: 3)")
    args = parser.parse_args()

    # Ensure DB is ready
    init_db()
    logger.info(f"Tracking leagues: {settings.TRACKED_LEAGUES}")

    # ── football-data.org ──
    if not args.source or args.source == "football_data":
        logger.info("=== football-data.org ===")
        with FootballDataOrgConnector() as c:
            c.fetch_all()
            if args.historical:
                for code in settings.tracked_leagues_list:
                    logger.info(f"Fetching historical data for {code}")
                    c.fetch_historical(code, seasons=args.seasons)

    # ── Transfermarkt ──
    if not args.source or args.source == "transfermarkt":
        logger.info("=== Transfermarkt ===")
        with TransfermarktConnector() as c:
            c.fetch_all()

    logger.info("=== Data fetch complete ===")


if __name__ == "__main__":
    main()