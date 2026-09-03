from pathlib import Path
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="NICOKARA_", env_file=".env", extra="ignore")
    data_dir: Path = Path("storage")
    api_prefix: str = "/api"
    ffprobe_path: str = "ffprobe"
    ffmpeg_path: str = "ffmpeg"
    chrome_path: str = ""
    export_base_url: str = "http://127.0.0.1:8100"
    allowed_origins: str = "http://localhost:5173,http://localhost:3200"
    max_video_bytes: int = 4 * 1024 * 1024 * 1024
    max_background_jobs: int = 1
    separator_model: str = "UVR_MDXNET_KARA_2.onnx"
    separator_device: str = "auto"
    whisper_model: str = "large-v3"
    whisper_device: str = "auto"
    whisper_compute_type: str = "int8"
    @property
    def database_path(self) -> Path:
        return self.data_dir / "nicokara.sqlite3"
    @property
    def projects_dir(self) -> Path:
        return self.data_dir / "projects"
    @property
    def models_dir(self) -> Path:
        return self.data_dir / "models"
    @property
    def cors_origins(self) -> list[str]:
        return [x.strip() for x in self.allowed_origins.split(",") if x.strip()]
    def prepare(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.projects_dir.mkdir(parents=True, exist_ok=True)
        self.models_dir.mkdir(parents=True, exist_ok=True)

@lru_cache
def get_settings() -> Settings:
    return Settings()
