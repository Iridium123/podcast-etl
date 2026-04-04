from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def create_app(config_path: Path, *, start_poller: bool = True) -> FastAPI:
    """Create and configure the FastAPI application.

    Set start_poller=False in tests to avoid running the poll loop.
    """
    from podcast_etl.web.routes.dashboard import router as dashboard_router
    from podcast_etl.web.routes.feeds import router as feeds_router

    app = FastAPI(title="podcast-etl")
    app.state.config_path = config_path

    app.include_router(dashboard_router)
    app.include_router(feeds_router)

    return app
