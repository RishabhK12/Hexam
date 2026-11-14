"""Hexam REST API.

    cd backend
    .venv/Scripts/python -m uvicorn hexam.api.main:app --reload

OpenAPI docs at http://127.0.0.1:8000/docs. Route handlers stay thin; logic
lives in ``hexam.services``.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from hexam.services.dataset_service import DatasetNotFound, DatasetRegistry
from hexam.services.optimization_service import InvalidRequest, RunStore
from hexam.services.student_service import StudentNotFound

from .routes import router

log = logging.getLogger("hexam.api")


def create_app(registry: DatasetRegistry | None = None, *, load_precomputed: bool = True) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        reg = registry or DatasetRegistry()
        store = RunStore(reg)
        app.state.registry = reg
        app.state.store = store
        if load_precomputed:
            for ds_id in reg.available():
                try:
                    run = store.load_precomputed(ds_id)
                    if run:
                        log.info("loaded precomputed run %s for %s", run.run_id, ds_id)
                except Exception:  # a bad demo file must never block startup
                    log.exception("could not load precomputed run for %s", ds_id)
        yield
        store.shutdown()

    app = FastAPI(
        title="Hexam API",
        version="1.0.0",
        description="Final-exam schedule evaluation and CP-SAT optimization on synthetic Georgia Tech enrollment data.",
        lifespan=lifespan,
    )
    origins = os.environ.get("HEXAM_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",")
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["*"], allow_headers=["*"])

    @app.exception_handler(DatasetNotFound)
    async def _ds(_: Request, exc: DatasetNotFound):
        return JSONResponse(status_code=404, content={"detail": f"dataset {exc.args[0]!r} not found"})

    @app.exception_handler(StudentNotFound)
    async def _st(_: Request, exc: StudentNotFound):
        return JSONResponse(status_code=404, content={"detail": f"student {exc.args[0]!r} not found"})

    @app.exception_handler(InvalidRequest)
    async def _inv(_: Request, exc: InvalidRequest):
        return JSONResponse(status_code=422, content={"detail": exc.problems})

    app.include_router(router, prefix="/api")
    return app


app = create_app()
