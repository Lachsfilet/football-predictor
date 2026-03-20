"""
Advanced match statistics ingestion using soccerdata's Understat backend.

Replaces the brittle HTML scraping of fbref.com with a stable, cached data
source that provides xG values. The public class name is kept for backwards
compatibility with existing scripts.
"""
from datetime import datetime, timezone, timedelta
import re
from typing import Dict, List, Mapping, Optional, Set, Iterable

import pandas as pd
from loguru import logger
from sqlalchemy import select
from soccerdata import Understat, FBref

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

# Mapping of internal competition codes to soccerdata/FBref league names
FBREF_LEAGUES: Dict[str, str] = {
    "PL": "ENG-Premier League",
    "PD": "ESP-La Liga",
    "BL1": "GER-Bundesliga",
    "SA": "ITA-Serie A",
    "FL1": "FRA-Ligue 1",
}


class FBrefConnector(BaseConnector):
    # Legacy source name preserved so CLI flags (--source fbref) and sync logs
    # remain compatible, even though data now comes from Understat.
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
        started = datetime.now(timezone.utc)
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

            fbref_league = FBREF_LEAGUES.get(code)
            if not fbref_league:
                logger.warning(f"[fbref] No FBref mapping for league code {code}")
                continue
            try:
                updated = self._sync_fbref_match_stats(code, fbref_league, seasons)
                total += updated
            except Exception as e:
                logger.error(f"[fbref] FBref match stats sync failed for {code}: {e}")

        self.log_sync("fetch_all", "success", records_inserted=total, started_at=started)

    # ──────────────────────────────────────────
    # Per-league sync (Understat xG)
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
                f"[fbref] Understat data for {league_name} not found in cache. "
                "Run the data fetch script (e.g., `python scripts/fetch_data.py --source fbref`) to trigger the Understat download or check connectivity."
            )
            return 0
        except Exception as e:
            logger.error(f"[fbref] Understat failed for {league_name}: {e}")
            return 0

        if df.empty:
            logger.warning(f"[fbref] No Understat schedule data for {league_name}")
            return 0

        inserted = 0
        with db_session() as session:
            resolver = TeamNameResolver(session)
            for row in df.itertuples(index=False):
                record = self._build_record(row._asdict())
                if not record:
                    continue
                if self._save_stats(session, resolver, record):
                    inserted += 1

        logger.info(f"[fbref][{code}] Stats records saved: {inserted}")
        return inserted

    # ──────────────────────────────────────────
    # Per-league sync (FBref match stats)
    # ──────────────────────────────────────────

    def _sync_fbref_match_stats(self, code: str, league_name: str, seasons: List[int]) -> int:
        """Fetch match-level stats from FBref and store TeamMatchStat fields."""
        logger.info(f"[fbref] Fetching FBref match stats for {league_name} seasons {seasons}")
        try:
            fbref = FBref(leagues=league_name, seasons=seasons)
            schedule = self._load_team_match_stats(fbref, "schedule")
            shooting = self._load_team_match_stats(fbref, "shooting")
            passing = self._load_team_match_stats(fbref, "passing")
        except Exception as e:
            logger.error(f"[fbref] FBref read failed for {league_name}: {e}")
            return 0

        if schedule.empty:
            logger.warning(f"[fbref] No FBref schedule data for {league_name}")
            return 0

        merge_cols = self._match_merge_columns(schedule)
        if not merge_cols:
            logger.warning(f"[fbref] Could not determine merge columns for {league_name}")
            return 0

        if not shooting.empty:
            shooting = shooting[self._columns_present(shooting, merge_cols + ["Sh", "SoT"])]
            schedule = schedule.merge(shooting, on=merge_cols, how="left", suffixes=("", "_shoot"))

        if not passing.empty:
            passing = passing[self._columns_present(passing, merge_cols + ["Cmp", "Att", "Cmp%"])]
            schedule = schedule.merge(passing, on=merge_cols, how="left", suffixes=("", "_pass"))

        updated = 0
        with db_session() as session:
            resolver = TeamNameResolver(session)
            for row in schedule.itertuples(index=False):
                row_dict = row._asdict()
                if self._save_fbref_match_stats(session, resolver, row_dict):
                    updated += 1

        logger.info(f"[fbref][{code}] Match stat rows updated: {updated}")
        return updated

    def _load_team_match_stats(self, fbref: FBref, stat_type: str) -> pd.DataFrame:
        df = fbref.read_team_match_stats(stat_type=stat_type)
        if isinstance(df.index, pd.MultiIndex):
            df = df.reset_index()
        elif df.index.name:
            df = df.reset_index()
        return df

    def _match_merge_columns(self, df: pd.DataFrame) -> List[str]:
        candidates = ["league", "season", "team", "game"]
        return [c for c in candidates if c in df.columns]

    def _columns_present(self, df: pd.DataFrame, columns: Iterable[str]) -> List[str]:
        return [c for c in columns if c in df.columns]

    def _save_fbref_match_stats(self, session, resolver: TeamNameResolver, row: dict) -> bool:
        team_name = self._first_value(row, ["team", "Team"])
        opponent_name = self._first_value(row, ["opponent", "Opponent"])
        if not team_name or not opponent_name:
            return False

        team = resolver.resolve(str(team_name))
        opponent = resolver.resolve(str(opponent_name))
        if not team or not opponent:
            return False

        venue = str(self._first_value(row, ["venue", "Venue"]) or "")
        is_home = venue.lower().startswith("home")
        home_id = team.id if is_home else opponent.id
        away_id = opponent.id if is_home else team.id

        match_date = self._parse_date(self._first_value(row, ["date", "Date"]))
        if not match_date:
            return False

        match_day = match_date.replace(hour=0, minute=0, second=0, microsecond=0)
        match = session.execute(
            select(Match).where(
                Match.home_team_id == home_id,
                Match.away_team_id == away_id,
                Match.match_date >= match_day,
                Match.match_date < match_day + timedelta(days=1),
            )
        ).scalar_one_or_none()

        if not match:
            return False

        shots = self._coerce_int(self._first_value(row, ["Sh", "Shots"]))
        shots_on_target = self._coerce_int(self._first_value(row, ["SoT", "SoT%", "Shots on Target"]))
        possession = self._coerce_float(self._first_value(row, ["Poss", "Possession", "Poss%", "Possession%"]))
        passes_att = self._coerce_int(self._first_value(row, ["Att", "Passes"], default=None))
        pass_accuracy = self._coerce_float(self._first_value(row, ["Cmp%", "Pass%", "Cmp %"], default=None))
        passes_cmp = self._coerce_int(self._first_value(row, ["Cmp", "Completed"], default=None))
        if pass_accuracy is None and passes_cmp is not None and passes_att:
            pass_accuracy = round((passes_cmp / passes_att) * 100, 2)

        goals = self._coerce_int(self._first_value(row, ["GF", "Goals For", "Gls"], default=None))
        xg = self._coerce_float(self._first_value(row, ["xG", "xg"], default=None))
        xga = self._coerce_float(self._first_value(row, ["xGA", "xga"], default=None))

        stat = session.execute(
            select(TeamMatchStat).where(
                TeamMatchStat.match_id == match.id,
                TeamMatchStat.team_id == team.id,
            )
        ).scalar_one_or_none()

        if stat:
            if shots is not None:
                stat.shots = shots
            if shots_on_target is not None:
                stat.shots_on_target = shots_on_target
            if possession is not None:
                stat.possession = possession
            if passes_att is not None:
                stat.passes = passes_att
            if pass_accuracy is not None:
                stat.pass_accuracy = pass_accuracy
            if goals is not None:
                stat.goals = goals
            if xg is not None:
                stat.xg = xg
            if xga is not None:
                stat.xga = xga
        else:
            session.add(TeamMatchStat(
                match_id=match.id,
                team_id=team.id,
                is_home=is_home,
                shots=shots,
                shots_on_target=shots_on_target,
                possession=possession,
                passes=passes_att,
                pass_accuracy=pass_accuracy,
                goals=goals,
                xg=xg,
                xga=xga,
            ))

        return True

    def _first_value(self, row: dict, keys: Iterable[str], default: Optional[object] = None):
        for key in keys:
            if key in row and row[key] is not None and not pd.isna(row[key]):
                return row[key]
        return default

    def _parse_date(self, value) -> Optional[datetime]:
        if value is None or pd.isna(value):
            return None
        try:
            dt_val = pd.to_datetime(value)
        except (ValueError, TypeError):
            return None
        if dt_val.tzinfo is not None:
            dt_val = dt_val.tz_convert(None)
        return dt_val.to_pydatetime()

    def _coerce_float(self, val) -> Optional[float]:
        if val is None or pd.isna(val):
            return None
        if isinstance(val, str):
            cleaned = val.replace("%", "").replace(",", "").strip()
        else:
            cleaned = val
        try:
            return float(cleaned)
        except (ValueError, TypeError):
            return None

    def _coerce_int(self, val) -> Optional[int]:
        if val is None or pd.isna(val):
            return None
        if isinstance(val, str):
            cleaned = val.replace(",", "").strip()
        else:
            cleaned = val
        try:
            return int(float(cleaned))
        except (ValueError, TypeError):
            return None

    def _build_record(self, row: Mapping[str, object]) -> Optional[dict]:
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

    def _save_stats(self, session, resolver: TeamNameResolver, record: dict) -> bool:
        """Match the row to a DB Match and store xG values."""
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
                stat_payload = {
                    "match_id": match.id,
                    "team_id": team_id,
                    "is_home": is_home,
                    "xg": xg,
                    "xga": xga,
                    "goals": goals,
                }
                session.add(TeamMatchStat(**stat_payload))

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
            year = datetime.now(timezone.utc).year
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