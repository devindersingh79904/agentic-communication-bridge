import uuid
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from app.core.logger import set_correlation_id, get_logger, correlation_id_ctx

logger = get_logger("core.middleware")

class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """
    Middleware to extract or generate a correlation ID for every incoming HTTP request.
    It injects the ID into contextvars for async-safe tracing and attaches it to the response headers.
    """
    
    CORRELATION_ID_HEADER = "X-Correlation-ID"

    async def dispatch(self, request: Request, call_next) -> Response:
        # Extract correlation ID from headers or generate a new one
        correlation_id = request.headers.get(self.CORRELATION_ID_HEADER)
        if not correlation_id:
            correlation_id = str(uuid.uuid4())
            
        # Set contextvar for the current async execution flow
        token = set_correlation_id(correlation_id)
        
        try:
            logger.info(f"Incoming Request -> {request.method} {request.url.path}")
            
            response = await call_next(request)
            
            logger.info(f"Outgoing Response -> {request.method} {request.url.path} {response.status_code}")
            
            # Attach correlation ID to response headers
            response.headers[self.CORRELATION_ID_HEADER] = correlation_id
            return response
            
        except Exception:
            logger.exception(
                f"Unhandled exception during request processing -> {request.method} {request.url.path}"
            )
            raise
        finally:
            correlation_id_ctx.reset(token)
