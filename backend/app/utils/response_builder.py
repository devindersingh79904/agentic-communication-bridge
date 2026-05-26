from datetime import datetime, timezone
from typing import Any, Optional
from app.core.logger import get_correlation_id
from app.schemas.base_response import BaseSuccessResponse

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
