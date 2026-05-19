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

class BaseErrorResponse(BaseModel):
    """
    Centralized schema for error API responses.
    Includes optional structured validation errors.
    """
    success: bool = Field(default=False, description="Indicates if the request failed")
    message: str = Field(..., description="Human-readable error description")
    error_code: str = Field(..., description="Machine-readable error code")
    correlation_id: str = Field(..., description="Trace ID for the request lifecycle")
    timestamp: datetime = Field(..., description="Time of response generation (timezone-aware UTC)")
    errors: Optional[list[dict[str, str]]] = Field(
        default=None, 
        description="List of structured validation errors (e.g., [{'field': 'email', 'message': 'Invalid format'}])"
    )
