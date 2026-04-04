from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def create_app(config_path: Path, *, start_poller: bool = True) -> FastAPI:
    """Create and configure the FastAPI application.

    Set start_poller=False in tests to avoid running the poll loop.
    """
    from podcast_etl.service import load_config

    app = FastAPI(title="podcast-etl")
    app.state.config_path = config_path

    @app.get("/")
    async def dashboard(request: Request):
        config = load_config(config_path)
        return templates.TemplateResponse(request, "dashboard.html", {"config": config})

    return app
