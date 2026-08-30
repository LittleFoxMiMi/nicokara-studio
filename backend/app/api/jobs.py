from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("")
def list_jobs(request: Request, project_id: str | None = None, limit: int = Query(50, ge=1, le=200)):
    return request.app.state.database.list_jobs(project_id, limit)


@router.get("/{job_id}")
def get_job(job_id: str, request: Request):
    job = request.app.state.database.get_job(job_id)
    if not job:
        raise HTTPException(404, "任务不存在")
    return job


@router.post("/{job_id}/cancel")
def cancel_job(job_id: str, request: Request):
    job = request.app.state.database.get_job(job_id)
    if not job:
        raise HTTPException(404, "任务不存在")
    if job["status"] in {"SUCCEEDED", "FAILED", "CANCELED"}:
        return job
    return request.app.state.database.update_job(job_id, cancel_requested=1, message="正在取消")


@router.post("/{job_id}/retry", status_code=202)
def retry_job(job_id: str, request: Request):
    job = request.app.state.database.get_job(job_id)
    if not job:
        raise HTTPException(404, "任务不存在")
    if job["status"] not in {"FAILED", "CANCELED"}:
        raise HTTPException(409, "仅失败或已取消任务可重试")
    project = request.app.state.database.get_project(job["project_id"])
    if not project:
        raise HTTPException(404, "工程不存在")
    return request.app.state.analysis_runner.enqueue(
        job["project_id"], job["type"], project["revision"], job["request"]
    )
