from fastapi import FastAPI
from app.core.logger import setup_logging, get_logger
from app.core.middleware import CorrelationIdMiddleware
from app.schemas.base_response import BaseSuccessResponse
from app.utils.response_builder import success_response
from app.api import metadata_api

# Initialize centralized logging before starting the app
setup_logging()
logger = get_logger("app.main")

app = FastAPI(
    title="Trybo Agentic Bridge Backend",
    version="1.0.0"
)

# Register API Routers
app.include_router(metadata_api.router)

# Add correlation ID middleware
app.add_middleware(CorrelationIdMiddleware)


@app.get("/", response_model=BaseSuccessResponse)
async def health_check():
    logger.info("Health check endpoint called")
    
    response = success_response(
        message="Health check successful",
        data={
            "service": "trybo-agentic-bridge-backend"
        }
    )
    
    logger.info("Health check served successfully")
    return response
