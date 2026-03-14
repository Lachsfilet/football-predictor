"""
FastAPI application factory and startup.
"""
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from loguru import logger

from config.settings import settings
from database.session import init_db
from api.routes.predictions import router as predictions_router
from api.routes.data import router as data_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown lifecycle."""
    logger.info("=== Football Predictor starting ===")

    # Initialize database
    init_db()
    logger.info("Database initialized")

    # Start background scheduler
    from ingestion.scheduler import create_scheduler
    scheduler = create_scheduler()
    scheduler.start()
    logger.info("Background scheduler started")

    yield  # Application is running

    # Shutdown
    scheduler.shutdown(wait=False)
    logger.info("=== Football Predictor stopped ===")


def create_app() -> FastAPI:
    app = FastAPI(
        title="Football Match Predictor",
        description="AI-powered football match outcome prediction system",
        version="1.0.0",
        lifespan=lifespan,
    )

    # Register API routers
    app.include_router(predictions_router)
    app.include_router(data_router)

    # Serve static files (the web UI)
    static_dir = Path(__file__).parent.parent / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.get("/", include_in_schema=False)
    async def root():
        index_file = static_dir / "index.html"
        if index_file.exists():
            return FileResponse(str(index_file))
        return {"message": "Football Predictor API", "docs": "/docs"}

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "api.main:app",
        host=settings.APP_HOST,
        port=settings.APP_PORT,
        reload=settings.DEBUG,
        log_level="info",
    )
