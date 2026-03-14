"""
FBref scraper for advanced match statistics (xG, shots, possession, etc.).
FBref allows scraping with polite rate limiting.
"""
import re
import time
from datetime import datetime
from typing import Optional, Dict, List
import pandas as pd
from bs4 import BeautifulSoup
from loguru import logger
from sqlalchemy import select

from config.settings import settings
from database.session import db_session
from database.models import Match, Team, TeamMatchStat
from .base import BaseConnector

# FBref competition URL slugs for top leagues
FBREF_LEAGUES: Dict[str, Dict] = {
    "PL":  {"url_slug": "9/Premier-League",    "name": "Premier League"},
    "PD":  {"url_slug": "12/La-Liga",          "name": "La Liga"},
    "BL1": {"url_slug": "20/Bundesliga",       "name": "Bundesliga"},
    "SA":  {"url_slug": "11/Serie-A",          "name": "Serie A"},
    "FL1": {"url_slug": "13/Ligue-1",          "name": "Ligue 1"},
}

FBREF_BASE = "https://fbref.com"


class FBrefConnector(BaseConnector):
    source_name = "fbref"
    base_url = FBREF_BASE

    # FBref asks for a longer delay between requests
    def __init__(self):
        super().__init__()
        self.client.headers.update({
            "Accept-Encoding": "gzip, deflate",
            "Referer": "https://fbref.com",
        })

    # ──────────────────────────────────────────
    # Public entry point
    # ──────────────────────────────────────────

    def fetch_all(self):
        started = datetime.utcnow()
        total = 0
        for code in settings.tracked_leagues_list:
            meta = FBREF_LEAGUES.get(code)
            if not meta:
                continue
            try:
                n = self._sync_league_stats(code, meta)
                total += n
            except Exception as e:
                logger.error(f"[fbref] Error syncing {code}: {e}")

        self.log_sync("fetch_all", "success", records_inserted=total, started_at=started)

    # ──────────────────────────────────────────
    # Per-league sync
    # ──────────────────────────────────────────

    def _sync_league_stats(self, code: str, meta: dict) -> int:
        """Scrape season match scores + stats page."""
        slug = meta["url_slug"]
        url = f"{FBREF_BASE}/en/comps/{slug}/schedule/{meta['name'].replace(' ', '-')}-Scores-and-Fixtures"
        logger.info(f"[fbref] Fetching scores page: {url}")

        try:
            resp = self._get(url)
        except Exception as e:
            logger.error(f"[fbref] Could not load schedule for {code}: {e}")
            return 0

        soup = BeautifulSoup(resp.text, "lxml")
        table = soup.find("table", {"id": re.compile(r"sched_")})
        if not table:
            logger.warning(f"[fbref] No schedule table found for {code}")
            return 0

        rows = table.find("tbody").find_all("tr") if table.find("tbody") else []
        inserted = 0
        for row in rows:
            if row.get("class") and "spacer" in row.get("class", []):
                continue
            record = self._parse_schedule_row(row)
            if not record:
                continue
            if self._save_stats(record, code):
                inserted += 1
            # polite delay
            time.sleep(0.5)

        logger.info(f"[fbref][{code}] Stats records saved: {inserted}")
        return inserted

    def _parse_schedule_row(self, row) -> Optional[dict]:
        cells = row.find_all(["td", "th"])
        if len(cells) < 10:
            return None

        def text(tag_id: str) -> str:
            el = row.find(attrs={"data-stat": tag_id})
            return el.get_text(strip=True) if el else ""

        date_str = text("date")
        if not date_str:
            return None

        home = text("home_team")
        away = text("away_team")
        score = text("score")

        if not home or not away:
            return None

        home_goals, away_goals = None, None
        if "–" in score or "-" in score:
            parts = score.replace("–", "-").split("-")
            try:
                home_goals = int(parts[0].strip())
                away_goals = int(parts[1].strip())
            except (ValueError, IndexError):
                pass

        xg_home = self._safe_float(text("home_xg"))
        xg_away = self._safe_float(text("away_xg"))

        match_url = None
        score_tag = row.find(attrs={"data-stat": "score"})
        if score_tag:
            a = score_tag.find("a")
            if a and a.get("href"):
                match_url = FBREF_BASE + a["href"]

        return {
            "date_str": date_str,
            "home_team_name": home,
            "away_team_name": away,
            "home_goals": home_goals,
            "away_goals": away_goals,
            "xg_home": xg_home,
            "xg_away": xg_away,
            "match_url": match_url,
        }

    def _safe_float(self, val: str) -> Optional[float]:
        try:
            return float(val) if val else None
        except ValueError:
            return None

    def _save_stats(self, record: dict, comp_code: str) -> bool:
        """Match the parsed row to a DB Match and store stats."""
        home_name = record["home_team_name"]
        away_name = record["away_team_name"]

        with db_session() as session:
            home_team = self._find_team(session, home_name)
            away_team = self._find_team(session, away_name)
            if not home_team or not away_team:
                return False

            # Find the match
            try:
                match_date = datetime.strptime(record["date_str"], "%Y-%m-%d")
            except ValueError:
                return False

            match = session.execute(
                select(Match).where(
                    Match.home_team_id == home_team.id,
                    Match.away_team_id == away_team.id,
                    Match.match_date >= datetime(match_date.year, match_date.month, match_date.day),
                    Match.match_date < datetime(match_date.year, match_date.month, match_date.day) + pd.Timedelta(days=1),
                )
            ).scalar_one_or_none()

            if not match:
                return False

            # Upsert home stats
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

    def _find_team(self, session, name: str) -> Optional[Team]:
        """Fuzzy-match a team name to the DB."""
        from ingestion.team_resolver import TeamNameResolver
        resolver = TeamNameResolver(session)
        return resolver.resolve(name)

    # ──────────────────────────────────────────
    # Detailed match stats (optional deep fetch)
    # ──────────────────────────────────────────

    def fetch_match_detail(self, match_url: str, match_id: int):
        """Scrape detailed stats from a single match report page."""
        if not match_url:
            return
        try:
            resp = self._get(match_url)
        except Exception as e:
            logger.warning(f"[fbref] Could not load match detail {match_url}: {e}")
            return

        soup = BeautifulSoup(resp.text, "lxml")

        def get_stat(team_idx: int, stat_name: str) -> Optional[float]:
            """team_idx: 0=home, 1=away"""
            el = soup.find("td", {"data-stat": stat_name})
            if el:
                try:
                    return float(el.get_text(strip=True))
                except (ValueError, TypeError):
                    pass
            return None

        # Parse team stats from #team_stats table
        stats_block = soup.find("div", {"id": "team_stats"})
        if not stats_block:
            return

        parsed = self._parse_team_stats_block(stats_block)
        if not parsed:
            return

        with db_session() as session:
            match = session.get(Match, match_id)
            if not match:
                return
            for team_id, is_home, side in [
                (match.home_team_id, True, "home"),
                (match.away_team_id, False, "away"),
            ]:
                stat = session.execute(
                    select(TeamMatchStat).where(
                        TeamMatchStat.match_id == match_id,
                        TeamMatchStat.team_id == team_id,
                    )
                ).scalar_one_or_none()

                d = parsed.get(side, {})
                if stat:
                    for attr in ["possession", "shots", "shots_on_target", "corners",
                                 "fouls", "yellow_cards", "red_cards", "pass_accuracy"]:
                        if attr in d:
                            setattr(stat, attr, d[attr])
                else:
                    session.add(TeamMatchStat(
                        match_id=match_id,
                        team_id=team_id,
                        is_home=is_home,
                        **{k: v for k, v in d.items() if k in TeamMatchStat.__table__.columns},
                    ))

    def _parse_team_stats_block(self, block) -> dict:
        """Parse possession, shots, etc. from FBref team stats HTML."""
        result = {"home": {}, "away": {}}
        rows = block.find_all("tr")
        for row in rows:
            cells = row.find_all(["td", "th"])
            if len(cells) < 3:
                continue
            stat_name = cells[1].get_text(strip=True).lower()
            home_val = self._safe_float(cells[0].get_text(strip=True).rstrip("%"))
            away_val = self._safe_float(cells[2].get_text(strip=True).rstrip("%"))

            mapping = {
                "possession": "possession",
                "shots": "shots",
                "shots on target": "shots_on_target",
                "corners": "corners",
                "fouls": "fouls",
                "yellow cards": "yellow_cards",
                "red cards": "red_cards",
            }
            db_key = mapping.get(stat_name)
            if db_key:
                if home_val is not None:
                    result["home"][db_key] = home_val
                if away_val is not None:
                    result["away"][db_key] = away_val

        return result
