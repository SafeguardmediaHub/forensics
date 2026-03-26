from fastapi import APIRouter
from fastapi.responses import JSONResponse

from api.services.job_service import get_job, get_job_result

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("/{job_id}")
async def get_job_status(job_id: str) -> JSONResponse:
    return JSONResponse(content=get_job(job_id))


@router.get("/{job_id}/result")
async def get_job_result_route(job_id: str) -> JSONResponse:
    return JSONResponse(content=get_job_result(job_id))
