from fastapi import APIRouter, HTTPException, Request


router = APIRouter(prefix="/models", tags=["models"])
ACTIVE_JOB_STATES = {"QUEUED", "PREPARING", "RUNNING"}


@router.get("/resident")
def resident_model(request: Request):
    return request.app.state.model_store.status()


@router.delete("/resident")
def release_resident_model(request: Request):
    database = request.app.state.database
    if any(job["status"] in ACTIVE_JOB_STATES for job in database.list_jobs(limit=200)):
        raise HTTPException(409, "分析任务运行时不能释放模型")
    return request.app.state.model_store.release()
