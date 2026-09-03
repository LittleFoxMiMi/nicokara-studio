from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Callable

from app.core.config import Settings
from app.core.defaults import DEFAULT_SUBTITLE_STYLE


class ExportError(RuntimeError):
    pass


class ExportCanceled(ExportError):
    pass


def _timestamp(ms: int | float) -> str:
    value = max(0, round(ms))
    return f"[{value // 60000:02d}:{(value % 60000) // 1000:02d}.{(value % 1000) // 10:02d}]"


def _escape(value: object) -> str:
    return str(value or "").replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")


def document_to_lrc(document: dict) -> str:
    rows: list[str] = []
    for line in document.get("lyrics", {}).get("lines", []):
        units = [unit for unit in line.get("units", []) if unit.get("surface")]
        current_role = ""
        body: list[str] = []
        index = 0
        timeline_cursor = line.get("start_ms") or 0
        while index < len(units):
            unit = units[index]
            raw_start = unit.get("start_ms")
            if raw_start is None and str(unit.get("surface") or "").isspace():
                start = timeline_cursor
            else:
                start = raw_start if raw_start is not None else line.get("start_ms") or 0
            end = unit.get("end_ms") if unit.get("end_ms") is not None else line.get("end_ms") or int(start) + 500
            role = "+".join(str(item) for item in unit.get("roles", []) if item)
            prefix = f"【@{role}】" if role and role != current_role else ""
            current_role = role or current_role
            ruby = unit.get("ruby") or unit.get("ruby_2")
            ruby_text = f"{unit.get('ruby') or ''}>{unit.get('ruby_2')}" if unit.get("ruby_2") else str(unit.get("ruby") or "")
            if ruby:
                # A ruby annotation belongs to the complete surface range. Older
                # projects may repeat the same reading on each unit, so fold both
                # the explicit ruby_span and contiguous duplicate readings into a
                # single KRL token. Kirakara then assigns the reading once to the
                # first character and uses ruby_span for the remaining characters.
                try:
                    raw_span = unit.get("ruby_span")
                    target_chars = max(1, int(raw_span or 1))
                except (TypeError, ValueError):
                    raw_span = None
                    target_chars = 1
                member_end = index + 1
                char_count = len(str(unit.get("surface") or ""))
                while target_chars > 1 and char_count < target_chars and member_end < len(units):
                    char_count += len(str(units[member_end].get("surface") or ""))
                    member_end += 1
                while target_chars <= 1 and member_end < len(units):
                    next_unit = units[member_end]
                    if next_unit.get("ruby") != unit.get("ruby") or next_unit.get("ruby_2") != unit.get("ruby_2"):
                        break
                    member_end += 1
                members = units[index:member_end]
                grouped_surface = _escape("".join(str(member.get("surface") or "") for member in members))
                body.append(f"{prefix}{_timestamp(start)}{{{grouped_surface}|{_escape(ruby_text)}}}")
                member_ends = [member.get("end_ms") for member in members if member.get("end_ms") is not None]
                timeline_cursor = max(timeline_cursor, max(member_ends) if member_ends else start)
                index += len(members)
                continue
            surface = _escape(unit.get("surface"))
            if len(str(unit.get("surface"))) == 1:
                body.append(f"{prefix}{_timestamp(start)}{surface}")
            else:
                chars = list(str(unit.get("surface")))
                body.extend(f"{prefix if char_index == 0 else ''}{_timestamp(start + (end - start) * char_index / len(chars))}{_escape(char)}" for char_index, char in enumerate(chars))
            timeline_cursor = max(timeline_cursor, unit.get("end_ms") if unit.get("end_ms") is not None else start)
            index += 1
        fallback_end = line.get("end_ms") if line.get("end_ms") is not None else (line.get("start_ms") or 0) + 500
        rows.append("".join(body) + _timestamp(fallback_end))
    return "\n".join(rows)


def document_to_config(document: dict) -> dict:
    raw = document.get("styles") if isinstance(document.get("styles"), dict) else {}
    defaults = DEFAULT_SUBTITLE_STYLE
    def number(key: str, fallback: float) -> float:
        try:
            value = float(raw.get(key, fallback))
            return value if value == value else fallback
        except (TypeError, ValueError):
            return fallback
    font_size = number("fontSizeMax", defaults["fontSizeMax"])
    profiles = {}
    for line in document.get("lyrics", {}).get("lines", []):
        for unit in line.get("units", []):
            for role in unit.get("roles", []):
                profiles[str(role)] = {"colorBefore": raw.get("textColor", defaults["textColor"]), "colorAfter": raw.get("activeColor", defaults["activeColor"]), "strokeColorBefore": raw.get("outlineColor", defaults["outlineColor"]), "strokeColorAfter": raw.get("outlineColor", defaults["outlineColor"])}
    return {
        "fontSize": font_size, "letterSpacing": number("letterSpacing", defaults["letterSpacing"]) * font_size,
        "fontFamily": str(raw.get("fontFamily", defaults["fontFamily"])), "fontBold": number("fontWeight", defaults["fontWeight"]) >= 600,
        "safeAreaLeft": number("safeAreaLeft", defaults["safeAreaLeft"]), "safeAreaRight": number("safeAreaRight", defaults["safeAreaRight"]),
        "rubySize": font_size * number("rubyScale", defaults["rubyScale"]), "rubyOffset": number("rubyGap", defaults["rubyGap"]) * font_size,
        "rubyLetterSpacing": number("letterSpacing", defaults["letterSpacing"]) * font_size, "rubyBold": False,
        "ruby2Size": font_size * number("ruby2Scale", defaults["ruby2Scale"]), "ruby2Offset": number("rubyGap", defaults["rubyGap"]) * font_size,
        "ruby2LetterSpacing": number("letterSpacing", defaults["letterSpacing"]) * font_size, "ruby2Bold": False, "rubyIsolateEnabled": True,
        "colorBefore": str(raw.get("textColor", defaults["textColor"])), "colorAfter": str(raw.get("activeColor", defaults["activeColor"])),
        "strokeColorBefore": str(raw.get("outlineColor", defaults["outlineColor"])), "strokeColorAfter": str(raw.get("outlineColor", defaults["outlineColor"])),
        "strokeWidth": number("outlineWidth", defaults["outlineWidth"]), "line1X": number("line1X", defaults["line1X"]) * 1280, "line1Y": number("line1Y", defaults["line1Y"]) * 720,
        "line2Right": number("line2Right", defaults["line2Right"]) * 1280, "line2Y": number("line2Y", defaults["line2Y"]) * 720,
        "fadeEnabled": True, "fadeParagraphOnly": False, "fadeDurationMs": number("fadeInMs", 100),
        "indicatorEnabled": raw.get("showProgressDots", True), "indicatorDuration": 3, "indicatorSize": 34,
        "indicatorSpacing": 12, "indicatorStrokeWidth": 3, "indicatorStrokeColor": "#000000", "indicatorFillColor": "#ffffff",
        "characterProfiles": profiles, "songTitle": {"enabled": False},
    }


def _media_number(value: object, fallback: float) -> float:
    try:
        text = str(value)
        if "/" in text:
            numerator, denominator = text.split("/", 1)
            return float(numerator) / float(denominator)
        return float(value)
    except (TypeError, ValueError, ZeroDivisionError):
        return fallback


def export_output_path(settings: Settings, project_id: str, job_id: str, fmt: str) -> Path:
    extension = "webm" if fmt == "webm" else "mp4"
    return settings.projects_dir / project_id / "exports" / f"{job_id[:8]}.{extension}"


def export_raw_output_path(settings: Settings, project_id: str, job_id: str, fmt: str) -> Path:
    extension = "webm" if fmt == "webm" else "mp4"
    return settings.projects_dir / project_id / "exports" / f"{job_id[:8]}.kirakara.{extension}"


def _safe_filename(value: object, extension: str) -> str:
    name = "".join(char if char.isalnum() or char in " .-_" else "_" for char in str(value or "nicokara")).strip()
    return f"{name or 'nicokara'}.{extension}"


def _chrome_path(settings: Settings) -> str:
    candidates = [settings.chrome_path, os.environ.get("NICOKARA_CHROME_PATH", ""), shutil.which("chrome"), r"C:\Program Files\Google\Chrome\Application\chrome.exe", r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return str(candidate)
    raise ExportError("未找到 Chrome，无法在服务端运行 Kirakara 导出")


def build_worker_html(settings: Settings, project_id: str, job_id: str, document: dict, payload: dict) -> str:
    media = document.get("media", {})
    width = int(media.get("width") or 1920)
    height = int(media.get("height") or 1080)
    fps = max(1.0, _media_number(media.get("fps"), 30))
    duration = _media_number(media.get("duration_ms"), max([line.get("end_ms") or 0 for line in document.get("lyrics", {}).get("lines", [])] + [1000])) / 1000
    config = document_to_config(document)
    entry_buf = (float(config.get("fadeDurationMs", 100)) / 1000 + 3.5) if config.get("indicatorEnabled") else 2
    base = f"{settings.export_base_url.rstrip('/')}{settings.api_prefix}/projects/{project_id}"
    fmt = str(payload.get("format", "mp4"))
    data = {"lrc": document_to_lrc(document), "config": config, "entryBuf": entry_buf, "w": width, "h": height, "fps": fps, "duration": duration, "format": fmt, "videoUrl": f"{base}/video", "audioUrl": None, "progressUrl": f"{base}/export-worker/{job_id}/progress", "cancelUrl": f"{base}/export-worker/{job_id}/cancel", "resultUrl": f"{base}/export-worker/{job_id}/result", "errorUrl": f"{base}/export-worker/{job_id}/error"}
    encoded = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    return f'''<!doctype html><meta charset="utf-8"><script src="/kirakara/vendor/mp4box.all.min.js"></script><script src="/kirakara/js/shared/title.js"></script><script src="/kirakara/js/shared/config.js"></script><script src="/kirakara/js/shared/measure.js"></script><script src="/kirakara/js/shared/progress.js"></script><script src="/kirakara/js/shared/utils.js"></script><script src="/kirakara/js/parser.js"></script><script src="/kirakara/js/muxer.js"></script><script src="/kirakara/js/codec.js"></script><script src="/kirakara/js/canvas-renderer.js"></script><script src="/kirakara/js/export/muxer.js"></script><script src="/kirakara/js/export/container-reader.js"></script><script src="/kirakara/js/export/decoder-provider.js"></script><script src="/kirakara/js/export/renderer.js"></script><script src="/kirakara/js/export/encoder.js"></script><script src="/kirakara/js/export/audio-encoder.js"></script><script src="/kirakara/js/exporter.js"></script><script>
const d={encoded}; const cancelRef={{current:false}};
setInterval(async()=>{{try{{const s=await fetch(d.cancelUrl).then(r=>r.json()); cancelRef.current=!!s.cancel_requested;}}catch{{}}}},500);
(async()=>{{try{{const config=d.config; const parsedData=parseLyrics(d.lrc,d.entryBuf,config); const measure=document.createElement('canvas').getContext('2d'); const maxWidth=1280*(1-Number(config.safeAreaLeft||.08)-Number(config.safeAreaRight||.08)); parsedData.forEach(line=>{{let low=10,high=Number(config.fontSize||64),best=low; while(low<=high){{const size=Math.floor((low+high)/2); measure.font=(config.fontBold?'bold ':'')+size+'px '+config.fontFamily; const width=line.chars.reduce((sum,ch)=>sum+measure.measureText(ch.text||'').width+Number(config.letterSpacing||0),0); if(width<=maxWidth){{best=size;low=size+1;}}else high=size-1;}} line.fontSize=best;}}); await doExportCanvas({{w:d.w,h:d.h,fps:d.fps,expCodec:d.format==='mp4'?'h264':'vp9',expFormat:d.format,duration:d.duration,totalTime:d.duration,videoUrl:d.videoUrl,audioUrl:d.audioUrl,bgImageEnabled:false,bgImageUrl:null,titleBackgroundUrl:null,parsedData,config,entryBuf:d.entryBuf,cancelRef,setExpProgress:v=>fetch(d.progressUrl,{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{progress:v/100,message:'正在逐帧渲染 Kirakara'}})}}),setExpEta:m=>fetch(d.progressUrl,{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{progress:.5,message:m}})}}),setExporting:()=>{{}},onComplete:async(blob,filename)=>fetch(d.resultUrl,{{method:'POST',headers:{{'X-Output-Filename':filename}},body:blob}}),onError:e=>fetch(d.errorUrl,{{method:'POST',body:String(e?.message||e)}})}});}}catch(e){{await fetch(d.errorUrl,{{method:'POST',body:String(e?.message||e)}});}}}})();
</script>'''


def _encoding_args(fmt: str, fps: float, payload: dict) -> tuple[list[str], list[str]]:
    output_fps = max(1.0, float(fps or 30))
    crf = int(payload.get("video_crf", 32 if fmt == "webm" else 20))
    audio_bitrate = int(payload.get("audio_bitrate_kbps", 192))
    gop = str(max(1, round(output_fps * float(payload.get("gop_seconds", 2)))))
    if fmt == "webm":
        cpu_used = int(payload.get("vp9_cpu_used", 2))
        video_args = ["-c:v", "libvpx-vp9", "-crf", str(crf), "-b:v", "0", "-deadline", "good", "-cpu-used", str(cpu_used), "-g", gop]
        audio_args = ["-c:a", "libopus", "-b:a", f"{audio_bitrate}k"]
    else:
        preset = str(payload.get("h264_preset") or "medium")
        video_args = ["-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-g", gop, "-pix_fmt", "yuv420p", "-movflags", "+faststart"]
        audio_args = ["-c:a", "aac", "-b:a", f"{audio_bitrate}k", "-ar", "44100", "-ac", "2"]
    return video_args, audio_args


def _encode_with_ffmpeg(raw: Path, output: Path, audio: Path, fmt: str, ffmpeg: str, fps: float, duration_ms: int, payload: dict, *, progress_callback: Callable[[float, str], None] | None = None, should_cancel: Callable[[], bool] | None = None) -> None:
    output_fps = max(1.0, float(fps or 30))
    video_args, audio_args = _encoding_args(fmt, output_fps, payload)
    # Force constant frame pacing in the final file. The browser-generated raw
    # container can carry fractional or slightly irregular timestamps; leaving
    # sync to the muxer makes some players present it as visibly lower FPS.
    frame_args = ["-r", f"{output_fps:.6f}", "-fps_mode", "cfr"]
    command = [ffmpeg, "-y", "-hide_banner", "-nostats", "-progress", "pipe:1", "-i", str(raw), "-i", str(audio), "-map", "0:v:0", "-map", "1:a:0", *frame_args, *video_args, *audio_args, "-shortest", str(output)]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace")
    try:
        assert process.stdout is not None
        for line in process.stdout:
            if should_cancel and should_cancel():
                process.terminate()
                raise ExportCanceled("导出已取消")
            if line.startswith("out_time_ms=") and duration_ms:
                try:
                    elapsed = int(line.split("=", 1)[1].strip())
                except ValueError:
                    continue
                if progress_callback:
                    progress_callback(min(.98, max(.82, .82 + elapsed / (duration_ms * 1000) * .16)), "正在用 FFmpeg 编码视频")
        if process.wait() != 0:
            raise ExportError(f"FFmpeg 编码失败（退出码 {process.returncode}）")
    finally:
        if process.poll() is None:
            process.kill()


def run_kirakara_export(job_id: str, project_id: str, document: dict, payload: dict, settings: Settings, *, progress_callback: Callable[[float, str], None] | None = None, should_cancel: Callable[[], bool] | None = None) -> dict:
    fmt = str(payload.get("format", "mp4"))
    output = export_output_path(settings, project_id, job_id, fmt)
    raw = export_raw_output_path(settings, project_id, job_id, fmt)
    output.parent.mkdir(parents=True, exist_ok=True)
    for marker in (output, raw, output.with_suffix(".error"), raw.with_suffix(".partial")):
        marker.unlink(missing_ok=True)
    chrome = _chrome_path(settings)
    worker_url = f"{settings.export_base_url.rstrip('/')}{settings.api_prefix}/projects/{project_id}/export-worker/{job_id}"
    profile = Path(tempfile.mkdtemp(prefix=f"nicokara-export-{job_id[:8]}-"))
    command = [chrome, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check", "--disable-extensions", "--autoplay-policy=no-user-gesture-required", f"--user-data-dir={profile}", worker_url]
    process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    started = time.monotonic()
    try:
        while True:
            if should_cancel and should_cancel():
                process.terminate()
                raise ExportCanceled("导出已取消")
            error = output.with_suffix(".error")
            if error.is_file():
                raise ExportError(error.read_text(encoding="utf-8", errors="replace") or "Kirakara 服务端导出失败")
            if raw.is_file() and raw.stat().st_size > 0:
                if progress_callback:
                    progress_callback(.8, "Kirakara 字幕渲染完成，正在封装音频")
                video = settings.projects_dir / project_id / "video.mp4"
                derived = settings.projects_dir / project_id / "derived"
                audio = derived / "instrumental.wav" if payload.get("audio_track") == "off_vocal" else derived / "source_audio.wav"
                if not audio.is_file() and payload.get("audio_track") == "on_vocal":
                    from app.services.audio import prepare_source_audio
                    prepare_source_audio(video, derived, settings.ffmpeg_path)
                if not audio.is_file():
                    raise ExportError("导出音轨不存在，请先准备音频或完成人声分离")
                duration_ms = round(_media_number(document.get("media", {}).get("duration_ms"), 1000))
                fps = max(1.0, _media_number(document.get("media", {}).get("fps"), 30))
                _encode_with_ffmpeg(raw, output, audio, fmt, settings.ffmpeg_path, fps, duration_ms, payload, progress_callback=progress_callback, should_cancel=should_cancel)
                raw.unlink(missing_ok=True)
                if progress_callback:
                    progress_callback(1.0, "Kirakara 服务端导出完成")
                extension = output.suffix.lstrip(".")
                return {"output": output.name, "filename": _safe_filename(document.get("project", {}).get("name"), extension), "format": extension, "media_type": "video/mp4" if output.suffix == ".mp4" else "video/webm", "size_bytes": output.stat().st_size}
            if process.poll() is not None:
                raise ExportError(f"Chrome 导出进程异常退出（退出码 {process.returncode}）")
            if time.monotonic() - started > 3600:
                process.terminate()
                raise ExportError("Kirakara 导出超时")
            time.sleep(.25)
    finally:
        if process.poll() is None:
            process.kill()
        shutil.rmtree(profile, ignore_errors=True)
