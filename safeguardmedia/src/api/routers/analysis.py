import json

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from api.services.job_service import submit_analysis

router = APIRouter(tags=["analysis"])


@router.post("/analyze")
async def analyze_media(
    media_type: str = Form(...),
    file: UploadFile = File(...),
    options: str | None = Form(default=None),
) -> JSONResponse:
    parsed_options: dict = {}
    if options:
        try:
            parsed_options = json.loads(options)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=422, detail="Invalid JSON in 'options'") from exc

    result, status_code = await submit_analysis(
        media_type=media_type,
        file=file,
        options=parsed_options,
    )
    return JSONResponse(content=result, status_code=status_code)
