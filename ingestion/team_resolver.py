"""
Team name resolution and normalization.
Supports English and Hebrew team names with fuzzy matching.
"""
from typing import Optional, Dict, List
from sqlalchemy.orm import Session
from sqlalchemy import select
from unidecode import unidecode
from fuzzywuzzy import fuzz, process
from loguru import logger

from database.models import Team

# Known aliases: maps alternate/Hebrew names → canonical English name fragments
TEAM_ALIASES: Dict[str, str] = {
    # Hebrew → English
    "ליברפול": "Liverpool",
    "ארסנל": "Arsenal",
    "מנצ'סטר סיטי": "Manchester City",
    "מנצ'סטר יונייטד": "Manchester United",
    "צ'לסי": "Chelsea",
    "טוטנהאם": "Tottenham Hotspur",
    "ניוקאסל": "Newcastle United",
    "ברייטון": "Brighton",
    "אסטון וילה": "Aston Villa",
    "ווסטהאם": "West Ham United",
    "ברצלונה": "Barcelona",
    "ריאל מדריד": "Real Madrid",
    "אטלטיקו מדריד": "Atletico Madrid",
    "סביליה": "Sevilla",
    "ולנסיה": "Valencia",
    "בילבאו": "Athletic Club",
    "ביירן מינכן": "Bayern Munich",
    "בורוסיה דורטמונד": "Borussia Dortmund",
    "לייפציג": "RB Leipzig",
    "יובנטוס": "Juventus",
    "אינטר מילאן": "Internazionale",
    "אינטר": "Internazionale",
    "מילאן": "AC Milan",
    "רומא": "AS Roma",
    "נאפולי": "Napoli",
    "פריז סן ז'רמן": "Paris Saint-Germain",
    "פ.ס.ז'": "Paris Saint-Germain",
    "מונאקו": "AS Monaco",
    "ליון": "Olympique Lyonnais",
    "מרסיי": "Olympique de Marseille",

    # Common English abbreviations / variants
    "man city": "Manchester City",
    "man utd": "Manchester United",
    "man united": "Manchester United",
    "spurs": "Tottenham Hotspur",
    "tottenham": "Tottenham Hotspur",
    "newcastle": "Newcastle United",
    "west ham": "West Ham United",
    "barca": "Barcelona",
    "atletico": "Atletico Madrid",
    "atletico madrid": "Atletico Madrid",
    "inter": "Internazionale",
    "inter milan": "Internazionale",
    "ac milan": "AC Milan",
    "psg": "Paris Saint-Germain",
    "paris sg": "Paris Saint-Germain",
    "paris saint germain": "Paris Saint-Germain",
    "lyon": "Olympique Lyonnais",
    "marseille": "Olympique de Marseille",
    "dortmund": "Borussia Dortmund",
    "bvb": "Borussia Dortmund",
    "bayern": "Bayern Munich",
    "fcb": "Bayern Munich",
    "rb leipzig": "RB Leipzig",
    "leipzig": "RB Leipzig",
    "ajax": "Ajax",
    "porto": "FC Porto",
    "benfica": "SL Benfica",
    "celtic": "Celtic",
    "rangers": "Rangers",
    "galatasaray": "Galatasaray",
    "fenerbahce": "Fenerbahçe",
    "besiktas": "Beşiktaş",
}


def normalize_name(name: str) -> str:
    """Lowercase, strip accents, remove punctuation."""
    name = name.strip().lower()
    name = unidecode(name)
    # Remove common suffixes that vary across sources
    for suffix in [" fc", " cf", " sc", " afc", " fk", " sk", " ac", " as"]:
        if name.endswith(suffix):
            name = name[:-len(suffix)].strip()
    return name


class TeamNameResolver:
    """
    Resolves a free-text team name (English or Hebrew) to a Team DB record.
    Uses:
      1. Alias lookup table
      2. Exact normalized match
      3. Fuzzy match against all team names in DB
    """

    def __init__(self, session: Session):
        self.session = session
        self._cache: Optional[Dict[str, Team]] = None

    def _load_all_teams(self) -> Dict[str, Team]:
        if self._cache is not None:
            return self._cache
        teams = self.session.execute(select(Team)).scalars().all()
        self._cache = {}
        for t in teams:
            key = normalize_name(t.name)
            self._cache[key] = t
            if t.short_name:
                self._cache[normalize_name(t.short_name)] = t
            if t.tla:
                self._cache[t.tla.lower()] = t
            # Load stored aliases
            for alias in (t.aliases or []):
                self._cache[normalize_name(alias)] = t
        return self._cache

    def resolve(self, name: str) -> Optional[Team]:
        if not name or not name.strip():
            return None

        # 1. Check alias table (translate to canonical English)
        lower = name.strip().lower()
        canonical = TEAM_ALIASES.get(name.strip()) or TEAM_ALIASES.get(lower)
        lookup_name = canonical if canonical else name

        # 2. Exact normalized match
        norm = normalize_name(lookup_name)
        all_teams = self._load_all_teams()
        if norm in all_teams:
            return all_teams[norm]

        # 3. Fuzzy match
        if all_teams:
            candidates = list(all_teams.keys())
            match, score = process.extractOne(norm, candidates, scorer=fuzz.token_sort_ratio)
            if score >= 75:
                logger.debug(f"Fuzzy matched '{name}' → '{all_teams[match].name}' (score={score})")
                return all_teams[match]

        logger.warning(f"Could not resolve team name: '{name}'")
        return None

    def add_alias(self, team_id: int, alias: str):
        """Persist a new alias for a team."""
        team = self.session.get(Team, team_id)
        if team:
            aliases = list(team.aliases or [])
            if alias not in aliases:
                aliases.append(alias)
                team.aliases = aliases
            # Also update cache
            if self._cache is not None:
                self._cache[normalize_name(alias)] = team
