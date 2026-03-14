"""
Train all prediction models, evaluate, and save the best.

Usage:
    python scripts/train_model.py
"""
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from loguru import logger
from database.session import init_db, db_session
from models.trainer import ModelTrainer


def main():
    init_db()
    logger.info("Starting model training...")

    with db_session() as session:
        trainer = ModelTrainer(session)
        try:
            result = trainer.train()
        except ValueError as e:
            logger.error(str(e))
            logger.info("Make sure you have run:")
            logger.info("  1. python scripts/fetch_data.py --historical")
            logger.info("  2. python scripts/compute_features.py")
            return

    logger.info("=== Training complete ===")
    logger.info(f"Best model: {result['best_model']}")
    logger.info(f"Model version: {result['version']}")
    logger.info(f"Training samples: {result['train_samples']}")
    logger.info("CV Scores:")
    for name, score in result["cv_scores"].items():
        logger.info(f"  {name}: {score:.4f}")
    logger.info("")
    logger.info("Model saved. You can now start the server:")
    logger.info("  python -m uvicorn api.main:app --host 0.0.0.0 --port 8000")


if __name__ == "__main__":
    main()
