"""
SHAP-based explainability for match predictions.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Any
import numpy as np
import pandas as pd
from loguru import logger

try:
    import shap
    SHAP_AVAILABLE = True
except ImportError:
    SHAP_AVAILABLE = False


class PredictionExplainer:
    """Generates SHAP-based explanations for predictions."""

    def explain(
        self,
        model,
        X: pd.DataFrame,
        feature_dict: Dict,
        predicted_outcome: str,
    ) -> Optional[List[Dict]]:
        """
        Returns top feature contributions as a list of dicts:
        [{"feature": name, "value": float, "impact": float}, ...]
        """
        if not SHAP_AVAILABLE:
            return self._fallback_explanation(feature_dict)

        try:
            return self._shap_explanation(model, X, predicted_outcome)
        except Exception as e:
            logger.warning(f"SHAP explanation failed: {e}. Using fallback.")
            return self._fallback_explanation(feature_dict)

    def _shap_explanation(
        self, model, X: pd.DataFrame, predicted_outcome: str
    ) -> List[Dict]:
        """Use SHAP to get feature importances for the predicted class."""
        outcome_idx = {"HOME": 0, "DRAW": 1, "AWAY": 2}.get(predicted_outcome, 0)

        # Try TreeExplainer first (works with XGBoost, LGBM, RF)
        try:
            # For Pipeline, extract the final estimator
            estimator = model
            if hasattr(model, "named_steps"):
                estimator = model.named_steps.get("model", model)
                # Transform X through pre-steps
                X_transformed = X.copy()
                for step_name, step in model.named_steps.items():
                    if step_name == "model":
                        break
                    X_transformed = step.transform(X_transformed)
            else:
                X_transformed = X

            explainer = shap.TreeExplainer(estimator)
            shap_vals = explainer.shap_values(X_transformed)

            # Multi-class: shap_vals is list of arrays [n_classes]
            if isinstance(shap_vals, list):
                vals = shap_vals[outcome_idx][0]
            else:
                vals = shap_vals[0]

            feature_names = X.columns.tolist()
            contributions = [
                {"feature": feature_names[i], "impact": float(vals[i])}
                for i in range(len(feature_names))
            ]
            contributions.sort(key=lambda x: abs(x["impact"]), reverse=True)
            return contributions[:10]

        except Exception:
            # Fall back to linear explainer
            explainer = shap.LinearExplainer(model, X)
            shap_vals = explainer.shap_values(X)
            if isinstance(shap_vals, list):
                vals = shap_vals[outcome_idx][0]
            else:
                vals = shap_vals[0]

            feature_names = X.columns.tolist()
            contributions = [
                {"feature": feature_names[i], "impact": float(vals[i])}
                for i in range(len(feature_names))
            ]
            contributions.sort(key=lambda x: abs(x["impact"]), reverse=True)
            return contributions[:10]

    def _fallback_explanation(self, feature_dict: Dict) -> List[Dict]:
        """Simple fallback: rank features by absolute value deviation from neutral."""
        neutral_values = {
            "home_form_last5": 0.5, "away_form_last5": 0.5,
            "home_league_position": 10, "away_league_position": 10,
            "home_xg_avg5": 1.5, "away_xg_avg5": 1.5,
            "xg_diff": 0.0, "form_diff": 0.0,
        }
        contributions = []
        for feat, val in feature_dict.items():
            if val is None:
                continue
            neutral = neutral_values.get(feat, 0)
            try:
                impact = float(val) - float(neutral)
                contributions.append({"feature": feat, "impact": impact})
            except (TypeError, ValueError):
                pass

        contributions.sort(key=lambda x: abs(x["impact"]), reverse=True)
        return contributions[:10]

    @staticmethod
    def format_for_display(explanation: Optional[List[Dict]]) -> List[str]:
        """Convert SHAP explanation to readable strings."""
        if not explanation:
            return []
        lines = []
        for item in explanation[:5]:
            feat = item["feature"].replace("_", " ").title()
            impact = item["impact"]
            direction = "↑ favors home" if impact > 0 else "↓ favors away"
            lines.append(f"{feat}: {direction} (impact: {impact:+.3f})")
        return lines
