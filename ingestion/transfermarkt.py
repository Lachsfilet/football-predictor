"""
Transfermarkt scraper for injuries, suspensions, and squad market values.
Uses polite scraping with delays.
"""
import re
import time
from datetime import datetime, date
from typing import Optional, Dict, List
from bs4 import BeautifulSoup
from loguru import logger
from sqlalchemy import select

from config.settings import settings
from database.session import db_session
from database.models import Team, Player, Injury, Competition
from .base import BaseConnector

# Transfermarkt league URL fragments
TM_LEAGUES: Dict[str, str] = {
    "PL":  "premier-league/startseite/wettbewerb/GB1",
    "PD":  "laliga/startseite/wettbewerb/ES1",
    "BL1": "bundesliga/startseite/wettbewerb/L1",
    "SA":  "serie-a/startseite/wettbewerb/IT1",
    "FL1": "ligue-1/startseite/wettbewerb/FR1",
}

TM_BASE = "https://www.transfermarkt.com"


class TransfermarktConnector(BaseConnector):
    source_name = "transfermarkt"
    base_url = TM_BASE

    def __init__(self):
        super().__init__()
        # TM requires these headers or it blocks
        self.client.headers.update({
            "Referer": "https://www.transfermarkt.com/",
        })

    # ──────────────────────────────────────────
    # Public entry point
    # ──────────────────────────────────────────

    def fetch_all(self):
        started = datetime.utcnow()
        total = 0
        for code in settings.tracked_leagues_list:
            path = TM_LEAGUES.get(code)
            if not path:
                continue
            try:
                n = self._sync_injuries_for_league(code, path)
                total += n
            except Exception as e:
                logger.error(f"[transfermarkt] Error syncing {code}: {e}")

        self.log_sync("fetch_all", "success", records_inserted=total, started_at=started)

    # ──────────────────────────────────────────
    # Injuries per league
    # ──────────────────────────────────────────

    def _sync_injuries_for_league(self, comp_code: str, path: str) -> int:
        """Fetch the injuries page for each team in the competition."""
        with db_session() as session:
            comp = session.execute(
                select(Competition).where(Competition.code == comp_code)
            ).scalar_one_or_none()
            if not comp:
                logger.warning(f"[transfermarkt] Competition {comp_code} not in DB yet")
                return 0

            teams = [ct.team for ct in comp.teams if ct.team]

        total = 0
        for team in teams:
            try:
                n = self._sync_team_injuries(team)
                total += n
                time.sleep(settings.SCRAPE_DELAY_SECONDS)
            except Exception as e:
                logger.warning(f"[transfermarkt] Injury fetch failed for {team.name}: {e}")

        return total

    def _sync_team_injuries(self, team: Team) -> int:
        """Scrape the injury page for a single team."""
        if not team.transfermarkt_id:
            # Try to find team on TM by name
            team_path = self._search_team(team.name)
            if not team_path:
                return 0
            with db_session() as session:
                db_team = session.get(Team, team.id)
                if db_team:
                    db_team.transfermarkt_id = team_path
        else:
            team_path = team.transfermarkt_id

        injury_url = f"{TM_BASE}/{team_path}/verletzte-spieler"
        try:
            resp = self._get(injury_url)
        except Exception as e:
            logger.warning(f"[transfermarkt] Could not load injuries for {team.name}: {e}")
            return 0

        soup = BeautifulSoup(resp.text, "lxml")
        table = soup.find("table", class_="items")
        if not table:
            return 0

        inserted = 0
        for row in table.find("tbody").find_all("tr"):
            cells = row.find_all("td")
            if len(cells) < 4:
                continue

            player_name = ""
            player_link = cells[0].find("a")
            if player_link:
                player_name = player_link.get_text(strip=True)

            injury_type = cells[1].get_text(strip=True) if len(cells) > 1 else ""
            return_text = cells[-1].get_text(strip=True) if cells else ""
            expected_return = self._parse_return_date(return_text)

            if not player_name:
                continue

            with db_session() as session:
                player = self._find_or_create_player(session, player_name, team.id)
                if not player:
                    continue

                # Mark old injuries for this player as inactive
                old_injuries = session.execute(
                    select(Injury).where(
                        Injury.player_id == player.id,
                        Injury.is_active == True,
                    )
                ).scalars().all()
                for old in old_injuries:
                    old.is_active = False

                session.add(Injury(
                    player_id=player.id,
                    team_id=team.id,
                    injury_type=injury_type,
                    start_date=date.today(),
                    expected_return=expected_return,
                    is_active=True,
                    source="transfermarkt",
                ))
                inserted += 1

        return inserted

    def _parse_return_date(self, text: str) -> Optional[date]:
        """Parse expected return date from strings like '14.03.2025' or 'Unknown'."""
        if not text or text.lower() in ("unknown", "-", ""):
            return None
        formats = ["%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y"]
        for fmt in formats:
            try:
                return datetime.strptime(text.strip(), fmt).date()
            except ValueError:
                continue
        return None

    def _find_or_create_player(self, session, name: str, team_id: int) -> Optional[Player]:
        # Try exact match first
        player = session.execute(
            select(Player).where(Player.name == name, Player.team_id == team_id)
        ).scalar_one_or_none()
        if player:
            return player

        # Fuzzy match
        all_players = session.execute(
            select(Player).where(Player.team_id == team_id)
        ).scalars().all()
        from fuzzywuzzy import fuzz
        best, best_score = None, 0
        for p in all_players:
            score = fuzz.token_sort_ratio(name.lower(), p.name.lower())
            if score > best_score:
                best_score = score
                best = p
        if best and best_score >= 80:
            return best

        # Create new player record
        player = Player(team_id=team_id, name=name)
        session.add(player)
        session.flush()
        return player

    # ──────────────────────────────────────────
    # Team search on TM
    # ──────────────────────────────────────────

    def _search_team(self, team_name: str) -> Optional[str]:
        """Search TM for a team and return its URL path."""
        search_url = f"{TM_BASE}/schnellsuche/ergebnis/schnellsuche"
        try:
            resp = self._get(search_url, params={"query": team_name, "Kategorie": "Vereine"})
        except Exception as e:
            logger.warning(f"[transfermarkt] Search failed for {team_name}: {e}")
            return None

        soup = BeautifulSoup(resp.text, "lxml")
        link = soup.find("a", href=re.compile(r"/startseite/verein/\d+"))
        if link:
            return link["href"]
        return None

    # ──────────────────────────────────────────
    # Market values
    # ──────────────────────────────────────────

    def fetch_team_market_values(self, comp_code: str):
        """Scrape market values from competition overview page."""
        path = TM_LEAGUES.get(comp_code)
        if not path:
            return

        url = f"{TM_BASE}/{path}"
        try:
            resp = self._get(url)
        except Exception as e:
            logger.error(f"[transfermarkt] Market value page failed for {comp_code}: {e}")
            return

        soup = BeautifulSoup(resp.text, "lxml")
        table = soup.find("table", class_="items")
        if not table:
            return

        for row in table.find("tbody").find_all("tr"):
            cells = row.find_all("td")
            if len(cells) < 5:
                continue
            name_cell = row.find("a", title=True)
            if not name_cell:
                continue
            team_name = name_cell["title"]
            value_text = cells[-1].get_text(strip=True) if cells else ""
            value_eur = self._parse_market_value(value_text)
            if not value_eur:
                continue

            with db_session() as session:
                from ingestion.team_resolver import TeamNameResolver
                resolver = TeamNameResolver(session)
                team = resolver.resolve(team_name)
                if team:
                    team.market_value_eur = value_eur

    def _parse_market_value(self, text: str) -> Optional[float]:
        """Convert '€450.00m' or '€23.50m' to float (euros)."""
        text = text.replace(",", ".").replace("€", "").strip()
        multiplier = 1.0
        if "bn" in text.lower():
            multiplier = 1_000_000_000
            text = text.lower().replace("bn", "").strip()
        elif "m" in text.lower():
            multiplier = 1_000_000
            text = text.lower().replace("m", "").strip()
        elif "k" in text.lower():
            multiplier = 1_000
            text = text.lower().replace("k", "").strip()
        try:
            return float(text) * multiplier
        except ValueError:
            return None
