"""
Data management API endpoints (status, sync, training).
"""
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session
from sqlalchemy import select, func

from database.session import get_db
from database.models import (
    Match, Team, Competition, SourceSyncLog, EngineeredFeature
)
from models.trainer import ModelTrainer

router = APIRouter(prefix="/api/data", tags=["data"])


# ──────────────────────────────────────────
# Status / overview
# ──────────────────────────────────────────

@router.get("/status")
def system_status(db: Session = Depends(get_db)):
    """Returns current system status: data counts, model info, last sync."""
    team_count = db.execute(select(func.count(Team.id))).scalar()
    match_count = db.execute(select(func.count(Match.id))).scalar()
    finished_count = db.execute(
        select(func.count(Match.id)).where(Match.status == "FINISHED")
    ).scalar()
    feature_count = db.execute(select(func.count(EngineeredFeature.id))).scalar()

    last_sync = db.execute(
        select(SourceSyncLog).order_by(SourceSyncLog.created_at.desc())
    ).scalars().first()

    model_version = ModelTrainer.model_version()

    return {
        "database": {
            "teams": team_count,
            "matches_total": match_count,
            "matches_finished": finished_count,
            "features_computed": feature_count,
        },
        "model": {
            "version": model_version,
            "ready": model_version is not None,
        },
        "last_sync": {
            "source": last_sync.source if last_sync else None,
            "status": last_sync.status if last_sync else None,
            "at": last_sync.created_at.isoformat() if last_sync else None,
        },
    }


@router.get("/teams")
def list_teams(
    search: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """List all teams, optionally filtered by name search."""
    query = select(Team).order_by(Team.name)
    if search:
        query = query.where(Team.name.ilike(f"%{search}%"))
    teams = db.execute(query).scalars().all()
    return [
        {
            "id": t.id,
            "name": t.name,
            "short_name": t.short_name,
            "country": t.country,
        }
        for t in teams
    ]


@router.get("/competitions")
def list_competitions(db: Session = Depends(get_db)):
    comps = db.execute(select(Competition)).scalars().all()
    return [
        {
            "id": c.id,
            "code": c.code,
            "name": c.name,
            "country": c.country,
            "current_season": c.current_season,
        }
        for c in comps
    ]


@router.get("/sync-logs")
def sync_logs(limit: int = 20, db: Session = Depends(get_db)):
    logs = db.execute(
        select(SourceSyncLog)
        .order_by(SourceSyncLog.created_at.desc())
        .limit(limit)
    ).scalars().all()
    return [
        {
            "id": l.id,
            "source": l.source,
            "operation": l.operation,
            "status": l.status,
            "records_fetched": l.records_fetched,
            "records_inserted": l.records_inserted,
            "error": l.error_message,
            "at": l.created_at.isoformat(),
        }
        for l in logs
    ]


# ──────────────────────────────────────────
# Trigger actions (background)
# ──────────────────────────────────────────

@router.post("/sync")
def trigger_sync(background_tasks: BackgroundTasks):
    """Trigger a full data sync in the background."""
    from ingestion.scheduler import run_daily_update
    background_tasks.add_task(run_daily_update)
    return {"status": "started", "message": "Data sync started in background"}


@router.post("/compute-features")
def compute_features(background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """Compute features for all FINISHED matches that don't have features yet."""

    def _run():
        from features.engineer import FeatureEngineer
        from database.session import db_session

        with db_session() as session:
            matches = session.execute(
                select(Match)
                .where(Match.status == "FINISHED", Match.outcome.isnot(None))
                .outerjoin(EngineeredFeature, Match.id == EngineeredFeature.match_id)
                .where(EngineeredFeature.id.is_(None))
            ).scalars().all()

            engineer = FeatureEngineer(session)
            count = engineer.compute_bulk(matches)
            return count

    background_tasks.add_task(_run)
    return {"status": "started", "message": "Feature computation started in background"}


@router.post("/train")
def trigger_training(background_tasks: BackgroundTasks):
    """Trigger model (re)training in the background."""

    def _train():
        from database.session import db_session
        with db_session() as session:
            trainer = ModelTrainer(session)
            result = trainer.train()
        return result

    background_tasks.add_task(_train)
    return {
        "status": "started",
        "message": "Model training started in background. This may take a few minutes.",
    }


@router.get("/model-info")
def model_info():
    """Return current model metadata."""
    import json
    from config.settings import settings

    meta_path = settings.MODELS_DIR / "metadata_latest.json"
    if not meta_path.exists():
        return {"ready": False, "message": "No trained model found"}

    with open(meta_path) as f:
        meta = json.load(f)

    return {"ready": True, **meta}
