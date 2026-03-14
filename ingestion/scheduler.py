"""
APScheduler-based background scheduler for daily data updates.
"""
from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from loguru import logger

from config.settings import settings
from ingestion.football_data_org import FootballDataOrgConnector
from ingestion.fbref import FBrefConnector
from ingestion.transfermarkt import TransfermarktConnector


def run_daily_update():
    """Full daily data refresh pipeline."""
    logger.info("=== Daily data update started ===")
    start = datetime.utcnow()

    # 1. Primary: football-data.org (fixtures, results, standings, squads)
    try:
        with FootballDataOrgConnector() as connector:
            connector.fetch_all()
        logger.info("football-data.org sync complete")
    except Exception as e:
        logger.error(f"football-data.org sync failed: {e}")

    # 2. FBref: advanced stats (xG, shots, possession)
    try:
        with FBrefConnector() as connector:
            connector.fetch_all()
        logger.info("FBref sync complete")
    except Exception as e:
        logger.error(f"FBref sync failed: {e}")

    # 3. Transfermarkt: injuries, suspensions, market values
    try:
        with TransfermarktConnector() as connector:
            connector.fetch_all()
        logger.info("Transfermarkt sync complete")
    except Exception as e:
        logger.error(f"Transfermarkt sync failed: {e}")

    elapsed = (datetime.utcnow() - start).total_seconds()
    logger.info(f"=== Daily update complete in {elapsed:.1f}s ===")


def create_scheduler() -> BackgroundScheduler:
    """Build and configure the APScheduler instance."""
    scheduler = BackgroundScheduler(timezone="UTC")

    # Parse cron expression from settings (default: 3 AM daily)
    cron_parts = settings.UPDATE_SCHEDULE.split()
    if len(cron_parts) == 5:
        minute, hour, day, month, day_of_week = cron_parts
    else:
        minute, hour, day, month, day_of_week = "0", "3", "*", "*", "*"

    scheduler.add_job(
        run_daily_update,
        CronTrigger(
            minute=minute,
            hour=hour,
            day=day,
            month=month,
            day_of_week=day_of_week,
            timezone="UTC",
        ),
        id="daily_update",
        name="Daily Football Data Update",
        replace_existing=True,
    )

    logger.info(
        f"Scheduler configured: runs at {hour}:{minute} UTC daily"
    )
    return scheduler
