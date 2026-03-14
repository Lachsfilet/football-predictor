"""
Compute feature vectors for all finished matches.

Usage:
    python scripts/compute_features.py
    python scripts/compute_features.py --recompute  # recompute even if already done
"""
import sys
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from loguru import logger
from sqlalchemy import select
from database.session import init_db, db_session
from database.models import Match, EngineeredFeature
from features.engineer import FeatureEngineer


def main():
    parser = argparse.ArgumentParser(description="Compute features for all finished matches")
    parser.add_argument("--recompute", action="store_true",
                        help="Recompute features even if they already exist")
    args = parser.parse_args()

    init_db()

    with db_session() as session:
        query = (
            select(Match)
            .where(Match.status == "FINISHED", Match.outcome.isnot(None))
        )
        if not args.recompute:
            query = (
                query
                .outerjoin(EngineeredFeature, Match.id == EngineeredFeature.match_id)
                .where(EngineeredFeature.id.is_(None))
            )

        matches = session.execute(query).scalars().all()
        logger.info(f"Matches to process: {len(matches)}")

        if not matches:
            logger.info("No matches to process. Everything is up to date.")
            return

        engineer = FeatureEngineer(session)
        count = engineer.compute_bulk(matches)
        logger.info(f"Features computed for {count}/{len(matches)} matches")


if __name__ == "__main__":
    main()
