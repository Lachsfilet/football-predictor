"""
Feature engineering: computes the full feature vector for a match.
Features cover rolling form, home/away splits, xG trends,
squad availability, head-to-head, schedule load, and league context.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
import numpy as np
from loguru import logger
from sqlalchemy.orm import Session
from sqlalchemy import select, and_, or_, func

from database.models import (
    Match, Team, Standing, TeamMatchStat, Injury, Suspension,
    HeadToHead, EngineeredFeature, CompetitionTeam
)


class FeatureEngineer:
    """Compute and store EngineeredFeature rows for matches."""

    def __init__(self, session: Session):
        self.session = session

    # ──────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────

    def compute_for_match(self, match: Match) -> Optional[EngineeredFeature]:
        """Compute (or update) the feature vector for a single match."""
        try:
            f = self._build_features(match)
            # Persist
            existing = self.session.execute(
                select(EngineeredFeature).where(EngineeredFeature.match_id == match.id)
            ).scalar_one_or_none()
            if existing:
                for k, v in vars(f).items():
                    if not k.startswith("_"):
                        setattr(existing, k, v)
                existing.computed_at = datetime.utcnow()
                return existing
            else:
                self.session.add(f)
                self.session.flush()
                return f
        except Exception as e:
            logger.error(f"Feature engineering failed for match {match.id}: {e}")
            return None

    def compute_bulk(self, matches: List[Match]) -> int:
        """Compute features for a list of matches. Returns count processed."""
        count = 0
        for m in matches:
            ef = self.compute_for_match(m)
            if ef:
                count += 1
        self.session.commit()
        logger.info(f"Feature engineering complete: {count}/{len(matches)} matches")
        return count

    def get_feature_dict(self, match_id: int) -> Optional[Dict[str, Any]]:
        """Return the stored feature vector as a plain dict for model inference."""
        ef = self.session.execute(
            select(EngineeredFeature).where(EngineeredFeature.match_id == match_id)
        ).scalar_one_or_none()
        if not ef:
            return None
        return {
            c.key: getattr(ef, c.key)
            for c in EngineeredFeature.__table__.columns
            if c.key not in ("id", "match_id", "computed_at")
        }

    # ──────────────────────────────────────────
    # Core builder
    # ──────────────────────────────────────────

    def _build_features(self, match: Match) -> EngineeredFeature:
        ref_date = match.match_date or datetime.utcnow()
        home_id = match.home_team_id
        away_id = match.away_team_id

        # --- Rolling match history ---
        home_hist = self._team_history(home_id, ref_date, n=10)
        away_hist = self._team_history(away_id, ref_date, n=10)

        home_hist_home = self._team_history(home_id, ref_date, n=10, venue="home")
        away_hist_away = self._team_history(away_id, ref_date, n=10, venue="away")

        # --- Form (points / max_points) ---
        home_form5  = self._form(home_hist[:5])
        home_form10 = self._form(home_hist[:10])
        away_form5  = self._form(away_hist[:5])
        away_form10 = self._form(away_hist[:10])

        # --- Goal averages ---
        h_gs5  = self._avg(home_hist[:5], "goals_scored")
        h_gc5  = self._avg(home_hist[:5], "goals_conceded")
        a_gs5  = self._avg(away_hist[:5], "goals_scored")
        a_gc5  = self._avg(away_hist[:5], "goals_conceded")

        # --- xG averages ---
        h_xg5  = self._avg(home_hist[:5], "xg")
        h_xga5 = self._avg(home_hist[:5], "xga")
        a_xg5  = self._avg(away_hist[:5], "xg")
        a_xga5 = self._avg(away_hist[:5], "xga")

        # --- Shot / possession ---
        h_shots5 = self._avg(home_hist[:5], "shots")
        h_sot5   = self._avg(home_hist[:5], "shots_on_target")
        h_poss5  = self._avg(home_hist[:5], "possession")
        h_pacc5  = self._avg(home_hist[:5], "pass_accuracy")
        a_shots5 = self._avg(away_hist[:5], "shots")
        a_sot5   = self._avg(away_hist[:5], "shots_on_target")
        a_poss5  = self._avg(away_hist[:5], "possession")
        a_pacc5  = self._avg(away_hist[:5], "pass_accuracy")

        # --- Clean sheets & blanks ---
        h_cs5  = self._count_where(home_hist[:5], "goals_conceded", 0)
        h_fts5 = self._count_where(home_hist[:5], "goals_scored", 0)
        a_cs5  = self._count_where(away_hist[:5], "goals_conceded", 0)
        a_fts5 = self._count_where(away_hist[:5], "goals_scored", 0)

        # --- Home/Away splits ---
        h_hw_rate = self._win_rate(home_hist_home)
        h_hg_avg  = self._avg(home_hist_home, "goals_scored")
        a_aw_rate = self._win_rate(away_hist_away)
        a_ag_avg  = self._avg(away_hist_away, "goals_scored")
        a_agc_avg = self._avg(away_hist_away, "goals_conceded")

        # --- League standing ---
        h_pos, h_ppg = self._standing(home_id, match.competition_id, match.season)
        a_pos, a_ppg = self._standing(away_id, match.competition_id, match.season)

        # --- Squad availability ---
        h_inj  = self._active_injuries(home_id)
        a_inj  = self._active_injuries(away_id)
        h_susp = self._active_suspensions(home_id)
        a_susp = self._active_suspensions(away_id)
        h_mv   = self._market_value(home_id)
        a_mv   = self._market_value(away_id)

        # --- Schedule load ---
        h_days_rest = self._days_since_last(home_id, ref_date)
        a_days_rest = self._days_since_last(away_id, ref_date)
        h_m7 = self._matches_in_window(home_id, ref_date, days=7)
        a_m7 = self._matches_in_window(away_id, ref_date, days=7)

        # --- Head-to-head ---
        h2h_hwr, h2h_dr, h2h_gavg = self._h2h(home_id, away_id, n=10)

        # --- xG trend (slope) ---
        h_xg_trend  = self._trend([r["xg"]  for r in home_hist[:5] if r["xg"] is not None])
        a_xg_trend  = self._trend([r["xg"]  for r in away_hist[:5] if r["xg"] is not None])
        h_xga_trend = self._trend([r["xga"] for r in home_hist[:5] if r["xga"] is not None])
        a_xga_trend = self._trend([r["xga"] for r in away_hist[:5] if r["xga"] is not None])

        xg_diff  = (h_xg5 or 0) - (a_xg5 or 0)
        form_diff = (home_form5 or 0) - (away_form5 or 0)
        pos_diff  = (h_pos or 20) - (a_pos or 20)

        return EngineeredFeature(
            match_id=match.id,
            # Home rolling
            home_form_last5=home_form5,
            home_form_last10=home_form10,
            home_goals_scored_avg5=h_gs5,
            home_goals_conceded_avg5=h_gc5,
            home_xg_avg5=h_xg5,
            home_xga_avg5=h_xga5,
            home_shots_avg5=h_shots5,
            home_sot_avg5=h_sot5,
            home_possession_avg5=h_poss5,
            home_pass_acc_avg5=h_pacc5,
            home_clean_sheets_last5=h_cs5,
            home_failed_to_score_last5=h_fts5,
            # Away rolling
            away_form_last5=away_form5,
            away_form_last10=away_form10,
            away_goals_scored_avg5=a_gs5,
            away_goals_conceded_avg5=a_gc5,
            away_xg_avg5=a_xg5,
            away_xga_avg5=a_xga5,
            away_shots_avg5=a_shots5,
            away_sot_avg5=a_sot5,
            away_possession_avg5=a_poss5,
            away_pass_acc_avg5=a_pacc5,
            away_clean_sheets_last5=a_cs5,
            away_failed_to_score_last5=a_fts5,
            # Home/away splits
            home_home_win_rate=h_hw_rate,
            home_home_goals_avg=h_hg_avg,
            away_away_win_rate=a_aw_rate,
            away_away_goals_avg=a_ag_avg,
            away_away_conceded_avg=a_agc_avg,
            # League
            home_league_position=h_pos,
            away_league_position=a_pos,
            home_points_per_game=h_ppg,
            away_points_per_game=a_ppg,
            position_diff=pos_diff,
            # Squad
            home_injured_count=h_inj,
            away_injured_count=a_inj,
            home_suspended_count=h_susp,
            away_suspended_count=a_susp,
            home_squad_market_value=h_mv,
            away_squad_market_value=a_mv,
            # Schedule
            home_days_since_last_match=h_days_rest,
            away_days_since_last_match=a_days_rest,
            home_matches_last7=h_m7,
            away_matches_last7=a_m7,
            # H2H
            h2h_home_win_rate=h2h_hwr,
            h2h_draw_rate=h2h_dr,
            h2h_goals_avg=h2h_gavg,
            # xG trends
            home_xg_trend=h_xg_trend,
            away_xg_trend=a_xg_trend,
            home_xga_trend=h_xga_trend,
            away_xga_trend=a_xga_trend,
            # Differentials
            xg_diff=xg_diff,
            form_diff=form_diff,
            league_pos_diff=pos_diff,
        )

    # ──────────────────────────────────────────
    # History queries
    # ──────────────────────────────────────────

    def _team_history(
        self, team_id: int, before: datetime, n: int = 10, venue: Optional[str] = None
    ) -> List[Dict]:
        """
        Returns the last n FINISHED matches for a team before `before`,
        enriched with goals scored/conceded, outcome, and stats.
        venue: None=all, "home"=home only, "away"=away only
        """
        query = (
            select(Match)
            .where(
                Match.status == "FINISHED",
                Match.match_date < before,
                Match.outcome.isnot(None),
                or_(Match.home_team_id == team_id, Match.away_team_id == team_id),
            )
            .order_by(Match.match_date.desc())
            .limit(n * 2)  # fetch extra to handle venue filter
        )

        if venue == "home":
            query = query.where(Match.home_team_id == team_id)
        elif venue == "away":
            query = query.where(Match.away_team_id == team_id)

        matches = self.session.execute(query).scalars().all()
        result = []
        for m in matches:
            is_home = m.home_team_id == team_id
            gs = (m.home_score if is_home else m.away_score) or 0
            gc = (m.away_score if is_home else m.home_score) or 0
            if m.outcome == "HOME":
                pts = 3 if is_home else 0
                outcome_label = "W" if is_home else "L"
            elif m.outcome == "AWAY":
                pts = 0 if is_home else 3
                outcome_label = "L" if is_home else "W"
            else:
                pts = 1
                outcome_label = "D"

            # Lookup stats
            stat = self.session.execute(
                select(TeamMatchStat).where(
                    TeamMatchStat.match_id == m.id,
                    TeamMatchStat.team_id == team_id,
                )
            ).scalar_one_or_none()

            result.append({
                "match_id": m.id,
                "date": m.match_date,
                "is_home": is_home,
                "goals_scored": gs,
                "goals_conceded": gc,
                "points": pts,
                "outcome": outcome_label,
                "xg": stat.xg if stat else None,
                "xga": stat.xga if stat else None,
                "shots": stat.shots if stat else None,
                "shots_on_target": stat.shots_on_target if stat else None,
                "possession": stat.possession if stat else None,
                "pass_accuracy": stat.pass_accuracy if stat else None,
            })
            if len(result) >= n:
                break

        return result

    # ──────────────────────────────────────────
    # Aggregate helpers
    # ──────────────────────────────────────────

    def _form(self, history: List[Dict]) -> Optional[float]:
        if not history:
            return None
        pts = sum(r["points"] for r in history)
        return pts / (len(history) * 3)

    def _avg(self, history: List[Dict], field: str) -> Optional[float]:
        vals = [r[field] for r in history if r.get(field) is not None]
        return float(np.mean(vals)) if vals else None

    def _win_rate(self, history: List[Dict]) -> Optional[float]:
        if not history:
            return None
        wins = sum(1 for r in history if r["outcome"] == "W")
        return wins / len(history)

    def _count_where(self, history: List[Dict], field: str, value: int) -> int:
        return sum(1 for r in history if r.get(field) == value)

    def _trend(self, values: List[float]) -> Optional[float]:
        """Linear regression slope of the values (positive = improving)."""
        if len(values) < 3:
            return None
        x = np.arange(len(values), dtype=float)
        y = np.array(values, dtype=float)
        slope = np.polyfit(x, y, 1)[0]
        return float(slope)

    # ──────────────────────────────────────────
    # Standing lookup
    # ──────────────────────────────────────────

    def _standing(
        self, team_id: int, comp_id: Optional[int], season: Optional[str]
    ) -> tuple:
        if not comp_id or not season:
            return None, None
        standing = self.session.execute(
            select(Standing)
            .where(
                Standing.team_id == team_id,
                Standing.competition_id == comp_id,
                Standing.season == season,
            )
            .order_by(Standing.matchday.desc())
        ).first()
        if not standing:
            return None, None
        s = standing[0]
        ppg = s.points / s.played if s.played else None
        return s.position, ppg

    # ──────────────────────────────────────────
    # Squad availability
    # ──────────────────────────────────────────

    def _active_injuries(self, team_id: int) -> int:
        return self.session.execute(
            select(func.count(Injury.id)).where(
                Injury.team_id == team_id, Injury.is_active == True
            )
        ).scalar() or 0

    def _active_suspensions(self, team_id: int) -> int:
        return self.session.execute(
            select(func.count(Suspension.id)).where(
                Suspension.team_id == team_id, Suspension.is_active == True
            )
        ).scalar() or 0

    def _market_value(self, team_id: int) -> Optional[float]:
        team = self.session.get(Team, team_id)
        return team.market_value_eur if team else None

    # ──────────────────────────────────────────
    # Schedule load
    # ──────────────────────────────────────────

    def _days_since_last(self, team_id: int, before: datetime) -> Optional[int]:
        last = self.session.execute(
            select(Match.match_date)
            .where(
                Match.status == "FINISHED",
                Match.match_date < before,
                or_(Match.home_team_id == team_id, Match.away_team_id == team_id),
            )
            .order_by(Match.match_date.desc())
        ).first()
        if not last or not last[0]:
            return None
        delta = before - last[0]
        return delta.days

    def _matches_in_window(self, team_id: int, before: datetime, days: int = 7) -> int:
        cutoff = before - timedelta(days=days)
        return self.session.execute(
            select(func.count(Match.id)).where(
                or_(Match.home_team_id == team_id, Match.away_team_id == team_id),
                Match.match_date >= cutoff,
                Match.match_date < before,
                Match.status == "FINISHED",
            )
        ).scalar() or 0

    # ──────────────────────────────────────────
    # Head-to-head
    # ──────────────────────────────────────────

    def _h2h(
        self, home_id: int, away_id: int, n: int = 10
    ) -> tuple:
        """Returns (home_win_rate, draw_rate, avg_total_goals) over last n H2H matches."""
        h2h_matches = self.session.execute(
            select(Match)
            .where(
                Match.status == "FINISHED",
                Match.outcome.isnot(None),
                or_(
                    and_(Match.home_team_id == home_id, Match.away_team_id == away_id),
                    and_(Match.home_team_id == away_id, Match.away_team_id == home_id),
                ),
            )
            .order_by(Match.match_date.desc())
            .limit(n)
        ).scalars().all()

        if not h2h_matches:
            return None, None, None

        home_wins, draws, total_goals = 0, 0, 0
        for m in h2h_matches:
            if m.home_score is not None and m.away_score is not None:
                total_goals += m.home_score + m.away_score
            # "home" perspective = team_a (home_id)
            if m.home_team_id == home_id:
                if m.outcome == "HOME":
                    home_wins += 1
                elif m.outcome == "DRAW":
                    draws += 1
            else:
                if m.outcome == "AWAY":
                    home_wins += 1
                elif m.outcome == "DRAW":
                    draws += 1

        n_real = len(h2h_matches)
        return (
            home_wins / n_real,
            draws / n_real,
            total_goals / n_real,
        )
