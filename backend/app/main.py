from fastapi import FastAPI

app = FastAPI(
    title="Trybo Agentic Bridge Backend",
    version="1.0.0"
)


@app.get("/")
async def health_check():
    return {
        "status": "healthy",
        "service": "trybo-agentic-bridge-backend"
    }
