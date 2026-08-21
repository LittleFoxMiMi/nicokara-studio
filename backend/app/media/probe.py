from __future__ import annotations
import json, subprocess
from pathlib import Path
def probe(path: Path, ffprobe: str) -> dict:
    try:
        p=subprocess.run([ffprobe,"-v","error","-show_format","-show_streams","-of","json",str(path)],capture_output=True,text=True,timeout=30,check=True)
        data=json.loads(p.stdout); video=next((s for s in data.get("streams",[]) if s.get("codec_type")=="video"),{})
        return {"duration_ms":round(float(data.get("format",{}).get("duration",0))*1000),"width":video.get("width"),"height":video.get("height"),"fps":video.get("r_frame_rate"),"codec":video.get("codec_name"),"probed":True}
    except (OSError, subprocess.SubprocessError, ValueError, json.JSONDecodeError): return {"probed":False}
def thumbnail(video: Path, target: Path, ffmpeg: str) -> bool:
    for seek in ("00:00:01", "00:00:00"):
        try:
            subprocess.run([ffmpeg,"-y","-ss",seek,"-i",str(video),"-frames:v","1","-vf","scale=640:-2",str(target)],capture_output=True,timeout=60,check=True)
            if target.is_file() and target.stat().st_size > 0: return True
        except (OSError, subprocess.SubprocessError):
            continue
    return False
