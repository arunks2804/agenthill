"""AgentHill FastAPI application: API + built React frontend."""
from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .api.routes import router
from .config import FRONTEND_DIST, SecretScrubber
from .db import SessionLocal, init_engine
from .platform_settings import CREDENTIALS
from .services import ensure_tenants

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
for h in logging.getLogger().handlers:
    h.addFilter(SecretScrubber(CREDENTIALS.all_secrets))

init_engine()
_db = SessionLocal()
ensure_tenants(_db)
_db.close()

app = FastAPI(title="AgentHill", version="1.0.0",
              description="AI Agent Evaluation and Continuous Improvement Platform")
app.include_router(router)


@app.get("/api/health")
def health():
    return {"status": "ok"}


if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        f = FRONTEND_DIST / path
        if path and f.is_file():
            return FileResponse(f)
        return FileResponse(FRONTEND_DIST / "index.html")
