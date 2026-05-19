import uuid
import asyncio
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.logger import get_logger, set_correlation_id, set_task_id, correlation_id_ctx, task_id_ctx
from app.core.enums import WebSocketEventType
from app.models.workflow_state import WorkflowState
from app.services.agent_orchestrator_service import (
    register_task,
    set_task_reference,
    approve_task,
    cancel_task,
    run_orchestration,
    is_websocket_active
)

router = APIRouter(tags=["WebSocket"])
logger = get_logger("websocket.agent")

@router.websocket("/v1/agent/connect")
async def websocket_endpoint(websocket: WebSocket):
    """
    WebSocket connection endpoint for agent interaction.
    Orchestration starts only after a START_TASK event is received with a valid prompt.
    """
    await websocket.accept()
    
    # Guard against duplicate tasks for this websocket connection lifecycle
    if is_websocket_active(websocket):
        logger.warning("Duplicate task creation attempted for active websocket connection (rejected)")
        return
        
    # Extract correlation_id or generate a new one
    correlation_id = websocket.headers.get("x-correlation-id")
    if not correlation_id:
        correlation_id = str(uuid.uuid4())
        
    # Generate task_id
    task_id = str(uuid.uuid4())
    
    # Propagate correlation_id and task_id using contextvars
    corr_token = set_correlation_id(correlation_id)
    task_token = set_task_id(task_id)
    
    logger.info("WebSocket connection established")
    
    # Create the approval event and register task in SCHEDULED state
    approval_event = asyncio.Event()
    register_task(task_id, websocket, approval_event)
    
    orchestration_started = False
    
    try:
        while True:
            # Receive websocket payloads
            payload = await websocket.receive_json()
            logger.info("WebSocket payload received: %s", payload)
            
            # Payload schema validation
            if not isinstance(payload, dict):
                logger.warning("Invalid non-dictionary websocket payload received: %s", payload)
                continue
                
            event_type = payload.get("event_type")
            
            if event_type == WebSocketEventType.START_TASK:
                if orchestration_started:
                    logger.warning("Duplicate START_TASK event received (ignored)")
                    continue
                    
                raw_prompt = payload.get("prompt", "")
                prompt = raw_prompt.strip()
                if not prompt:
                    logger.warning(
                        "START_TASK received with empty or whitespace-only prompt (rejected)"
                    )
                    continue
                
                logger.info("START_TASK received, initializing workflow state")
                state = WorkflowState(prompt=prompt)
                
                # Spawn background orchestration task with workflow state
                orchestration_task = asyncio.create_task(
                    run_orchestration(websocket, correlation_id, task_id, state)
                )
                set_task_reference(task_id, orchestration_task)
                orchestration_started = True
                
            elif event_type == WebSocketEventType.APPROVED:
                approve_task(task_id)
            elif event_type == WebSocketEventType.STOP:
                await cancel_task(task_id)
            else:
                logger.warning("Unknown websocket event received: %s", event_type)
                
    except WebSocketDisconnect:
        logger.info("WebSocket disconnected")
    except Exception:
        logger.exception("WebSocket unexpected failure")
    finally:
        # Cancel the orchestration task and cleanup the task session on connection drop
        await cancel_task(task_id)
        # Clear contextvars correctly
        correlation_id_ctx.reset(corr_token)
        task_id_ctx.reset(task_token)
