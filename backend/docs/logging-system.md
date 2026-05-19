# Trybo Agentic Bridge - Centralized Logging System

This document outlines the usage and architecture of our async-safe logging system, designed specifically for tracing agent orchestration, background tasks, and websockets.

## Overview
The logging system utilizes Python's native `contextvars` to propagate `correlation_id` and `task_id` safely across `asyncio` execution flows without requiring them to be manually passed through every function signature.

### Expected Log Format
```text
[2026-05-19 18:42:11.234] [INFO] [abc123] [task-123] [agent_service] Starting workflow
```
- **Timestamp**: Exact time with milliseconds.
- **Level**: Standard log levels (`INFO`, `ERROR`, etc.).
- **Correlation ID**: Traces a single user session or request chain.
- **Task ID**: Traces a specific orchestration task or background job.
- **Module/Service**: The origin of the log message.
- **Message**: The human-readable or structured payload.

---

## 1. Initialization & Uvicorn Integration (`main.py`)
To configure the centralized logging system and automatically capture and trace HTTP requests (including Uvicorn's access/error logs), call `setup_logging()` before initializing the FastAPI application.

```python
from fastapi import FastAPI
from app.core.logger import setup_logging, get_logger
from app.core.middleware import CorrelationIdMiddleware

# Call setup_logging FIRST to intercept Uvicorn and FastAPI loggers
setup_logging()

logger = get_logger("app.main")

app = FastAPI(title="Trybo Agentic Bridge Backend")

# Add middleware for Correlation ID propagation
app.add_middleware(CorrelationIdMiddleware)

@app.get("/")
async def health_check():
    logger.info("Health check endpoint called")
    return {"status": "healthy"}
```

### Uvicorn Access Logs
By running `setup_logging()`, the default Uvicorn loggers are overridden. Uvicorn access logs are reformatted to be cleaner and fully integrated with your correlation IDs.

**Example output:**
```text
[2026-05-19 18:42:11.234] [INFO] [abc123] [-] [uvicorn.access] GET / 200
[2026-05-19 18:42:11.235] [INFO] [abc123] [-] [app.main] Health check endpoint called
```

---

## 2. Service Layer Logging Example
In your service layers, simply import `get_logger` and use it. The `correlation_id` (and `task_id`, if set) will be injected automatically.

```python
# app/services/agent_service.py
from app.core.logger import get_logger

logger = get_logger("services.agent")

async def process_user_query(query: str):
    logger.info("Starting to process user query")
    try:
        # Business logic...
        logger.info("Successfully resolved user query")
    except Exception as e:
        # Full traceback logging without silent failures
        logger.exception("Failed to process query")
        raise
```

---

## 3. Background Task Tracing Example
When spawning background tasks or agent loops, you should set a specific `task_id`.

```python
# app/services/orchestrator.py
import uuid
import asyncio
from app.core.logger import get_logger, set_task_id, task_id_ctx

logger = get_logger("services.orchestrator")

async def run_agent_loop(payload: dict):
    # Set the task context for this specific async flow
    task_id = payload.get("task_id", str(uuid.uuid4()))
    token = set_task_id(task_id)
    
    try:
        logger.info("Agent loop initiated")
        # ... further execution ...
    finally:
        task_id_ctx.reset(token)
```

---

## 4. WebSocket Logging Example
WebSockets bypass standard HTTP middleware after the initial connection, but you should extract or generate a correlation ID upon connection and set it.

```python
# app/websocket/connection_manager.py
import uuid
from fastapi import WebSocket
from app.core.logger import get_logger, set_correlation_id

logger = get_logger("websocket.manager")

async def handle_connection(websocket: WebSocket):
    await websocket.accept()
    
    # Extract from headers (initial handshake) or generate new
    corr_id = websocket.headers.get("X-Correlation-ID", str(uuid.uuid4()))
    set_correlation_id(corr_id)
    
    logger.info("WebSocket connected successfully")
    
    try:
        from starlette.websockets import WebSocketDisconnect
        
        while True:
            data = await websocket.receive_text()
            logger.info("Received payload from client")
            # Route to services...
    except WebSocketDisconnect:
        logger.info("WebSocket disconnected")
```

---

## 5. Best Practices for Async Task Tracing
1. **Never Swallow Errors**: Always use `logger.error("Message", exc_info=True)` for caught exceptions that halt a flow.
2. **Beware of Thread Pools**: `contextvars` do automatically propagate into threads created by `asyncio.to_thread` in Python 3.9+, but verify your specific runtime if using older executors.
3. **Scrubbing Secrets**: The logger attempts to scrub obvious secrets based on string matching (`api_key`, `token`, etc.), but **do not intentionally log sensitive payloads**.
4. **State Transitions**: Always log agent state changes (e.g., `PENDING` -> `RUNNING`). The `task_id` context will make it trivial to filter logs for a specific orchestration run.
