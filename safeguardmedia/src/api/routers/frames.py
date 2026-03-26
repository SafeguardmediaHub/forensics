from fastapi import APIRouter, UploadFile
from fastapi.responses import JSONResponse

from api.services.job_service import submit_analysis

router = APIRouter(prefix="/frames", tags=["frames"])


@router.post("/analyze")
async def analyze_frames(file: UploadFile) -> JSONResponse:
    result, status_code = await submit_analysis(media_type="frames", file=file)
    return JSONResponse(content=result, status_code=status_code)
