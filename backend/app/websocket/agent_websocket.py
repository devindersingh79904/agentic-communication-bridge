import uuid
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from app.core.logger import get_logger, set_correlation_id, correlation_id_ctx
from app.core.enums import TaskState, AgentStep
from app.schemas.websocket_schema import StatusUpdateEvent

router = APIRouter(tags=["WebSocket"])
logger = get_logger("websocket.agent")

@router.websocket("/v1/agent/connect")
async def websocket_endpoint(websocket: WebSocket):
    """
    WebSocket connection endpoint for agent interaction.
    """
    # Accept the incoming connection
    await websocket.accept()
    
    # Extract X-Correlation-ID header if provided, otherwise generate UUID4 correlation_id
    correlation_id = websocket.headers.get("x-correlation-id")
    if not correlation_id:
        correlation_id = str(uuid.uuid4())
        
    # Propagate correlation_id using contextvars
    token = set_correlation_id(correlation_id)
    
    logger.info("WebSocket connection established")
    
    try:
        # Send initial StatusUpdateEvent
        initial_event = StatusUpdateEvent(
            correlation_id=correlation_id,
            task_id=None,
            task_state=TaskState.RUNNING,
            agent_step=AgentStep.SEARCHING_VENDORS,
            message="WebSocket connection established successfully"
        )
        await websocket.send_json(initial_event.model_dump())
        
        # Receive websocket payloads
        while True:
            payload = await websocket.receive_json()
            logger.info("WebSocket payload received: %s", payload)
            
    except WebSocketDisconnect:
        logger.info("WebSocket disconnected")
    except Exception:
        logger.exception("WebSocket unexpected failure")
    finally:
        # Clear contextvars correctly
        correlation_id_ctx.reset(token)
