"""
Central configuration management using pydantic-settings.
All settings can be overridden via environment variables or .env file.
"""
from pathlib import Path
from typing import List
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Database
    DATABASE_URL: str = f"sqlite:///{BASE_DIR}/data/football.db"

    # API keys
    FOOTBALL_DATA_API_KEY: str = ""

    # Application
    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8000
    DEBUG: bool = False

    # Data update schedule
    UPDATE_SCHEDULE: str = "0 3 * * *"

    # Leagues to track
    TRACKED_LEAGUES: str = "PL,PD,BL1,SA,FL1"

    # Scraping
    SCRAPE_DELAY_SECONDS: float = 3.0
    REQUEST_TIMEOUT_SECONDS: int = 30

    # Paths
    DATA_DIR: Path = BASE_DIR / "data"
    MODELS_DIR: Path = BASE_DIR / "data" / "models"
    LOGS_DIR: Path = BASE_DIR / "logs"

    @property
    def tracked_leagues_list(self) -> List[str]:
        return [x.strip() for x in self.TRACKED_LEAGUES.split(",") if x.strip()]

    def ensure_dirs(self):
        self.DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.MODELS_DIR.mkdir(parents=True, exist_ok=True)
        self.LOGS_DIR.mkdir(parents=True, exist_ok=True)


settings = Settings()
settings.ensure_dirs()
