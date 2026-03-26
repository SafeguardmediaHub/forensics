from fastapi import APIRouter, UploadFile
from fastapi.responses import JSONResponse

from api.services.job_service import submit_analysis

router = APIRouter(prefix="/video", tags=["video"])


@router.post("/analyze")
async def analyze_video(file: UploadFile) -> JSONResponse:
    result, status_code = await submit_analysis(media_type="video", file=file)
    return JSONResponse(content=result, status_code=status_code)
