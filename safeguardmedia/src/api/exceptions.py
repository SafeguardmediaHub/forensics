from fastapi import Request
from fastapi.responses import JSONResponse


class SafeguardMediaException(Exception):
    def __init__(self, message: str, code: str, status_code: int = 400):
        self.message = message
        self.code = code
        self.status_code = status_code
        super().__init__(message)


class UnsupportedFileTypeError(SafeguardMediaException):
    def __init__(self, filename: str):
        super().__init__(
            message=f"Unsupported file type: {filename}",
            code="UNSUPPORTED_FILE_TYPE",
            status_code=422,
        )


class FileTooLargeError(SafeguardMediaException):
    def __init__(self, size_mb: float, limit_mb: int):
        super().__init__(
            message=f"File too large: {size_mb:.1f} MB (limit: {limit_mb} MB)",
            code="FILE_TOO_LARGE",
            status_code=413,
        )


class EngineError(SafeguardMediaException):
    def __init__(self, engine: str, detail: str):
        super().__init__(
            message=f"Engine '{engine}' failed: {detail}",
            code="ENGINE_ERROR",
            status_code=500,
        )


# ── Exception handlers (registered in main.py) ────────────────────────────────

async def safeguard_exception_handler(
    request: Request, exc: SafeguardMediaException
) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.message, "code": exc.code},
    )
