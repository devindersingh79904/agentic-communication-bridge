from datetime import datetime, timezone
from typing import Any, Optional
from app.core.logger import get_correlation_id
from app.schemas.base_response import BaseSuccessResponse, BaseErrorResponse

def success_response(message: str, data: Optional[Any] = None) -> BaseSuccessResponse:
    """
    Utility to build a standardized successful API response.
    Automatically injects the current correlation ID and a timezone-aware UTC timestamp.
    """
    return BaseSuccessResponse(
        success=True,
        message=message,
        correlation_id=get_correlation_id(),
        timestamp=datetime.now(timezone.utc),
        data=data
    )

def error_response(
    message: str, 
    error_code: str, 
    errors: Optional[list[dict[str, str]]] = None
) -> BaseErrorResponse:
    """
    Utility to build a standardized error API response.
    Automatically injects the current correlation ID and a timezone-aware UTC timestamp.
    """
    return BaseErrorResponse(
        success=False,
        message=message,
        error_code=error_code,
        correlation_id=get_correlation_id(),
        timestamp=datetime.now(timezone.utc),
        errors=errors
    )
