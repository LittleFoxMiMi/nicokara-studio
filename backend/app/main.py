from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from app.api.projects import router as projects_router
from app.api.settings import router as settings_router
from app.api.jobs import router as jobs_router
from app.api.capabilities import router as capabilities_router
from app.api.pronunciation import router as pronunciation_router
from app.core.config import get_settings
from app.core.database import Database
from app.services.pipeline import AnalysisPipeline, AnalysisRunner
@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings(); settings.prepare(); db = Database(settings.database_path); db.initialize(); db.mark_interrupted_jobs()
    runner = AnalysisRunner(db, AnalysisPipeline(settings, db), settings.max_background_jobs)
    app.state.settings = settings; app.state.database = db; app.state.analysis_runner = runner
    yield
    runner.shutdown()
def create_app() -> FastAPI:
    settings = get_settings(); app = FastAPI(title="Nicokara Studio", version="0.1.0", lifespan=lifespan)
    kirakara_dir = Path(__file__).resolve().parents[2] / "frontend" / "public" / "kirakara"
    if kirakara_dir.is_dir():
        app.mount("/kirakara", StaticFiles(directory=kirakara_dir), name="kirakara")
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["*"], allow_headers=["*"])
    app.include_router(projects_router, prefix=settings.api_prefix); app.include_router(settings_router, prefix=settings.api_prefix)
    app.include_router(jobs_router, prefix=settings.api_prefix); app.include_router(capabilities_router, prefix=settings.api_prefix)
    app.include_router(pronunciation_router, prefix=settings.api_prefix)
    @app.get("/health")
    def health(): return {"status": "ok", "phase": "7"}
    return app
app = create_app()
