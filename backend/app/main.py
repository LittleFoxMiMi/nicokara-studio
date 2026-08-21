from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.projects import router as projects_router
from app.api.settings import router as settings_router
from app.core.config import get_settings
from app.core.database import Database
@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings(); settings.prepare(); db = Database(settings.database_path); db.initialize(); app.state.settings = settings; app.state.database = db; yield
def create_app() -> FastAPI:
    settings = get_settings(); app = FastAPI(title="Nicokara Studio", version="0.1.0", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["*"], allow_headers=["*"])
    app.include_router(projects_router, prefix=settings.api_prefix); app.include_router(settings_router, prefix=settings.api_prefix)
    @app.get("/health")
    def health(): return {"status": "ok", "phase": "2"}
    return app
app = create_app()
