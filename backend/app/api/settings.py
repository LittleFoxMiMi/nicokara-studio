from time import monotonic
from uuid import uuid4
import httpx
from fastapi import APIRouter, HTTPException, Request
from urllib.parse import urlparse
from app.schemas.projects import AIProfilePayload, PromptPresetPayload, SettingsPayload
from app.services.pronunciation import DEFAULT_SYSTEM_PROMPT, DEFAULT_USER_TEMPLATE
from app.services.ai_client import AIClient
from app.services.secrets import SecretStore
router = APIRouter(prefix="/settings", tags=["settings"])
@router.get("")
def get_settings(request: Request):
    return request.app.state.database.settings()
@router.put("")
def save_settings(payload: SettingsPayload, request: Request):
    values = payload.values
    token_step = values.get("stable_ts_token_step")
    segment_padding = values.get("stable_ts_segment_padding_seconds")
    if token_step is not None and (
        isinstance(token_step, bool) or not isinstance(token_step, int) or token_step < 0 or token_step > 442
    ):
        raise HTTPException(422, "stable-ts token-step 必须是 0 到 442 的整数")
    if segment_padding is not None and (
        isinstance(segment_padding, bool) or not isinstance(segment_padding, (int, float)) or segment_padding < 0 or segment_padding > 30
    ):
        raise HTTPException(422, "词/短语精修 segment 扩展必须是 0 到 30 秒")
    separator_device = values.get("separator_device")
    whisper_device = values.get("whisper_device")
    providers: list[str] = []
    try:
        import onnxruntime as ort
        providers = list(ort.get_available_providers())
    except ImportError:
        pass
    if separator_device == "directml" and "DmlExecutionProvider" not in providers:
        raise HTTPException(422, "当前环境不支持 DirectML")
    if whisper_device == "directml":
        raise HTTPException(422, "faster-whisper 不支持 DirectML")
    if separator_device not in {None, "auto", "directml", "cpu"} or whisper_device not in {None, "auto", "cpu"}:
        raise HTTPException(422, "当前版本仅支持 DirectML/CPU 分离和 CPU Whisper")
    proxy_url = str(values.get("proxy_url") or "").strip()
    if values.get("proxy_enabled") and (
        not proxy_url or urlparse(proxy_url).scheme not in {"http", "https"} or not urlparse(proxy_url).netloc
    ):
        raise HTTPException(422, "代理地址必须是有效的 HTTP/HTTPS URL")
    return request.app.state.database.save_settings(values)

def _profile_response(request: Request, profile: dict) -> dict:
    db = request.app.state.database
    encrypted = db.get_ai_profile_secret(profile["id"])
    key = SecretStore(request.app.state.settings.data_dir).decrypt(encrypted)
    return {**profile, "has_api_key": bool(key), "api_key_suffix": key[-4:] if key else None}

@router.get("/ai-profiles")
def list_ai_profiles(request: Request):
    return [_profile_response(request, profile) for profile in request.app.state.database.list_ai_profiles()]

@router.post("/ai-profiles")
def create_ai_profile(payload: AIProfilePayload, request: Request):
    profile_id = str(uuid4())
    return _save_profile(profile_id, payload, request)

@router.put("/ai-profiles/{profile_id}")
def update_ai_profile(profile_id: str, payload: AIProfilePayload, request: Request):
    if not request.app.state.database.get_ai_profile(profile_id):
        raise HTTPException(404, "AI profile 不存在")
    return _save_profile(profile_id, payload, request)

def _save_profile(profile_id: str, payload: AIProfilePayload, request: Request):
    if urlparse(payload.base_url).scheme not in {"http", "https"} or not urlparse(payload.base_url).netloc:
        raise HTTPException(422, "Base URL 必须是有效的 HTTP/HTTPS 地址")
    db = request.app.state.database
    previous = db.get_ai_profile(profile_id)
    encrypted = None
    if payload.api_key:
        encrypted = SecretStore(request.app.state.settings.data_dir).encrypt(payload.api_key)
    elif not previous:
        encrypted = None
    effort = payload.thinking_effort
    # Requests from old clients only carried thinking_enabled.
    if effort == "off" and payload.thinking_enabled:
        effort = "low"
    profile = {"id": profile_id, "name": payload.name.strip(), "api_format": payload.api_format, "base_url": payload.base_url.rstrip("/"), "model": payload.model.strip(), "temperature": payload.temperature, "max_tokens": payload.max_tokens, "timeout_seconds": payload.timeout_seconds, "max_chars_per_request": payload.max_chars_per_request, "retry_count": payload.retry_count, "thinking_effort": effort, "thinking_enabled": effort != "off", "custom_payload": payload.custom_payload, "created_at": previous.get("created_at") if previous else None}
    return _profile_response(request, db.save_ai_profile(profile, encrypted))

@router.delete("/ai-profiles/{profile_id}", status_code=204)
def delete_ai_profile(profile_id: str, request: Request):
    if not request.app.state.database.delete_ai_profile(profile_id):
        raise HTTPException(404, "AI profile 不存在")

@router.delete("/ai-profiles/{profile_id}/key")
def clear_ai_profile_key(profile_id: str, request: Request):
    profile = request.app.state.database.clear_ai_profile_key(profile_id)
    if not profile:
        raise HTTPException(404, "AI profile 不存在")
    return _profile_response(request, profile)

@router.post("/ai-profiles/{profile_id}/test")
def test_ai_profile(profile_id: str, request: Request):
    db = request.app.state.database
    profile = db.get_ai_profile(profile_id)
    if not profile:
        raise HTTPException(404, "AI profile 不存在")
    key = SecretStore(request.app.state.settings.data_dir).decrypt(db.get_ai_profile_secret(profile_id))
    if not key:
        raise HTTPException(422, "AI profile 未配置密钥")
    settings = db.settings(); proxy = str(settings.get("proxy_url") or "") if settings.get("proxy_enabled", True) else None
    started = monotonic()
    try:
        raw = AIClient(profile, key, proxy).complete(DEFAULT_SYSTEM_PROMPT, '只返回 {"result":[]}')
    except (httpx.HTTPError, TimeoutError, ValueError, OSError) as exc:
        raise HTTPException(502, "连接测试失败，请检查地址、密钥和代理") from exc
    return {"ok": True, "elapsed_ms": round((monotonic() - started) * 1000), "response_shape": isinstance(raw, dict)}

@router.get("/prompt-presets")
def list_prompt_presets(request: Request):
    presets = request.app.state.database.list_prompt_presets()
    if not presets:
        presets = [{"id": "builtin-default", "name": "默认日语注音", "system_prompt": DEFAULT_SYSTEM_PROMPT, "user_template": DEFAULT_USER_TEMPLATE, "builtin": True}]
    return presets

@router.post("/prompt-presets")
def create_prompt_preset(payload: PromptPresetPayload, request: Request):
    preset = {"id": str(uuid4()), **payload.model_dump()}
    return request.app.state.database.save_prompt_preset(preset)

@router.put("/prompt-presets/{preset_id}")
def update_prompt_preset(preset_id: str, payload: PromptPresetPayload, request: Request):
    if preset_id == "builtin-default" or not request.app.state.database.get_prompt_preset(preset_id):
        raise HTTPException(404, "提示词预设不存在或不可编辑")
    return request.app.state.database.save_prompt_preset({"id": preset_id, **payload.model_dump()})

@router.delete("/prompt-presets/{preset_id}", status_code=204)
def delete_prompt_preset(preset_id: str, request: Request):
    if preset_id == "builtin-default" or not request.app.state.database.delete_prompt_preset(preset_id):
        raise HTTPException(404, "提示词预设不存在或不可删除")
@router.get("/schema")
def settings_schema():
    return {"sections": [{"id": "general", "label": "常规", "fields": [{"key": "autosave_interval_seconds", "type": "number", "min": 5, "max": 300, "scope": "global"}, {"key": "theme", "type": "enum", "values": ["system", "light", "dark"], "scope": "global"}]}, {"id": "subtitles", "label": "字幕与样式", "fields": [{"key": "font_family", "type": "text", "scope": "global"}, {"key": "font_size_max", "type": "number", "min": 12, "max": 180, "scope": "global"}]}]}
