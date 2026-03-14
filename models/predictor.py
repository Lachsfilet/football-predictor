"""
Prediction workflow:
  1. Resolve team names
  2. Find (or create stub) match record
  3. Compute features
  4. Run trained model
  5. Generate explanation
  6. Return structured result
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
import numpy as np
import pandas as pd
from loguru import logger
from sqlalchemy.orm import Session
from sqlalchemy import select, or_, and_

from database.models import (
    Match, Team, EngineeredFeature, ModelPrediction, Competition
)
from features.engineer import FeatureEngineer
from ingestion.team_resolver import TeamNameResolver
from models.trainer import ModelTrainer, FEATURE_COLS, LABEL_NAMES
from models.explainer import PredictionExplainer


class MatchPredictor:
    """End-to-end prediction for a given pair of team names."""

    def __init__(self, session: Session):
        self.session = session
        self.resolver = TeamNameResolver(session)
        self.engineer = FeatureEngineer(session)
        self.explainer = PredictionExplainer()
        self._model = None
        self._feature_cols: List[str] = FEATURE_COLS

    # ──────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────

    def predict(self, home_name: str, away_name: str) -> Dict[str, Any]:
        """
        Main entry point.
        Returns a structured prediction dict.
        """
        # 1. Resolve team names
        home_team = self.resolver.resolve(home_name)
        away_team = self.resolver.resolve(away_name)

        if not home_team:
            return self._error(f"Could not find team: '{home_name}'. "
                               "Make sure data has been ingested.")
        if not away_team:
            return self._error(f"Could not find team: '{away_name}'. "
                               "Make sure data has been ingested.")

        # 2. Find best matching upcoming/recent match
        match = self._find_match(home_team.id, away_team.id)
        if not match:
            match = self._create_stub_match(home_team.id, away_team.id)

        # 3. Compute features
        ef = self.engineer.compute_for_match(match)
        if not ef:
            return self._error("Feature engineering failed — not enough historical data.")
        self.session.commit()

        # 4. Load model
        model = self._get_model()
        if model is None:
            return self._error(
                "No trained model found. Run 'python scripts/train_model.py' first."
            )

        # 5. Build feature vector
        feature_dict = self.engineer.get_feature_dict(match.id)
        X = self._feature_vector(feature_dict)

        # 6. Predict
        probs = model.predict_proba(X)[0]
        pred_idx = int(np.argmax(probs))
        predicted = LABEL_NAMES[pred_idx]
        confidence = float(probs[pred_idx])

        prob_home = float(probs[0])
        prob_draw = float(probs[1])
        prob_away = float(probs[2])

        # 7. Explain
        explanation = self.explainer.explain(model, X, feature_dict, predicted)
        key_factors = self._key_factors(feature_dict, home_team, away_team, predicted)

        # 8. Persist prediction
        self._save_prediction(
            match.id, predicted, prob_home, prob_draw, prob_away,
            confidence, explanation, key_factors,
        )
        self.session.commit()

        return {
            "status": "ok",
            "home_team": home_team.name,
            "away_team": away_team.name,
            "home_team_resolved": home_name != home_team.name,
            "away_team_resolved": away_name != away_team.name,
            "match_date": match.match_date.isoformat() if match.match_date else None,
            "competition": match.competition.name if match.competition else None,
            "predicted_outcome": predicted,
            "probabilities": {
                "home_win": round(prob_home * 100, 1),
                "draw": round(prob_draw * 100, 1),
                "away_win": round(prob_away * 100, 1),
            },
            "confidence": round(confidence * 100, 1),
            "key_factors": key_factors,
            "model_version": ModelTrainer.model_version() or "unknown",
        }

    def predict_batch(self, matches: List[Dict[str, str]]) -> List[Dict[str, Any]]:
        """Predict a list of matches. Each item: {"home": str, "away": str}"""
        return [self.predict(m["home"], m["away"]) for m in matches]

    # ──────────────────────────────────────────
    # Match finding / creation
    # ──────────────────────────────────────────

    def _find_match(self, home_id: int, away_id: int) -> Optional[Match]:
        """Find the most relevant upcoming or recent match between these teams."""
        now = datetime.utcnow()
        window_future = now + timedelta(days=30)
        window_past = now - timedelta(days=7)

        # Prefer upcoming scheduled matches
        match = self.session.execute(
            select(Match)
            .where(
                Match.home_team_id == home_id,
                Match.away_team_id == away_id,
                Match.match_date >= window_past,
                Match.match_date <= window_future,
            )
            .order_by(Match.match_date.asc())
        ).scalars().first()

        if match:
            return match

        # Fall back: most recent finished match
        match = self.session.execute(
            select(Match)
            .where(
                Match.home_team_id == home_id,
                Match.away_team_id == away_id,
                Match.status == "FINISHED",
            )
            .order_by(Match.match_date.desc())
        ).scalars().first()

        return match

    def _create_stub_match(self, home_id: int, away_id: int) -> Match:
        """
        If no match record exists, create a temporary stub so we can compute features.
        This is used when the user asks about a hypothetical/upcoming match
        not yet in the DB.
        """
        # Try to find the competition both teams share
        comp = self._find_shared_competition(home_id, away_id)
        stub = Match(
            home_team_id=home_id,
            away_team_id=away_id,
            match_date=datetime.utcnow() + timedelta(days=7),
            status="SCHEDULED",
            competition_id=comp.id if comp else None,
            season=str(datetime.utcnow().year),
        )
        self.session.add(stub)
        self.session.flush()
        return stub

    def _find_shared_competition(self, home_id: int, away_id: int) -> Optional[Competition]:
        """Find the competition both teams participate in (prefer domestic league)."""
        home_comps = {ct.competition_id for ct in self.session.get(Team, home_id).competition_entries}
        away_comps = {ct.competition_id for ct in self.session.get(Team, away_id).competition_entries}
        shared = home_comps & away_comps
        if not shared:
            return None
        comp = self.session.execute(
            select(Competition)
            .where(Competition.id.in_(shared))
            .order_by(Competition.tier.asc())
        ).scalars().first()
        return comp

    # ──────────────────────────────────────────
    # Feature vector assembly
    # ──────────────────────────────────────────

    def _feature_vector(self, feature_dict: Dict) -> pd.DataFrame:
        row = {col: feature_dict.get(col, np.nan) for col in self._feature_cols}
        return pd.DataFrame([row])

    # ──────────────────────────────────────────
    # Key factors (human-readable)
    # ──────────────────────────────────────────

    def _key_factors(
        self,
        f: Dict,
        home: Team,
        away: Team,
        predicted: str,
    ) -> List[str]:
        factors = []

        def safe(key, default=None):
            v = f.get(key)
            return v if v is not None else default

        hf5 = safe("home_form_last5", 0.5)
        af5 = safe("away_form_last5", 0.5)
        if abs(hf5 - af5) > 0.1:
            better = home.name if hf5 > af5 else away.name
            factors.append(f"{better} has stronger recent form (last 5 matches)")

        h_pos = safe("home_league_position")
        a_pos = safe("away_league_position")
        if h_pos and a_pos and abs(h_pos - a_pos) >= 5:
            better = home.name if h_pos < a_pos else away.name
            factors.append(f"{better} is significantly higher in the table "
                           f"(positions {h_pos} vs {a_pos})")

        h_inj = safe("home_injured_count", 0)
        a_inj = safe("away_injured_count", 0)
        if h_inj != a_inj:
            more = home.name if h_inj > a_inj else away.name
            factors.append(f"{more} has more injury concerns "
                           f"({max(h_inj, a_inj)} active injuries)")

        h_xg = safe("home_xg_avg5")
        a_xg = safe("away_xg_avg5")
        if h_xg and a_xg and abs(h_xg - a_xg) > 0.3:
            better = home.name if h_xg > a_xg else away.name
            factors.append(f"{better} has a better xG average over last 5 matches "
                           f"({max(h_xg, a_xg):.2f} vs {min(h_xg, a_xg):.2f})")

        h_xg_trend = safe("home_xg_trend")
        a_xg_trend = safe("away_xg_trend")
        if h_xg_trend and h_xg_trend > 0.1:
            factors.append(f"{home.name} is in improving xG trend")
        if a_xg_trend and a_xg_trend > 0.1:
            factors.append(f"{away.name} is in improving xG trend")

        h2h_hwr = safe("h2h_home_win_rate")
        if h2h_hwr is not None:
            if h2h_hwr > 0.55:
                factors.append(f"{home.name} has a strong head-to-head record in this fixture")
            elif h2h_hwr < 0.30:
                factors.append(f"{away.name} has historically performed well against {home.name}")

        h_hw = safe("home_home_win_rate")
        if h_hw and h_hw > 0.60:
            factors.append(f"{home.name} has a strong home record ({h_hw*100:.0f}% win rate)")

        a_aw = safe("away_away_win_rate")
        if a_aw and a_aw > 0.45:
            factors.append(f"{away.name} is a strong away side ({a_aw*100:.0f}% away win rate)")

        h_cs = safe("home_clean_sheets_last5", 0)
        if h_cs >= 3:
            factors.append(f"{home.name} has kept {h_cs} clean sheets in last 5 matches")

        if not factors:
            factors.append(f"Prediction based on overall statistical profile and historical data")

        return factors[:6]  # max 6 bullet points

    # ──────────────────────────────────────────
    # Model caching
    # ──────────────────────────────────────────

    def _get_model(self):
        if self._model is None:
            self._model = ModelTrainer.load_model("ensemble")
            if self._model is None:
                # Try individual models
                for name in ["lightgbm", "xgboost", "random_forest", "logistic_regression"]:
                    self._model = ModelTrainer.load_model(name)
                    if self._model:
                        break
        return self._model

    # ──────────────────────────────────────────
    # Prediction persistence
    # ──────────────────────────────────────────

    def _save_prediction(
        self, match_id, predicted, prob_home, prob_draw, prob_away,
        confidence, explanation, key_factors,
    ):
        existing = self.session.execute(
            select(ModelPrediction).where(ModelPrediction.match_id == match_id)
        ).scalar_one_or_none()
        version = ModelTrainer.model_version() or "unknown"
        if existing:
            existing.predicted_outcome = predicted
            existing.prob_home = prob_home
            existing.prob_draw = prob_draw
            existing.prob_away = prob_away
            existing.confidence = confidence
            existing.shap_explanation = explanation
            existing.key_factors = key_factors
            existing.model_version = version
        else:
            self.session.add(ModelPrediction(
                match_id=match_id,
                predicted_outcome=predicted,
                prob_home=prob_home,
                prob_draw=prob_draw,
                prob_away=prob_away,
                confidence=confidence,
                shap_explanation=explanation,
                key_factors=key_factors,
                model_version=version,
            ))

    def _error(self, message: str) -> Dict[str, Any]:
        return {"status": "error", "message": message}
