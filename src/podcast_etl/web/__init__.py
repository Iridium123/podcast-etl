from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.templating import Jinja2Templates

from podcast_etl.poller import PollControl, async_poll_loop
from podcast_etl.service import load_config

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def create_app(config_path: Path, *, start_poller: bool = True) -> FastAPI:
    """Create and configure the FastAPI application.

    Set start_poller=False in tests to avoid running the poll loop.
    """
    poll_control = PollControl()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        task = None
        if start_poller:
            config = load_config(config_path)
            task = asyncio.create_task(async_poll_loop(config, config_path, poll_control))
        yield
        poll_control.shutdown.set()
        if task:
            await task

    app = FastAPI(title="podcast-etl", lifespan=lifespan)
    app.state.config_path = config_path
    app.state.poll_control = poll_control

    from podcast_etl.web.routes.dashboard import router as dashboard_router
    from podcast_etl.web.routes.defaults import router as defaults_router
    from podcast_etl.web.routes.feeds import router as feeds_router

    app.include_router(dashboard_router)
    app.include_router(feeds_router)
    app.include_router(defaults_router)

    return app
