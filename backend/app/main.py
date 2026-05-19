from fastapi import FastAPI
from app.core.logger import setup_logging, get_logger
from app.core.middleware import CorrelationIdMiddleware

# Initialize centralized logging before starting the app
setup_logging()
logger = get_logger("app.main")

app = FastAPI(
    title="Trybo Agentic Bridge Backend",
    version="1.0.0"
)

# Add correlation ID middleware
app.add_middleware(CorrelationIdMiddleware)


@app.get("/")
async def health_check():
    logger.info("Health check endpoint called")
    return {
        "status": "healthy",
        "service": "trybo-agentic-bridge-backend"
    }
