"""
Advanced match statistics ingestion using soccerdata's Understat backend.

Replaces the brittle HTML scraping of fbref.com with a stable, cached data
source that provides xG values. The public class name is kept for backwards
compatibility with existing scripts.
"""
from datetime import datetime
import re
from typing import Dict, List, Optional, Set

import pandas as pd
from loguru import logger
from sqlalchemy import select
from soccerdata import Understat

from config.settings import settings
from database.session import db_session
from database.models import Match, TeamMatchStat
from .base import BaseConnector
from .team_resolver import TeamNameResolver

# Mapping of internal competition codes to soccerdata/Understat league names
UNDERSTAT_LEAGUES: Dict[str, str] = {
    "PL": "ENG-Premier League",
    "PD": "ESP-La Liga",
    "BL1": "GER-Bundesliga",
    "SA": "ITA-Serie A",
    "FL1": "FRA-Ligue 1",
}


class FBrefConnector(BaseConnector):
    source_name = "fbref"

    def __init__(self):
        # BaseConnector currently sets up an httpx client; we don't rely on it
        # here but keep the initialization for consistency with context manager
        # behavior used elsewhere.
        super().__init__()

    # ──────────────────────────────────────────
    # Public entry point
    # ──────────────────────────────────────────

    def fetch_all(self):
        started = datetime.utcnow()
        total = 0
        seasons = self._target_seasons()

        for code in settings.tracked_leagues_list:
            league = UNDERSTAT_LEAGUES.get(code)
            if not league:
                logger.warning(f"[fbref] No Understat mapping for league code {code}")
                continue
            try:
                inserted = self._sync_league_stats(code, league, seasons)
                total += inserted
            except Exception as e:
                logger.error(f"[fbref] Error syncing {code}: {e}")

        self.log_sync("fetch_all", "success", records_inserted=total, started_at=started)

    # ──────────────────────────────────────────
    # Per-league sync
    # ──────────────────────────────────────────

    def _sync_league_stats(self, code: str, league_name: str, seasons: List[int]) -> int:
        """
        Fetch xG-enhanced match results for a league.

        soccerdata handles its own caching and request retries; we rely on its
        built-in resilience rather than duplicating retry logic here.
        """
        logger.info(f"[fbref] Fetching Understat data for {league_name} seasons {seasons}")
        try:
            reader = Understat(leagues=[league_name], seasons=seasons)
            df = reader.read_schedule(include_matches_without_data=False)
        except FileNotFoundError:
            logger.warning(
                f"[fbref] Understat data for {league_name} not available yet; ensure initial download succeeds"
            )
            return 0
        except Exception as e:
            logger.error(f"[fbref] Understat failed for {league_name}: {e}")
            return 0

        if df.empty:
            logger.warning(f"[fbref] No Understat schedule data for {league_name}")
            return 0

        inserted = 0
        for _, row in df.iterrows():
            record = self._build_record(row)
            if not record:
                continue
            if self._save_stats(record):
                inserted += 1

        logger.info(f"[fbref][{code}] Stats records saved: {inserted}")
        return inserted

    def _build_record(self, row: pd.Series) -> Optional[dict]:
        """Map a soccerdata schedule row to our internal structure."""
        date_val = row.get("date")
        if pd.isna(date_val):
            return None
        try:
            dt_val = pd.to_datetime(date_val)
            if dt_val.tzinfo is not None:
                dt_val = dt_val.tz_convert(None)
            match_date = dt_val.to_pydatetime()
        except (ValueError, TypeError):
            return None

        home = row.get("home_team")
        away = row.get("away_team")
        if not home or not away:
            return None

        return {
            "match_date": match_date,
            "home_team_name": str(home),
            "away_team_name": str(away),
            "home_goals": self._safe_int(row.get("home_goals")),
            "away_goals": self._safe_int(row.get("away_goals")),
            "xg_home": self._safe_float(row.get("home_xg")),
            "xg_away": self._safe_float(row.get("away_xg")),
        }

    def _safe_float(self, val) -> Optional[float]:
        if val is None or pd.isna(val):
            return None
        try:
            return float(val)
        except (ValueError, TypeError):
            return None

    def _safe_int(self, val) -> Optional[int]:
        if val is None or pd.isna(val):
            return None
        try:
            return int(val)
        except (ValueError, TypeError):
            return None

    def _save_stats(self, record: dict) -> bool:
        """Match the row to a DB Match and store xG values."""
        with db_session() as session:
            resolver = TeamNameResolver(session)
            home_team = resolver.resolve(record["home_team_name"])
            away_team = resolver.resolve(record["away_team_name"])
            if not home_team or not away_team:
                return False

            match_day = record["match_date"].replace(hour=0, minute=0, second=0, microsecond=0)

            match = session.execute(
                select(Match).where(
                    Match.home_team_id == home_team.id,
                    Match.away_team_id == away_team.id,
                    Match.match_date >= match_day,
                    Match.match_date < match_day + pd.Timedelta(days=1),
                )
            ).scalar_one_or_none()

            if not match:
                return False

            for team_id, is_home, xg, xga, goals in [
                (home_team.id, True, record["xg_home"], record["xg_away"], record["home_goals"]),
                (away_team.id, False, record["xg_away"], record["xg_home"], record["away_goals"]),
            ]:
                stat = session.execute(
                    select(TeamMatchStat).where(
                        TeamMatchStat.match_id == match.id,
                        TeamMatchStat.team_id == team_id,
                    )
                ).scalar_one_or_none()

                if stat:
                    if xg is not None:
                        stat.xg = xg
                    if xga is not None:
                        stat.xga = xga
                    if goals is not None:
                        stat.goals = goals
                else:
                    session.add(TeamMatchStat(
                        match_id=match.id,
                        team_id=team_id,
                        is_home=is_home,
                        xg=xg,
                        xga=xga,
                        goals=goals,
                    ))

        return True

    # ──────────────────────────────────────────
    # Season selection helpers
    # ──────────────────────────────────────────

    def _target_seasons(self) -> List[int]:
        """Use seasons present in the DB; fall back to previous year and current year."""
        seasons: Set[int] = set()
        with db_session() as session:
            for season_val in session.execute(select(Match.season)).scalars().all():
                normalized = self._normalize_season(season_val)
                if normalized:
                    seasons.add(normalized)

        if not seasons:
            year = datetime.utcnow().year
            seasons.update([year - 1, year])

        return sorted(seasons)

    def _normalize_season(self, val) -> Optional[int]:
        if val is None:
            return None
        if isinstance(val, int):
            return val
        if isinstance(val, str):
            match = re.search(r"(20\d{2})", val)
            if match:
                try:
                    return int(match.group(1))
                except ValueError:
                    return None
            try:
                return int(val)
            except ValueError:
                return None
        return None
