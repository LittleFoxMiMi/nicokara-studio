from typing import Any, Literal
from pydantic import BaseModel, Field
class ProjectCreate(BaseModel): name: str = Field(min_length=1,max_length=120)
class ProjectPatch(BaseModel): name: str|None = Field(default=None,min_length=1,max_length=120); title: str|None=None; artist: str|None=None
class DocumentSave(BaseModel): revision: int = Field(ge=1); document: dict[str,Any]
class SettingsPayload(BaseModel): values: dict[str,Any]
class ProjectResponse(BaseModel): id: str; name: str; title: str; artist: str; status: str; created_at: str; updated_at: str; revision: int; deleted_at: str|None=None

class LyricsDetect(BaseModel):
    content: str = Field(min_length=1, max_length=2_000_000)
    filename: str | None = Field(default=None, max_length=255)

class LyricsImport(LyricsDetect):
    revision: int = Field(ge=1)
    format: Literal["auto", "text", "lrc", "krl"] = "auto"

class AnalysisRequest(BaseModel):
    revision: int = Field(ge=1)
    line_ids: list[str] = Field(default_factory=list, max_length=5000)
    unit_ids: list[str] = Field(default_factory=list, max_length=20000)
    start_ms: int | None = Field(default=None, ge=0)
    end_ms: int | None = Field(default=None, ge=0)
    overwrite_policy: Literal["unlocked_only", "all", "create_revision"] = "unlocked_only"
    preserve_line_anchors: bool = True
    model: Literal["small", "medium", "turbo", "large-v3"] | str | None = None
    device: Literal["auto", "cpu"] | None = None
    compute_type: str | None = None

class FAKaraRequest(BaseModel):
    revision: int = Field(ge=1)
    line_ids: list[str] = Field(default_factory=list, max_length=5000)
    overwrite_policy: Literal["unlocked_only", "all"] = "unlocked_only"
    model: Literal["mms", "yohane"] | None = None

class FullAnalysisRequest(AnalysisRequest):
    steps: list[Literal["separation", "transcription", "pronunciation", "global_alignment", "alignment", "fa_kara"]] = Field(
        default_factory=lambda: ["separation", "transcription", "pronunciation", "fa_kara"],
        max_length=5,
    )
    alignment_backend: Literal["fa_kara", "stable_ts"] | None = None
    fa_kara_model: Literal["mms", "yohane"] | None = None
    profile_id: str | None = None

class SeparationRequest(BaseModel):
    revision: int = Field(ge=1)
    device: Literal["auto", "directml", "cpu"] = "auto"
    model: str | None = Field(default=None, max_length=255)

class PronunciationRequest(BaseModel):
    revision: int = Field(ge=1)
    line_ids: list[str] = Field(default_factory=list, max_length=5000)
    unit_ids: list[str] = Field(default_factory=list, max_length=20000)
    overwrite_policy: Literal["unlocked_only", "all"] = "unlocked_only"
    mode: Literal["local", "ai"] = "local"
    profile_id: str | None = None

class ExportRequest(BaseModel):
    revision: int = Field(ge=1)
    format: Literal["mp4", "webm"] = "mp4"
    audio_track: Literal["on_vocal", "off_vocal"] = "on_vocal"

class AIProfilePayload(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    api_format: Literal["openai_chat", "openai_responses", "anthropic_messages"] = "openai_chat"
    base_url: str = Field(min_length=1, max_length=500)
    model: str = Field(min_length=1, max_length=200)
    api_key: str | None = Field(default=None, max_length=1000)
    temperature: float = Field(default=0.2, ge=0, le=2)
    max_tokens: int = Field(default=2000, ge=128, le=100000)
    timeout_seconds: float = Field(default=45, ge=5, le=300)
    max_chars_per_request: int = Field(default=1200, ge=100, le=20000)
    retry_count: int = Field(default=2, ge=0, le=10)
    thinking_effort: Literal["off", "minimal", "low", "medium", "high", "xhigh"] = "off"
    # Kept for clients created before the thinking-effort selector existed.
    thinking_enabled: bool = False
    custom_payload: dict[str, Any] = Field(default_factory=dict)

class PromptPresetPayload(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    system_prompt: str = Field(min_length=1, max_length=20000)
    user_template: str = Field(min_length=1, max_length=50000)
