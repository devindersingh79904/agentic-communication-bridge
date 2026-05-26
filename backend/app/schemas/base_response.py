from datetime import datetime
from typing import Any, Optional
from pydantic import BaseModel, Field

class BaseSuccessResponse(BaseModel):
    """
    Centralized schema for successful API responses.
    """
    success: bool = Field(default=True, description="Indicates if the request was successful")
    message: str = Field(..., description="Human-readable success message")
    correlation_id: str = Field(..., description="Trace ID for the request lifecycle")
    timestamp: datetime = Field(..., description="Time of response generation (timezone-aware UTC)")
    data: Optional[Any] = Field(default=None, description="The payload data returned by the API")
