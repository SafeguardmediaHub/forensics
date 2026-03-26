from fastapi import APIRouter, UploadFile
from fastapi.responses import JSONResponse

from api.services.job_service import submit_analysis

router = APIRouter(prefix="/image", tags=["image"])


@router.post("/analyze")
async def analyze_image(file: UploadFile) -> JSONResponse:
    result, status_code = await submit_analysis(media_type="image", file=file)
    return JSONResponse(content=result, status_code=status_code)
