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

# Minimum key length to include in fuzzy-match candidates.
# TLA abbreviations (3 chars or fewer) are excluded to prevent false positives
# such as "brest" matching Brentford's TLA "bre" at score 75.
_FUZZY_MIN_KEY_LEN = 4

# Fuzzy-match score threshold.  Raised from 75 to 85 so that short or
# overlapping strings (e.g. "brest"/"bre", "paris"/"arsenal") no longer
# produce wrong mappings.
_FUZZY_THRESHOLD = 85

# Known aliases: maps alternate/Hebrew names → canonical English name fragments
#
# Values should be the official football-data.org team name (or a form that
# produces the correct DB cache key after normalize_name()).  This covers all
# teams in the five tracked leagues (PL, PD, BL1, SA, FL1) as returned by
# soccerdata / Understat, which often uses shorter names than the official ones.
TEAM_ALIASES: Dict[str, str] = {
    # ── Hebrew → English ────────────────────────────────────────────────────
    "ליברפול": "Liverpool FC",
    "ארסנל": "Arsenal FC",
    "מנצ'סטר סיטי": "Manchester City FC",
    "מנצ'סטר יונייטד": "Manchester United FC",
    "צ'לסי": "Chelsea FC",
    "טוטנהאם": "Tottenham Hotspur FC",
    "ניוקאסל": "Newcastle United FC",
    "ברייטון": "Brighton & Hove Albion FC",
    "אסטון וילה": "Aston Villa FC",
    "ווסטהאם": "West Ham United FC",
    "ברצלונה": "FC Barcelona",
    "ריאל מדריד": "Real Madrid CF",
    "אטלטיקו מדריד": "Club Atlético de Madrid",
    "סביליה": "Sevilla FC",
    "ולנסיה": "Valencia CF",
    "בילבאו": "Athletic Club",
    "ביירן מינכן": "FC Bayern München",
    "בורוסיה דורטמונד": "Borussia Dortmund",
    "לייפציג": "RB Leipzig",
    "יובנטוס": "Juventus FC",
    "אינטר מילאן": "FC Internazionale Milano",
    "אינטר": "FC Internazionale Milano",
    "מילאן": "AC Milan",
    "רומא": "AS Roma",
    "נאפולי": "SSC Napoli",
    "פריז סן ז'רמן": "Paris Saint-Germain FC",
    "פ.ס.ז'": "Paris Saint-Germain FC",
    "מונאקו": "AS Monaco",
    "ליון": "Olympique Lyonnais",
    "מרסיי": "Olympique de Marseille",

    # ── Common English abbreviations / short forms ───────────────────────────
    "man city": "Manchester City FC",
    "man utd": "Manchester United FC",
    "man united": "Manchester United FC",
    "spurs": "Tottenham Hotspur FC",
    "tottenham": "Tottenham Hotspur FC",
    "newcastle": "Newcastle United FC",
    "west ham": "West Ham United FC",
    "barca": "FC Barcelona",
    "atletico": "Club Atlético de Madrid",
    "inter": "FC Internazionale Milano",
    "inter milan": "FC Internazionale Milano",
    "ac milan": "AC Milan",
    "paris sg": "Paris Saint-Germain FC",
    "dortmund": "Borussia Dortmund",
    "bvb": "Borussia Dortmund",
    "bayern": "FC Bayern München",
    "bayern munich": "FC Bayern München",
    "fcb": "FC Bayern München",
    "rb leipzig": "RB Leipzig",
    "leipzig": "RB Leipzig",
    "ajax": "AFC Ajax",
    "porto": "FC Porto",
    "benfica": "SL Benfica",
    "celtic": "Celtic FC",
    "rangers": "Rangers FC",
    "galatasaray": "Galatasaray SK",
    "fenerbahce": "Fenerbahçe SK",
    "besiktas": "Beşiktaş JK",

    # ── Premier League (soccerdata/Understat names → football-data.org) ──────
    "bournemouth": "AFC Bournemouth",
    "brighton": "Brighton & Hove Albion FC",
    "leicester": "Leicester City FC",
    "luton": "Luton Town FC",
    "sheffield utd": "Sheffield United FC",
    "sheffield united": "Sheffield United FC",
    "wolves": "Wolverhampton Wanderers FC",
    "wolverhampton": "Wolverhampton Wanderers FC",
    "ipswich": "Ipswich Town FC",
    "nottingham forest": "Nottingham Forest FC",
    "west brom": "West Bromwich Albion FC",
    "west bromwich": "West Bromwich Albion FC",
    "qpr": "Queens Park Rangers FC",
    "swansea": "Swansea City AFC",
    "cardiff": "Cardiff City FC",
    "stoke": "Stoke City FC",
    "hull": "Hull City AFC",
    "middlesbrough": "Middlesbrough FC",
    "sunderland": "Sunderland AFC",

    # ── La Liga (soccerdata/Understat names → football-data.org) ────────────
    "real madrid": "Real Madrid CF",
    "barcelona": "FC Barcelona",
    "atletico madrid": "Club Atlético de Madrid",
    "sevilla": "Sevilla FC",
    "villarreal": "Villarreal CF",
    "real betis": "Real Betis Balompié",
    "real sociedad": "Real Sociedad de Fútbol",
    "athletic club": "Athletic Club",
    "athletic bilbao": "Athletic Club",
    "valencia": "Valencia CF",
    "osasuna": "CA Osasuna",
    "celta vigo": "RC Celta de Vigo",
    "celta": "RC Celta de Vigo",
    "getafe": "Getafe CF",
    "espanyol": "RCD Espanyol de Barcelona",
    "girona": "Girona FC",
    "las palmas": "UD Las Palmas",
    "rayo vallecano": "Rayo Vallecano de Madrid",
    "alaves": "Deportivo Alavés",
    "deportivo alaves": "Deportivo Alavés",
    "mallorca": "RCD Mallorca",
    "leganes": "CD Leganés",
    "valladolid": "Real Valladolid CF",
    "granada": "Granada CF",
    "cadiz": "Cádiz CF",
    "almeria": "UD Almería",
    "levante": "Levante UD",

    # ── Bundesliga (soccerdata/Understat names → football-data.org) ──────────
    "bayer leverkusen": "Bayer 04 Leverkusen",
    "union berlin": "1. FC Union Berlin",
    "freiburg": "Sport-Club Freiburg",
    "wolfsburg": "VfL Wolfsburg",
    "mainz": "1. FSV Mainz 05",
    "borussia monchengladbach": "Borussia Mönchengladbach",
    "gladbach": "Borussia Mönchengladbach",
    "cologne": "1. FC Köln",
    "koln": "1. FC Köln",
    "hoffenheim": "TSG 1899 Hoffenheim",
    "tsg hoffenheim": "TSG 1899 Hoffenheim",
    "werder bremen": "SV Werder Bremen",
    "werder": "SV Werder Bremen",
    "augsburg": "FC Augsburg",
    "stuttgart": "VfB Stuttgart",
    "hertha": "Hertha BSC",
    "hertha berlin": "Hertha BSC",
    "schalke": "FC Schalke 04",
    "hamburg": "Hamburger SV",
    "hsv": "Hamburger SV",
    "bochum": "VfL Bochum 1848",
    "heidenheim": "1. FC Heidenheim 1846",
    "holstein kiel": "Holstein Kiel",
    "st. pauli": "FC St. Pauli",
    "st pauli": "FC St. Pauli",
    "darmstadt": "SV Darmstadt 98",
    "paderborn": "SC Paderborn 07",
    "eintracht frankfurt": "Eintracht Frankfurt",
    "frankfurt": "Eintracht Frankfurt",

    # ── Serie A (soccerdata/Understat names → football-data.org) ────────────
    "juventus": "Juventus FC",
    "napoli": "SSC Napoli",
    "milan": "AC Milan",
    "roma": "AS Roma",
    "lazio": "SS Lazio",
    "atalanta": "Atalanta BC",
    "fiorentina": "ACF Fiorentina",
    "torino": "Torino FC",
    "verona": "Hellas Verona FC",
    "hellas verona": "Hellas Verona FC",
    "bologna": "Bologna FC 1909",
    "udinese": "Udinese Calcio",
    "sampdoria": "UC Sampdoria",
    "sassuolo": "US Sassuolo Calcio",
    "cagliari": "Cagliari Calcio",
    "empoli": "Empoli FC",
    "spezia": "Spezia Calcio",
    "venezia": "Venezia FC",
    "lecce": "US Lecce",
    "monza": "AC Monza",
    "como": "Como 1907",
    "parma": "Parma Calcio 1913",
    "genoa": "Genoa CFC",
    "cremonese": "US Cremonese",
    "frosinone": "Frosinone Calcio",
    "salernitana": "US Salernitana 1919",
    "benevento": "Benevento Calcio",
    "brescia": "Brescia Calcio",

    # ── Ligue 1 (soccerdata/Understat names → football-data.org) ────────────
    "monaco": "AS Monaco",
    "as monaco": "AS Monaco",
    "lille": "LOSC Lille",
    "losc lille": "LOSC Lille",
    "losc": "LOSC Lille",
    "nice": "OGC Nice",
    "ogc nice": "OGC Nice",
    "lens": "RC Lens",
    "rc lens": "RC Lens",
    "strasbourg": "RC Strasbourg Alsace",
    "rc strasbourg": "RC Strasbourg Alsace",
    "nantes": "FC Nantes",
    "montpellier": "Montpellier HSC",
    "brest": "Stade Brestois 29",
    "stade brestois": "Stade Brestois 29",
    "rennes": "Stade Rennais FC 1901",
    "stade rennais": "Stade Rennais FC 1901",
    "toulouse": "Toulouse FC",
    "reims": "Stade de Reims",
    "stade de reims": "Stade de Reims",
    "lorient": "FC Lorient",
    "metz": "FC Metz",
    "clermont": "Clermont Foot 63",
    "clermont foot": "Clermont Foot 63",
    "angers": "Angers SCO",
    "le havre": "Le Havre AC",
    "saint-etienne": "AS Saint-Étienne",
    "st etienne": "AS Saint-Étienne",
    "st-etienne": "AS Saint-Étienne",
    "auxerre": "AJ Auxerre",
    "aj auxerre": "AJ Auxerre",
    "paris fc": "Paris FC",
    "paris saint germain": "Paris Saint-Germain FC",
    "paris saint-germain": "Paris Saint-Germain FC",
    "psg": "Paris Saint-Germain FC",
    "guingamp": "En Avant de Guingamp",
    "caen": "SM Caen",
    "dijon": "Dijon FCO",
    "troyes": "ESTAC Troyes",
    "bordeaux": "FC Girondins de Bordeaux",
    "lyon": "Olympique Lyonnais",
    "marseille": "Olympique de Marseille",
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
        # Exclude very short keys (TLA abbreviations, ≤3 chars) from candidates
        # to prevent false positives such as "brest" matching TLA "bre" at
        # score 75 and resolving to the wrong team entirely.
        if all_teams:
            candidates = [k for k in all_teams if len(k) >= _FUZZY_MIN_KEY_LEN]
            if candidates:
                match, score = process.extractOne(norm, candidates, scorer=fuzz.token_sort_ratio)
                if score >= _FUZZY_THRESHOLD:
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
