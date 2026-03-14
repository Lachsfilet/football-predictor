"""
Prediction API endpoints.
"""
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from database.session import get_db
from models.predictor import MatchPredictor

router = APIRouter(prefix="/api/predictions", tags=["predictions"])


# ──────────────────────────────────────────
# Request / Response schemas
# ──────────────────────────────────────────

class SingleMatchRequest(BaseModel):
    home: str
    away: str

    @field_validator("home", "away")
    @classmethod
    def not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Team name cannot be empty")
        return v.strip()


class BatchMatchRequest(BaseModel):
    matches: List[SingleMatchRequest]

    @field_validator("matches")
    @classmethod
    def not_empty_list(cls, v):
        if not v:
            raise ValueError("Must provide at least one match")
        if len(v) > 20:
            raise ValueError("Maximum 20 matches per request")
        return v


class MatchTextRequest(BaseModel):
    """
    Accepts free-form text like:
      Liverpool vs Arsenal
      Barcelona - Real Madrid
      Bayern Munich : Dortmund
    """
    text: str

    def parse_matches(self) -> List[SingleMatchRequest]:
        matches = []
        for line in self.text.strip().splitlines():
            line = line.strip()
            if not line:
                continue
            for sep in [" vs ", " VS ", " v ", " V ", " - ", " : "]:
                if sep in line:
                    parts = line.split(sep, 1)
                    home = parts[0].strip()
                    away = parts[1].strip()
                    if home and away:
                        matches.append(SingleMatchRequest(home=home, away=away))
                    break
        return matches


# ──────────────────────────────────────────
# Endpoints
# ──────────────────────────────────────────

@router.post("/single")
def predict_single(req: SingleMatchRequest, db: Session = Depends(get_db)):
    """Predict outcome for a single match."""
    predictor = MatchPredictor(db)
    result = predictor.predict(req.home, req.away)
    if result.get("status") == "error":
        raise HTTPException(status_code=400, detail=result["message"])
    return result


@router.post("/batch")
def predict_batch(req: BatchMatchRequest, db: Session = Depends(get_db)):
    """Predict outcomes for multiple matches."""
    predictor = MatchPredictor(db)
    results = []
    for match in req.matches:
        result = predictor.predict(match.home, match.away)
        results.append(result)
    return {"predictions": results}


@router.post("/text")
def predict_from_text(req: MatchTextRequest, db: Session = Depends(get_db)):
    """
    Parse free-form text (one match per line) and predict all.
    Accepts: 'Team A vs Team B', 'Team A - Team B', etc.
    """
    matches = req.parse_matches()
    if not matches:
        raise HTTPException(
            status_code=400,
            detail=(
                "Could not parse any matches. "
                "Use format: 'Team A vs Team B' (one per line)"
            ),
        )

    predictor = MatchPredictor(db)
    results = []
    for m in matches:
        result = predictor.predict(m.home, m.away)
        results.append(result)

    return {
        "parsed_matches": len(matches),
        "predictions": results,
    }
