"""
SQLAlchemy ORM models for the football prediction system.
"""
from datetime import datetime
from typing import Optional
from sqlalchemy import (
    Column, Integer, String, Float, Boolean, DateTime, Date,
    ForeignKey, Text, UniqueConstraint, Index, JSON
)
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


# ─────────────────────────────────────────────
# Reference / Lookup tables
# ─────────────────────────────────────────────

class Competition(Base):
    __tablename__ = "competitions"

    id = Column(Integer, primary_key=True)
    code = Column(String(20), unique=True, nullable=False)   # e.g. "PL", "PD"
    name = Column(String(100), nullable=False)
    country = Column(String(60))
    tier = Column(Integer, default=1)
    current_season = Column(String(20))
    external_id = Column(String(50))                          # football-data.org id
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    teams = relationship("CompetitionTeam", back_populates="competition")
    matches = relationship("Match", back_populates="competition")
    standings = relationship("Standing", back_populates="competition")


class Team(Base):
    __tablename__ = "teams"

    id = Column(Integer, primary_key=True)
    name = Column(String(120), nullable=False)
    short_name = Column(String(60))
    tla = Column(String(10))                                  # three-letter abbreviation
    country = Column(String(60))
    founded = Column(Integer)
    stadium = Column(String(120))
    external_id = Column(String(50), unique=True)             # football-data.org id
    transfermarkt_id = Column(String(50))
    fbref_id = Column(String(50))
    market_value_eur = Column(Float)
    aliases = Column(JSON, default=list)                      # alternate names / Hebrew names
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("ix_teams_name", "name"),
        Index("ix_teams_external_id", "external_id"),
    )

    home_matches = relationship("Match", foreign_keys="Match.home_team_id", back_populates="home_team")
    away_matches = relationship("Match", foreign_keys="Match.away_team_id", back_populates="away_team")
    competition_entries = relationship("CompetitionTeam", back_populates="team")
    stats = relationship("TeamMatchStat", back_populates="team")
    players = relationship("Player", back_populates="team")


class CompetitionTeam(Base):
    """Many-to-many: team participates in a competition season."""
    __tablename__ = "competition_teams"

    id = Column(Integer, primary_key=True)
    competition_id = Column(Integer, ForeignKey("competitions.id"), nullable=False)
    team_id = Column(Integer, ForeignKey("teams.id"), nullable=False)
    season = Column(String(20), nullable=False)

    __table_args__ = (
        UniqueConstraint("competition_id", "team_id", "season"),
    )

    competition = relationship("Competition", back_populates="teams")
    team = relationship("Team", back_populates="competition_entries")


class Player(Base):
    __tablename__ = "players"

    id = Column(Integer, primary_key=True)
    team_id = Column(Integer, ForeignKey("teams.id"))
    name = Column(String(120), nullable=False)
    position = Column(String(30))
    nationality = Column(String(60))
    date_of_birth = Column(Date)
    shirt_number = Column(Integer)
    external_id = Column(String(50))
    market_value_eur = Column(Float)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    team = relationship("Team", back_populates="players")
    injuries = relationship("Injury", back_populates="player")
    suspensions = relationship("Suspension", back_populates="player")


# ─────────────────────────────────────────────
# Match data
# ─────────────────────────────────────────────

class Match(Base):
    __tablename__ = "matches"

    id = Column(Integer, primary_key=True)
    external_id = Column(String(50), unique=True)
    competition_id = Column(Integer, ForeignKey("competitions.id"))
    season = Column(String(20))
    matchday = Column(Integer)
    home_team_id = Column(Integer, ForeignKey("teams.id"), nullable=False)
    away_team_id = Column(Integer, ForeignKey("teams.id"), nullable=False)
    match_date = Column(DateTime)
    status = Column(String(30), default="SCHEDULED")         # SCHEDULED / IN_PLAY / FINISHED
    home_score = Column(Integer)
    away_score = Column(Integer)
    home_score_ht = Column(Integer)
    away_score_ht = Column(Integer)
    outcome = Column(String(10))                              # HOME / AWAY / DRAW
    venue = Column(String(120))
    referee = Column(String(100))
    attendance = Column(Integer)
    is_neutral_venue = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("ix_matches_date", "match_date"),
        Index("ix_matches_home", "home_team_id"),
        Index("ix_matches_away", "away_team_id"),
    )

    competition = relationship("Competition", back_populates="matches")
    home_team = relationship("Team", foreign_keys=[home_team_id], back_populates="home_matches")
    away_team = relationship("Team", foreign_keys=[away_team_id], back_populates="away_matches")
    team_stats = relationship("TeamMatchStat", back_populates="match")
    lineups = relationship("Lineup", back_populates="match")
    prediction = relationship("ModelPrediction", back_populates="match", uselist=False)
    engineered_features = relationship("EngineeredFeature", back_populates="match", uselist=False)


class TeamMatchStat(Base):
    """Per-team per-match statistics (shots, xG, possession, etc.)."""
    __tablename__ = "team_match_stats"

    id = Column(Integer, primary_key=True)
    match_id = Column(Integer, ForeignKey("matches.id"), nullable=False)
    team_id = Column(Integer, ForeignKey("teams.id"), nullable=False)
    is_home = Column(Boolean, nullable=False)

    # Shooting
    shots = Column(Integer)
    shots_on_target = Column(Integer)
    goals = Column(Integer)
    xg = Column(Float)
    xga = Column(Float)

    # Possession / passing
    possession = Column(Float)
    passes = Column(Integer)
    pass_accuracy = Column(Float)
    key_passes = Column(Integer)

    # Defending
    tackles = Column(Integer)
    interceptions = Column(Integer)
    clearances = Column(Integer)
    blocks = Column(Integer)

    # Set pieces / discipline
    corners = Column(Integer)
    fouls = Column(Integer)
    yellow_cards = Column(Integer)
    red_cards = Column(Integer)
    offsides = Column(Integer)

    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("match_id", "team_id"),
    )

    match = relationship("Match", back_populates="team_stats")
    team = relationship("Team", back_populates="stats")


# ─────────────────────────────────────────────
# Standings
# ─────────────────────────────────────────────

class Standing(Base):
    __tablename__ = "standings"

    id = Column(Integer, primary_key=True)
    competition_id = Column(Integer, ForeignKey("competitions.id"), nullable=False)
    team_id = Column(Integer, ForeignKey("teams.id"), nullable=False)
    season = Column(String(20), nullable=False)
    matchday = Column(Integer)
    position = Column(Integer)
    played = Column(Integer, default=0)
    won = Column(Integer, default=0)
    drawn = Column(Integer, default=0)
    lost = Column(Integer, default=0)
    goals_for = Column(Integer, default=0)
    goals_against = Column(Integer, default=0)
    goal_difference = Column(Integer, default=0)
    points = Column(Integer, default=0)
    home_won = Column(Integer, default=0)
    home_drawn = Column(Integer, default=0)
    home_lost = Column(Integer, default=0)
    away_won = Column(Integer, default=0)
    away_drawn = Column(Integer, default=0)
    away_lost = Column(Integer, default=0)
    form = Column(String(20))                                 # e.g. "WWDLW"
    recorded_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("competition_id", "team_id", "season", "matchday"),
        Index("ix_standings_team", "team_id"),
    )

    competition = relationship("Competition", back_populates="standings")
    team = relationship("Team")


# ─────────────────────────────────────────────
# Squad / Injuries / Suspensions / Lineups
# ─────────────────────────────────────────────

class Injury(Base):
    __tablename__ = "injuries"

    id = Column(Integer, primary_key=True)
    player_id = Column(Integer, ForeignKey("players.id"), nullable=False)
    team_id = Column(Integer, ForeignKey("teams.id"), nullable=False)
    injury_type = Column(String(100))
    start_date = Column(Date)
    expected_return = Column(Date)
    is_active = Column(Boolean, default=True)
    source = Column(String(60))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    player = relationship("Player", back_populates="injuries")
    team = relationship("Team")


class Suspension(Base):
    __tablename__ = "suspensions"

    id = Column(Integer, primary_key=True)
    player_id = Column(Integer, ForeignKey("players.id"), nullable=False)
    team_id = Column(Integer, ForeignKey("teams.id"), nullable=False)
    reason = Column(String(200))
    matches_remaining = Column(Integer, default=1)
    start_date = Column(Date)
    end_date = Column(Date)
    is_active = Column(Boolean, default=True)
    source = Column(String(60))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    player = relationship("Player", back_populates="suspensions")
    team = relationship("Team")


class Lineup(Base):
    __tablename__ = "lineups"

    id = Column(Integer, primary_key=True)
    match_id = Column(Integer, ForeignKey("matches.id"), nullable=False)
    team_id = Column(Integer, ForeignKey("teams.id"), nullable=False)
    player_id = Column(Integer, ForeignKey("players.id"), nullable=False)
    is_home = Column(Boolean)
    is_starter = Column(Boolean, default=True)
    position = Column(String(30))
    shirt_number = Column(Integer)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("match_id", "team_id", "player_id"),
    )

    match = relationship("Match", back_populates="lineups")
    team = relationship("Team")
    player = relationship("Player")


# ─────────────────────────────────────────────
# Head-to-head summary
# ─────────────────────────────────────────────

class HeadToHead(Base):
    __tablename__ = "head_to_head"

    id = Column(Integer, primary_key=True)
    team_a_id = Column(Integer, ForeignKey("teams.id"), nullable=False)
    team_b_id = Column(Integer, ForeignKey("teams.id"), nullable=False)
    total_matches = Column(Integer, default=0)
    team_a_wins = Column(Integer, default=0)
    team_b_wins = Column(Integer, default=0)
    draws = Column(Integer, default=0)
    team_a_goals = Column(Integer, default=0)
    team_b_goals = Column(Integer, default=0)
    last_5_results = Column(JSON, default=list)              # list of "A" / "B" / "D"
    last_match_date = Column(Date)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("team_a_id", "team_b_id"),
    )

    team_a = relationship("Team", foreign_keys=[team_a_id])
    team_b = relationship("Team", foreign_keys=[team_b_id])


# ─────────────────────────────────────────────
# Feature store
# ─────────────────────────────────────────────

class EngineeredFeature(Base):
    """Pre-computed feature vector for a match (home + away perspective)."""
    __tablename__ = "engineered_features"

    id = Column(Integer, primary_key=True)
    match_id = Column(Integer, ForeignKey("matches.id"), unique=True, nullable=False)
    computed_at = Column(DateTime, default=datetime.utcnow)

    # --- Home team rolling features ---
    home_form_last5 = Column(Float)          # points/15
    home_form_last10 = Column(Float)
    home_goals_scored_avg5 = Column(Float)
    home_goals_conceded_avg5 = Column(Float)
    home_xg_avg5 = Column(Float)
    home_xga_avg5 = Column(Float)
    home_shots_avg5 = Column(Float)
    home_sot_avg5 = Column(Float)
    home_possession_avg5 = Column(Float)
    home_pass_acc_avg5 = Column(Float)
    home_clean_sheets_last5 = Column(Integer)
    home_failed_to_score_last5 = Column(Integer)

    # --- Away team rolling features ---
    away_form_last5 = Column(Float)
    away_form_last10 = Column(Float)
    away_goals_scored_avg5 = Column(Float)
    away_goals_conceded_avg5 = Column(Float)
    away_xg_avg5 = Column(Float)
    away_xga_avg5 = Column(Float)
    away_shots_avg5 = Column(Float)
    away_sot_avg5 = Column(Float)
    away_possession_avg5 = Column(Float)
    away_pass_acc_avg5 = Column(Float)
    away_clean_sheets_last5 = Column(Integer)
    away_failed_to_score_last5 = Column(Integer)

    # --- Home / Away split features ---
    home_home_win_rate = Column(Float)       # win% when playing at home
    home_home_goals_avg = Column(Float)
    away_away_win_rate = Column(Float)       # win% when playing away
    away_away_goals_avg = Column(Float)
    away_away_conceded_avg = Column(Float)

    # --- League context ---
    home_league_position = Column(Integer)
    away_league_position = Column(Integer)
    home_points_per_game = Column(Float)
    away_points_per_game = Column(Float)
    position_diff = Column(Integer)

    # --- Squad availability ---
    home_injured_count = Column(Integer, default=0)
    away_injured_count = Column(Integer, default=0)
    home_suspended_count = Column(Integer, default=0)
    away_suspended_count = Column(Integer, default=0)
    home_squad_market_value = Column(Float)
    away_squad_market_value = Column(Float)

    # --- Schedule / fatigue ---
    home_days_since_last_match = Column(Integer)
    away_days_since_last_match = Column(Integer)
    home_matches_last7 = Column(Integer, default=0)
    away_matches_last7 = Column(Integer, default=0)

    # --- Head-to-head ---
    h2h_home_win_rate = Column(Float)
    h2h_draw_rate = Column(Float)
    h2h_goals_avg = Column(Float)

    # --- xG trend ---
    home_xg_trend = Column(Float)            # slope of xG over last 5
    away_xg_trend = Column(Float)
    home_xga_trend = Column(Float)
    away_xga_trend = Column(Float)

    # --- Derived differentials ---
    xg_diff = Column(Float)
    form_diff = Column(Float)
    league_pos_diff = Column(Integer)

    match = relationship("Match", back_populates="engineered_features")


# ─────────────────────────────────────────────
# Predictions
# ─────────────────────────────────────────────

class ModelPrediction(Base):
    __tablename__ = "model_predictions"

    id = Column(Integer, primary_key=True)
    match_id = Column(Integer, ForeignKey("matches.id"), unique=True)
    model_version = Column(String(50))
    predicted_outcome = Column(String(10))           # HOME / DRAW / AWAY
    prob_home = Column(Float)
    prob_draw = Column(Float)
    prob_away = Column(Float)
    confidence = Column(Float)
    shap_explanation = Column(JSON)                  # top feature contributions
    key_factors = Column(JSON)                       # human-readable bullets
    created_at = Column(DateTime, default=datetime.utcnow)

    match = relationship("Match", back_populates="prediction")


# ─────────────────────────────────────────────
# Operational / audit
# ─────────────────────────────────────────────

class SourceSyncLog(Base):
    __tablename__ = "source_sync_logs"

    id = Column(Integer, primary_key=True)
    source = Column(String(60), nullable=False)       # "football_data_org" / "fbref" / etc.
    operation = Column(String(100))
    status = Column(String(20))                       # "success" / "error" / "partial"
    records_fetched = Column(Integer, default=0)
    records_inserted = Column(Integer, default=0)
    records_updated = Column(Integer, default=0)
    error_message = Column(Text)
    started_at = Column(DateTime)
    finished_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
