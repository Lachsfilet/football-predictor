"""
Base class for all data ingestion connectors.
"""
import time
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional
import httpx
from loguru import logger
from config.settings import settings
from database.session import db_session
from database.models import SourceSyncLog


class BaseConnector(ABC):
    """Abstract base for all data source connectors."""

    source_name: str = "base"
    base_url: str = ""

    def __init__(self):
        self.client = httpx.Client(
            timeout=settings.REQUEST_TIMEOUT_SECONDS,
            headers=self._default_headers(),
            follow_redirects=True,
        )

    def _default_headers(self) -> dict:
        return {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.9",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }

    def _get(self, url: str, headers: Optional[dict] = None, **kwargs) -> httpx.Response:
        """GET with retry logic and rate-limit delay."""
        merged_headers = dict(self.client.headers)
        if headers:
            merged_headers.update(headers)
        for attempt in range(3):
            try:
                response = self.client.get(url, headers=merged_headers, **kwargs)
                response.raise_for_status()
                time.sleep(settings.SCRAPE_DELAY_SECONDS)
                return response
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429:
                    wait = 60 * (attempt + 1)
                    logger.warning(f"Rate limited by {self.source_name}. Waiting {wait}s")
                    time.sleep(wait)
                else:
                    raise
            except httpx.RequestError as e:
                logger.warning(f"Request error (attempt {attempt+1}/3): {e}")
                time.sleep(5 * (attempt + 1))
        raise RuntimeError(f"Failed to GET {url} after 3 attempts")

    def log_sync(
        self,
        operation: str,
        status: str,
        records_fetched: int = 0,
        records_inserted: int = 0,
        records_updated: int = 0,
        error_message: Optional[str] = None,
        started_at: Optional[datetime] = None,
    ):
        with db_session() as session:
            session.add(SourceSyncLog(
                source=self.source_name,
                operation=operation,
                status=status,
                records_fetched=records_fetched,
                records_inserted=records_inserted,
                records_updated=records_updated,
                error_message=error_message,
                started_at=started_at or datetime.utcnow(),
                finished_at=datetime.utcnow(),
            ))

    @abstractmethod
    def fetch_all(self):
        """Main entry point to pull all data from this source."""
        ...

    def close(self):
        self.client.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
