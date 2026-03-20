"""
Model training pipeline.
Trains Logistic Regression, Random Forest, XGBoost, and LightGBM,
performs cross-validation, selects the best model, and persists it.
"""
from __future__ import annotations

import json
import pickle
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, Tuple

import numpy as np
import pandas as pd
from loguru import logger
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import RandomForestClassifier, VotingClassifier
from sklearn.preprocessing import LabelEncoder, StandardScaler
from sklearn.model_selection import StratifiedKFold, cross_val_score
from sklearn.metrics import accuracy_score, classification_report
from sklearn.pipeline import Pipeline
from sklearn.impute import SimpleImputer
import xgboost as xgb
import lightgbm as lgb
from sqlalchemy import select
from sqlalchemy.orm import Session

from config.settings import settings
from database.models import Match, EngineeredFeature


# Feature columns used for training (must match EngineeredFeature fields)
FEATURE_COLS = [
    "home_form_last5", "home_form_last10",
    "home_goals_scored_avg5", "home_goals_conceded_avg5",
    "home_xg_avg5", "home_xga_avg5",
    "home_shots_avg5", "home_sot_avg5",
    "home_possession_avg5", "home_pass_acc_avg5",
    "home_clean_sheets_last5", "home_failed_to_score_last5",
    "away_form_last5", "away_form_last10",
    "away_goals_scored_avg5", "away_goals_conceded_avg5",
    "away_xg_avg5", "away_xga_avg5",
    "away_shots_avg5", "away_sot_avg5",
    "away_possession_avg5", "away_pass_acc_avg5",
    "away_clean_sheets_last5", "away_failed_to_score_last5",
    "home_home_win_rate", "home_home_goals_avg",
    "away_away_win_rate", "away_away_goals_avg", "away_away_conceded_avg",
    "home_league_position", "away_league_position",
    "home_points_per_game", "away_points_per_game", "position_diff",
    "home_injured_count", "away_injured_count",
    "home_suspended_count", "away_suspended_count",
    "home_squad_market_value", "away_squad_market_value",
    "home_days_since_last_match", "away_days_since_last_match",
    "home_matches_last7", "away_matches_last7",
    "h2h_home_win_rate", "h2h_draw_rate", "h2h_goals_avg",
    "home_xg_trend", "away_xg_trend", "home_xga_trend", "away_xga_trend",
    "xg_diff", "form_diff", "league_pos_diff",
]

TARGET_MAP = {"HOME": 0, "DRAW": 1, "AWAY": 2}
LABEL_NAMES = ["HOME", "DRAW", "AWAY"]


class ModelTrainer:
    """Trains and evaluates multiple classification models."""

    def __init__(self, session: Session):
        self.session = session
        self.models_dir = settings.MODELS_DIR

    # ──────────────────────────────────────────
    # Data loading
    # ──────────────────────────────────────────

    def load_training_data(self) -> Tuple[pd.DataFrame, pd.Series]:
        """
        Join EngineeredFeature with Match outcomes to build training dataset.
        Only includes FINISHED matches with a valid outcome.
        """
        rows = self.session.execute(
            select(EngineeredFeature, Match.outcome)
            .join(Match, EngineeredFeature.match_id == Match.id)
            .where(
                Match.status == "FINISHED",
                Match.outcome.isnot(None),
            )
        ).all()

        if not rows:
            raise ValueError("No training data found. Run data ingestion first.")

        records = []
        targets = []
        for ef, outcome in rows:
            row = {col: getattr(ef, col) for col in FEATURE_COLS}
            records.append(row)
            targets.append(TARGET_MAP.get(outcome, -1))

        df = pd.DataFrame(records)
        y = pd.Series(targets, name="outcome")

        # Drop samples with unknown outcome
        valid = y >= 0
        df = df[valid].reset_index(drop=True)
        y = y[valid].reset_index(drop=True)

        logger.info(f"Training dataset: {len(df)} samples, {df.shape[1]} features")
        logger.info(f"Class distribution: {y.value_counts().to_dict()}")
        return df, y

    # ──────────────────────────────────────────
    # Pipeline builder
    # ──────────────────────────────────────────

    def _build_pipeline(self, estimator) -> Pipeline:
        return Pipeline([
            ("imputer", SimpleImputer(strategy="median")),
            ("scaler", StandardScaler()),
            ("model", estimator),
        ])

    def _build_all_models(self) -> Dict[str, Pipeline]:
        return {
            "logistic_regression": self._build_pipeline(
                LogisticRegression(
                    max_iter=1000, C=1.0,
                    solver="lbfgs", random_state=42,
                )
            ),
            "random_forest": self._build_pipeline(
                RandomForestClassifier(
                    n_estimators=300, max_depth=8, min_samples_leaf=5,
                    class_weight="balanced", random_state=42, n_jobs=-1,
                )
            ),
            "xgboost": Pipeline([
                ("imputer", SimpleImputer(strategy="median")),
                ("model", xgb.XGBClassifier(
                    n_estimators=300, max_depth=5, learning_rate=0.05,
                    subsample=0.8, colsample_bytree=0.8,
                    use_label_encoder=False, eval_metric="mlogloss",
                    random_state=42, n_jobs=-1,
                )),
            ]),
            "lightgbm": Pipeline([
                ("imputer", SimpleImputer(strategy="median")),
                ("model", lgb.LGBMClassifier(
                    n_estimators=300, max_depth=5, learning_rate=0.05,
                    num_leaves=31, subsample=0.8, colsample_bytree=0.8,
                    class_weight="balanced", random_state=42, n_jobs=-1,
                    verbose=-1,
                )),
            ]),
        }

    # ──────────────────────────────────────────
    # Training & evaluation
    # ──────────────────────────────────────────

    def train(self) -> Dict[str, Any]:
        """Full training run. Returns evaluation report."""
        X, y = self.load_training_data()

        if len(X) < 50:
            raise ValueError(
                f"Only {len(X)} training samples found. "
                "Need at least 50 finished matches with features. "
                "Run data ingestion and feature engineering first."
            )

        models = self._build_all_models()
        cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
        results: Dict[str, float] = {}

        for name, pipeline in models.items():
            scores = cross_val_score(pipeline, X, y, cv=cv, scoring="accuracy", n_jobs=-1)
            results[name] = float(scores.mean())
            logger.info(f"[{name}] CV accuracy: {scores.mean():.4f} ± {scores.std():.4f}")

        best_name = max(results, key=results.get)
        logger.info(f"Best model: {best_name} ({results[best_name]:.4f})")

        # Retrain best on full data
        best_pipeline = models[best_name]
        best_pipeline.fit(X, y)

        # Also train an ensemble
        ensemble = VotingClassifier(
            estimators=[(k, v) for k, v in models.items()],
            voting="soft",
        )
        ensemble.fit(X, y)

        # Evaluate on full training set (sanity check)
        train_preds = best_pipeline.predict(X)
        report = classification_report(y, train_preds, target_names=LABEL_NAMES)
        logger.info(f"Training set report:\n{report}")

        # Persist
        version = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        self._save_model(best_pipeline, best_name, version)
        self._save_model(ensemble, "ensemble", version)
        self._save_feature_cols(version)
        self._save_metadata(best_name, results, version)

        return {
            "best_model": best_name,
            "cv_scores": results,
            "version": version,
            "train_samples": len(X),
            "report": report,
        }

    # ──────────────────────────────────────────
    # Persistence
    # ──────────────────────────────────────────

    def _save_model(self, model, name: str, version: str):
        path = self.models_dir / f"{name}_{version}.pkl"
        with open(path, "wb") as f:
            pickle.dump(model, f)
        # Also save as "latest"
        latest_path = self.models_dir / f"{name}_latest.pkl"
        with open(latest_path, "wb") as f:
            pickle.dump(model, f)
        logger.info(f"Model saved: {path}")

    def _save_feature_cols(self, version: str):
        path = self.models_dir / f"feature_cols_{version}.json"
        with open(path, "w") as f:
            json.dump(FEATURE_COLS, f)
        latest = self.models_dir / "feature_cols_latest.json"
        with open(latest, "w") as f:
            json.dump(FEATURE_COLS, f)

    def _save_metadata(self, best_name: str, results: Dict, version: str):
        meta = {
            "version": version,
            "best_model": best_name,
            "cv_scores": results,
            "trained_at": datetime.utcnow().isoformat(),
            "feature_count": len(FEATURE_COLS),
        }
        path = self.models_dir / f"metadata_{version}.json"
        with open(path, "w") as f:
            json.dump(meta, f, indent=2)
        latest = self.models_dir / "metadata_latest.json"
        with open(latest, "w") as f:
            json.dump(meta, f, indent=2)

    # ──────────────────────────────────────────
    # Helpers for loading
    # ──────────────────────────────────────────

    @staticmethod
    def load_model(name: str = "ensemble") -> Optional[Any]:
        path = settings.MODELS_DIR / f"{name}_latest.pkl"
        if not path.exists():
            logger.warning(f"Model not found: {path}")
            return None
        with open(path, "rb") as f:
            return pickle.load(f)

    @staticmethod
    def load_feature_cols() -> list:
        path = settings.MODELS_DIR / "feature_cols_latest.json"
        if not path.exists():
            return FEATURE_COLS
        with open(path) as f:
            return json.load(f)

    @staticmethod
    def model_version() -> Optional[str]:
        path = settings.MODELS_DIR / "metadata_latest.json"
        if not path.exists():
            return None
        with open(path) as f:
            meta = json.load(f)
        return meta.get("version")
