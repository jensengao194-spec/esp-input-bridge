import asyncio
import secrets
import uuid
from contextlib import asynccontextmanager
from importlib.resources import files
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel, ConfigDict, Field

from .config import create_runner


class StartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    goal: str = Field(default="Enter Hello ESP32-C3! and submit the form", min_length=1, max_length=4000)
    demo: bool = True


def create_app(config_path="config.local.toml", execute=False, output="runs"):
    session_token = secrets.token_urlsafe(32)
    state = {"runner": None, "task": None}
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)

    @asynccontextmanager
    async def lifespan(app):
        yield
        if state["task"] and not state["task"].done():
            state["runner"].stop.set()
            try:
                await asyncio.wait_for(asyncio.shield(state["task"]), 10)
            except TimeoutError:
                state["task"].cancel()
                await asyncio.gather(state["task"], return_exceptions=True)

    app = FastAPI(title="ESP Input Bridge", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "[::1]", "testserver"])

    @app.middleware("http")
    async def local_access(request: Request, call_next):
        # No cross-origin control; reject foreign Origins even if a token is accidentally shared.
        from urllib.parse import urlsplit
        origin = request.headers.get("origin")
        if origin and urlsplit(origin).netloc != request.headers.get("host"):
            return Response("foreign origin", status_code=403)
        if request.url.path.startswith("/api/") and not secrets.compare_digest(
                request.headers.get("x-session-token", ""), session_token):
            return Response("session token required", status_code=403)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src 'self' blob:; frame-ancestors 'none'"
        return response

    @app.get("/", response_class=HTMLResponse)
    async def index():
        return files("espcomputeruse").joinpath("web/index.html").read_text().replace("__SESSION_TOKEN__", session_token)

    @app.get("/api/status")
    async def status():
        runner = state["runner"]
        return {"hardware_enabled": execute, "run": runner.state if runner else None,
                "events": runner.events[-80:] if runner else [],
                "observation_id": runner.latest.id if runner and runner.latest else None}

    @app.get("/api/image")
    async def image():
        runner = state["runner"]
        if not runner or not runner.latest:
            raise HTTPException(404, "no observation yet")
        return Response(runner.latest.jpeg(), media_type="image/jpeg")

    @app.post("/api/start")
    async def start(body: StartRequest):
        if state["task"] and not state["task"].done():
            raise HTTPException(409, "a run is already active")
        try:
            runner = create_runner(config_path, output / uuid.uuid4().hex,
                                   execute=execute, demo=body.demo)
        except Exception as exc:
            raise HTTPException(400, str(exc)) from exc
        state["runner"] = runner
        state["task"] = asyncio.create_task(runner.run(body.goal))
        return {"status": "started", "run_dir": str(runner.run_dir)}

    @app.post("/api/stop")
    async def stop():
        if state["runner"]:
            state["runner"].stop.set()
        return {"status": "stop_requested"}

    return app
