"""
Connector for football-data.org free tier API.
Provides: competitions, teams, fixtures, results, standings, squads.

Free tier limits: 10 req/min, top-tier competitions only.
Register for a free API key at: https://www.football-data.org/
"""
from datetime import datetime, timedelta
from typing import Optional
from loguru import logger
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy import select

from config.settings import settings
from database.session import db_session
from database.models import (
    Competition, Team, CompetitionTeam, Match, Standing, Player
)
from .base import BaseConnector

# Mapping of competition code → competition metadata
COMPETITION_META = {
    "PL":  {"name": "Premier League",     "country": "England"},
    "PD":  {"name": "La Liga",            "country": "Spain"},
    "BL1": {"name": "Bundesliga",         "country": "Germany"},
    "SA":  {"name": "Serie A",            "country": "Italy"},
    "FL1": {"name": "Ligue 1",            "country": "France"},
    "DED": {"name": "Eredivisie",         "country": "Netherlands"},
    "PPL": {"name": "Primeira Liga",      "country": "Portugal"},
    "CL":  {"name": "Champions League",   "country": "Europe"},
}


class FootballDataOrgConnector(BaseConnector):
    source_name = "football_data_org"
    base_url = "https://api.football-data.org/v4"

    def __init__(self, api_key: Optional[str] = None):
        super().__init__()
        key = api_key or settings.FOOTBALL_DATA_API_KEY
        if not key:
            logger.warning(
                "No FOOTBALL_DATA_API_KEY set. "
                "Register free at football-data.org and add key to .env"
            )
        self._headers = {"X-Auth-Token": key} if key else {}

    # ──────────────────────────────────────────
    # Public entry point
    # ──────────────────────────────────────────

    def fetch_all(self):
        started = datetime.utcnow()
        total_inserted = 0
        try:
            for code in settings.tracked_leagues_list:
                logger.info(f"[football-data.org] Syncing competition: {code}")
                comp_id = self._upsert_competition(code)
                if comp_id is None:
                    continue
                self._sync_standings(code, comp_id)
                n = self._sync_matches(code, comp_id)
                total_inserted += n
                self._sync_squad(code, comp_id)

            self.log_sync(
                "fetch_all", "success",
                records_inserted=total_inserted,
                started_at=started,
            )
        except Exception as e:
            logger.error(f"[football-data.org] fetch_all failed: {e}")
            self.log_sync("fetch_all", "error", error_message=str(e), started_at=started)
            raise

    # ──────────────────────────────────────────
    # Competition
    # ──────────────────────────────────────────

    def _upsert_competition(self, code: str) -> Optional[int]:
        try:
            resp = self._get(f"{self.base_url}/competitions/{code}", headers=self._headers)
            data = resp.json()
        except Exception as e:
            logger.error(f"Failed to fetch competition {code}: {e}")
            return None

        meta = COMPETITION_META.get(code, {})
        with db_session() as session:
            comp = session.execute(
                select(Competition).where(Competition.code == code)
            ).scalar_one_or_none()

            if comp is None:
                comp = Competition(
                    code=code,
                    name=data.get("name", meta.get("name", code)),
                    country=data.get("area", {}).get("name", meta.get("country")),
                    external_id=str(data.get("id", "")),
                    current_season=self._current_season_str(data),
                )
                session.add(comp)
                session.flush()
            else:
                comp.current_season = self._current_season_str(data)
                comp.updated_at = datetime.utcnow()

            return comp.id

    def _current_season_str(self, data: dict) -> str:
        season = data.get("currentSeason") or {}
        return season.get("startDate", "")[:4] or str(datetime.utcnow().year)

    # ──────────────────────────────────────────
    # Standings
    # ──────────────────────────────────────────

    def _sync_standings(self, code: str, comp_id: int):
        try:
            resp = self._get(
                f"{self.base_url}/competitions/{code}/standings",
                headers=self._headers,
            )
        except Exception as e:
            logger.error(f"Standings fetch failed for {code}: {e}")
            return

        data = resp.json()
        season_str = (data.get("season") or {}).get("startDate", "")[:4]
        matchday = (data.get("season") or {}).get("currentMatchday", 0)

        for table in data.get("standings", []):
            if table.get("type") != "TOTAL":
                continue
            for row in table.get("table", []):
                team_data = row.get("team", {})
                team_id = self._upsert_team(team_data, comp_id, season_str)
                if not team_id:
                    continue
                with db_session() as session:
                    existing = session.execute(
                        select(Standing).where(
                            Standing.competition_id == comp_id,
                            Standing.team_id == team_id,
                            Standing.season == season_str,
                            Standing.matchday == matchday,
                        )
                    ).scalar_one_or_none()
                    if existing:
                        continue
                    session.add(Standing(
                        competition_id=comp_id,
                        team_id=team_id,
                        season=season_str,
                        matchday=matchday,
                        position=row.get("position"),
                        played=row.get("playedGames", 0),
                        won=row.get("won", 0),
                        drawn=row.get("draw", 0),
                        lost=row.get("lost", 0),
                        goals_for=row.get("goalsFor", 0),
                        goals_against=row.get("goalsAgainst", 0),
                        goal_difference=row.get("goalDifference", 0),
                        points=row.get("points", 0),
                        form=row.get("form"),
                    ))

    # ──────────────────────────────────────────
    # Matches
    # ──────────────────────────────────────────

    def _sync_matches(self, code: str, comp_id: int) -> int:
        """Fetch last 60 days of finished matches + next 14 days of scheduled."""
        inserted = 0
        date_from = (datetime.utcnow() - timedelta(days=60)).strftime("%Y-%m-%d")
        date_to = (datetime.utcnow() + timedelta(days=14)).strftime("%Y-%m-%d")

        try:
            resp = self._get(
                f"{self.base_url}/competitions/{code}/matches",
                headers=self._headers,
                params={"dateFrom": date_from, "dateTo": date_to},
            )
        except Exception as e:
            logger.error(f"Matches fetch failed for {code}: {e}")
            return 0

        data = resp.json()
        season_str = ""

        for m in data.get("matches", []):
            ext_id = str(m["id"])
            home_data = m.get("homeTeam", {})
            away_data = m.get("awayTeam", {})

            season_info = m.get("season") or {}
            season_str = str(season_info.get("startDate", ""))[:4]

            home_id = self._upsert_team(home_data, comp_id, season_str)
            away_id = self._upsert_team(away_data, comp_id, season_str)
            if not home_id or not away_id:
                continue

            score = m.get("score", {})
            ft = score.get("fullTime", {})
            ht = score.get("halfTime", {})
            winner = score.get("winner")
            outcome = None
            if winner == "HOME_TEAM":
                outcome = "HOME"
            elif winner == "AWAY_TEAM":
                outcome = "AWAY"
            elif winner == "DRAW":
                outcome = "DRAW"

            with db_session() as session:
                existing = session.execute(
                    select(Match).where(Match.external_id == ext_id)
                ).scalar_one_or_none()

                match_date_str = m.get("utcDate", "")
                match_date = None
                if match_date_str:
                    try:
                        match_date = datetime.fromisoformat(match_date_str.replace("Z", "+00:00"))
                    except ValueError:
                        pass

                if existing:
                    existing.status = m.get("status", existing.status)
                    existing.home_score = ft.get("home")
                    existing.away_score = ft.get("away")
                    existing.home_score_ht = ht.get("home")
                    existing.away_score_ht = ht.get("away")
                    existing.outcome = outcome
                    existing.updated_at = datetime.utcnow()
                else:
                    session.add(Match(
                        external_id=ext_id,
                        competition_id=comp_id,
                        season=season_str,
                        matchday=m.get("matchday"),
                        home_team_id=home_id,
                        away_team_id=away_id,
                        match_date=match_date,
                        status=m.get("status", "SCHEDULED"),
                        home_score=ft.get("home"),
                        away_score=ft.get("away"),
                        home_score_ht=ht.get("home"),
                        away_score_ht=ht.get("away"),
                        outcome=outcome,
                        referee=self._referee_name(m),
                    ))
                    inserted += 1

        logger.info(f"[{code}] Matches inserted: {inserted}")
        return inserted

    def _referee_name(self, match: dict) -> Optional[str]:
        refs = match.get("referees", [])
        return refs[0]["name"] if refs else None

    # ──────────────────────────────────────────
    # Squads
    # ──────────────────────────────────────────

    def _sync_squad(self, code: str, comp_id: int):
        try:
            resp = self._get(
                f"{self.base_url}/competitions/{code}/teams",
                headers=self._headers,
            )
        except Exception as e:
            logger.error(f"Teams fetch failed for {code}: {e}")
            return

        season_str = str(datetime.utcnow().year)
        for team_data in resp.json().get("teams", []):
            team_id = self._upsert_team(team_data, comp_id, season_str)
            if not team_id:
                continue
            for p in team_data.get("squad", []):
                self._upsert_player(p, team_id)

    # ──────────────────────────────────────────
    # Helpers: upsert team / player
    # ──────────────────────────────────────────

    def _upsert_team(self, data: dict, comp_id: int, season: str) -> Optional[int]:
        if not data or not data.get("id"):
            return None
        ext_id = str(data["id"])
        with db_session() as session:
            team = session.execute(
                select(Team).where(Team.external_id == ext_id)
            ).scalar_one_or_none()

            if team is None:
                team = Team(
                    name=data.get("name", ""),
                    short_name=data.get("shortName", data.get("name", "")),
                    tla=data.get("tla", ""),
                    country=data.get("area", {}).get("name"),
                    founded=data.get("founded"),
                    stadium=data.get("venue"),
                    external_id=ext_id,
                    aliases=[],
                )
                session.add(team)
                session.flush()
            else:
                if data.get("name") and team.name != data["name"]:
                    team.name = data["name"]

            # Link team to competition if not already
            existing_link = session.execute(
                select(CompetitionTeam).where(
                    CompetitionTeam.competition_id == comp_id,
                    CompetitionTeam.team_id == team.id,
                    CompetitionTeam.season == season,
                )
            ).scalar_one_or_none()
            if not existing_link:
                session.add(CompetitionTeam(
                    competition_id=comp_id,
                    team_id=team.id,
                    season=season,
                ))

            return team.id

    def _upsert_player(self, data: dict, team_id: int):
        if not data or not data.get("id"):
            return
        ext_id = str(data["id"])
        dob = None
        if data.get("dateOfBirth"):
            try:
                dob = datetime.strptime(data["dateOfBirth"], "%Y-%m-%d").date()
            except ValueError:
                pass

        with db_session() as session:
            player = session.execute(
                select(Player).where(Player.external_id == ext_id)
            ).scalar_one_or_none()
            if player is None:
                session.add(Player(
                    team_id=team_id,
                    name=data.get("name", ""),
                    position=data.get("position"),
                    nationality=data.get("nationality"),
                    date_of_birth=dob,
                    shirt_number=data.get("shirtNumber"),
                    external_id=ext_id,
                ))
            else:
                player.team_id = team_id
                player.position = data.get("position", player.position)
                player.shirt_number = data.get("shirtNumber", player.shirt_number)
                player.updated_at = datetime.utcnow()

    # ──────────────────────────────────────────
    # Historical data fetch
    # ──────────────────────────────────────────

    def fetch_historical(self, code: str, seasons: int = 3):
        """Fetch multiple past seasons for training data."""
        comp_id = self._upsert_competition(code)
        if not comp_id:
            return

        current_year = datetime.utcnow().year
        for year_offset in range(seasons):
            start_year = current_year - year_offset - 1
            date_from = f"{start_year}-06-01"
            date_to = f"{start_year + 1}-06-30"
            logger.info(f"[{code}] Fetching historical season {start_year}/{start_year+1}")
            try:
                resp = self._get(
                    f"{self.base_url}/competitions/{code}/matches",
                    headers=self._headers,
                    params={"dateFrom": date_from, "dateTo": date_to, "status": "FINISHED"},
                )
                for m in resp.json().get("matches", []):
                    ext_id = str(m["id"])
                    season_str = str(start_year)
                    home_id = self._upsert_team(m.get("homeTeam", {}), comp_id, season_str)
                    away_id = self._upsert_team(m.get("awayTeam", {}), comp_id, season_str)
                    if not home_id or not away_id:
                        continue

                    score = m.get("score", {})
                    ft = score.get("fullTime", {})
                    winner = score.get("winner")
                    outcome = {"HOME_TEAM": "HOME", "AWAY_TEAM": "AWAY", "DRAW": "DRAW"}.get(winner)

                    match_date = None
                    try:
                        match_date = datetime.fromisoformat(
                            m.get("utcDate", "").replace("Z", "+00:00")
                        )
                    except (ValueError, TypeError):
                        pass

                    with db_session() as session:
                        if not session.execute(
                            select(Match).where(Match.external_id == ext_id)
                        ).scalar_one_or_none():
                            session.add(Match(
                                external_id=ext_id,
                                competition_id=comp_id,
                                season=season_str,
                                matchday=m.get("matchday"),
                                home_team_id=home_id,
                                away_team_id=away_id,
                                match_date=match_date,
                                status="FINISHED",
                                home_score=ft.get("home"),
                                away_score=ft.get("away"),
                                outcome=outcome,
                            ))
            except Exception as e:
                logger.warning(f"Historical fetch error for {code} season {start_year}: {e}")
