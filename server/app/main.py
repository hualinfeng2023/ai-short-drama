from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.errors import install_error_handlers
from app.api.trace import trace_id_context
from app.api.v1.assets import router as assets_router
from app.api.v1.audio import router as audio_router
from app.api.v1.character_visuals import router as character_visuals_router
from app.api.v1.delivery import router as delivery_router
from app.api.v1.events import router as events_router
from app.api.v1.exports import router as exports_router
from app.api.v1.health import router as health_router
from app.api.v1.jobs import router as jobs_router
from app.api.v1.preproduction import router as preproduction_router
from app.api.v1.production import router as production_router
from app.api.v1.projects import router as projects_router
from app.api.v1.proposals import router as proposals_router
from app.api.v1.provider_settings import router as provider_settings_router
from app.api.v1.relationship_graphs import router as relationship_graphs_router
from app.api.v1.reviews import router as reviews_router
from app.api.v1.revisions import router as revisions_router
from app.api.v1.stories import router as stories_router
from app.api.v1.storyboards import router as storyboards_router
from app.api.v1.takes import router as takes_router
from app.api.v1.timelines import router as timelines_router
from app.config import get_settings
from app.jobs.worker import PersistentJobWorker


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ANN201
    settings = get_settings()
    worker: PersistentJobWorker | None = None
    if settings.job_worker_enabled:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        worker = PersistentJobWorker(settings)
        await worker.start()
    app.state.job_worker = worker
    try:
        yield
    finally:
        if worker is not None:
            await worker.stop()


app = FastAPI(
    title="AI Short Drama API",
    version="0.1.0",
    description="AI 短剧 MVP API，提供持久化工作区、任务与确定性 Mock 生成。",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5173",
        "http://localhost:5173",
        "http://127.0.0.1:4173",
        "http://127.0.0.1:5174",
        "http://localhost:5174",
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)


@app.middleware("http")
async def attach_trace_id(request: Request, call_next):  # noqa: ANN001, ANN201
    trace_id = request.headers.get("X-Trace-ID") or str(uuid4())
    token = trace_id_context.set(trace_id)
    try:
        response = await call_next(request)
        response.headers["X-Trace-ID"] = trace_id
        return response
    finally:
        trace_id_context.reset(token)


install_error_handlers(app)
app.include_router(health_router)
app.include_router(projects_router)
app.include_router(jobs_router)
app.include_router(proposals_router)
app.include_router(relationship_graphs_router)
app.include_router(stories_router)
app.include_router(storyboards_router)
app.include_router(audio_router)
app.include_router(character_visuals_router)
app.include_router(delivery_router)
app.include_router(takes_router)
app.include_router(timelines_router)
app.include_router(events_router)
app.include_router(assets_router)
app.include_router(production_router)
app.include_router(preproduction_router)
app.include_router(provider_settings_router)
app.include_router(revisions_router)
app.include_router(reviews_router)
app.include_router(exports_router)

STATIC_ROOT = Path(__file__).resolve().parent / "static"
if (STATIC_ROOT / "index.html").is_file():
    app.mount("/assets", StaticFiles(directory=STATIC_ROOT / "assets"), name="web-assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def web_app(full_path: str) -> FileResponse:
        if full_path.startswith(("api/", "health/", "meta/")):
            raise HTTPException(status_code=404, detail="Route not found")
        return FileResponse(STATIC_ROOT / "index.html")
