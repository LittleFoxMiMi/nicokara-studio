from fastapi import APIRouter, Request
from app.schemas.projects import SettingsPayload
router = APIRouter(prefix="/settings", tags=["settings"])
@router.get("")
def get_settings(request: Request):
    return request.app.state.database.settings()
@router.put("")
def save_settings(payload: SettingsPayload, request: Request):
    return request.app.state.database.save_settings(payload.values)
@router.get("/schema")
def settings_schema():
    return {"sections": [{"id": "general", "label": "常规", "fields": [{"key": "autosave_interval_seconds", "type": "number", "min": 5, "max": 300, "scope": "global"}, {"key": "theme", "type": "enum", "values": ["system", "light", "dark"], "scope": "global"}]}, {"id": "subtitles", "label": "字幕与样式", "fields": [{"key": "font_family", "type": "text", "scope": "global"}, {"key": "font_size_max", "type": "number", "min": 12, "max": 180, "scope": "global"}]}]}
