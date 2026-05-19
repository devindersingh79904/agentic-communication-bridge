import asyncio
from typing import Dict, Any
from fastapi import WebSocket
from starlette.websockets import WebSocketState

from app.core.logger import get_logger, set_correlation_id, set_task_id, correlation_id_ctx, task_id_ctx
from app.core.enums import TaskState, AgentStep
from app.core import config
from app.schemas.websocket_schema import (
    StatusUpdateEvent,
    ApprovalRequiredEvent,
    TaskCompletedEvent,
    TaskCancelledEvent,
    ErrorEvent
)

logger = get_logger("services.agent_orchestrator")

# In-memory registry to track active WebSocket orchestration sessions
active_tasks: Dict[str, Dict[str, Any]] = {}

def register_task(task_id: str, websocket: WebSocket, approval_event: asyncio.Event) -> None:
    """
    Registers a new active WebSocket task session in the registry.
    """
    active_tasks[task_id] = {
        "websocket": websocket,
        "task": None,
        "approval_event": approval_event,
        "task_state": TaskState.SCHEDULED,
        "cancelled": False
    }
    logger.info("Orchestration task registered in registry")

def set_task_reference(task_id: str, task: asyncio.Task) -> None:
    """
    Associates the asyncio.Task reference with the registered task.
    """
    if task_id in active_tasks:
        active_tasks[task_id]["task"] = task

def approve_task(task_id: str) -> None:
    """
    Approves the task and triggers the approval event to resume the workflow.
    """
    task_info = active_tasks.get(task_id)
    if task_info:
        state = task_info.get("task_state")
        if state == TaskState.WAITING_APPROVAL:
            logger.info("Approval received")
            task_info["approval_event"].set()
        else:
            logger.warning("Task received APPROVED event but is in state: %s", state)

async def cancel_task(task_id: str) -> None:
    """
    Cancels the active task and interrupts the background orchestration task.
    """
    task_info = active_tasks.get(task_id)
    if not task_info:
        return
        
    state = task_info.get("task_state")
    # If the task is already finished, ignore safely to avoid race conditions
    if state in (TaskState.SUCCESS, TaskState.FAILED, TaskState.CANCELLED):
        logger.debug("Task already finished. Ignoring cancellation request.")
        return
        
    task_info["cancelled"] = True
    task_info["task_state"] = TaskState.CANCELLED
    
    asyncio_task = task_info.get("task")
    if asyncio_task and not asyncio_task.done():
        logger.info("Orchestration cancelled")
        asyncio_task.cancel()
        
def cleanup_task(task_id: str) -> None:
    """
    Removes the task from the registry to prevent memory leaks.
    """
    if task_id in active_tasks:
        active_tasks.pop(task_id)
        logger.info("Orchestration task cleaned up from registry")

async def safe_send_json(websocket: WebSocket, payload: dict) -> None:
    """
    Sends JSON payload to the websocket only if the connection is currently open/connected.
    """
    if websocket.client_state != WebSocketState.CONNECTED:
        logger.debug("Skipping send, WebSocket is not in CONNECTED state: %s", websocket.client_state)
        return
    try:
        await websocket.send_json(payload)
    except Exception:
        logger.debug("Failed to send websocket payload, client likely disconnected")

async def run_orchestration(websocket: WebSocket, correlation_id: str, task_id: str) -> None:
    """
    Simulates the async agent orchestration workflow lifecycle.
    """
    # Propagate correlation_id and task_id inside the background task context
    corr_token = set_correlation_id(correlation_id)
    task_token = set_task_id(task_id)
    
    logger.info("Orchestration workflow started")
    
    try:
        # Step 1: SEARCHING_VENDORS
        logger.info("Entering step: SEARCHING_VENDORS")
        active_tasks[task_id]["task_state"] = TaskState.RUNNING
        event = StatusUpdateEvent(
            correlation_id=correlation_id,
            task_id=task_id,
            task_state=TaskState.RUNNING,
            agent_step=AgentStep.SEARCHING_VENDORS,
            message="Searching for vendors..."
        )
        await safe_send_json(websocket, event.model_dump())
        await asyncio.sleep(1.5)
        
        # Step 2: ANALYZING_PRICING
        logger.info("Entering step: ANALYZING_PRICING")
        event = StatusUpdateEvent(
            correlation_id=correlation_id,
            task_id=task_id,
            task_state=TaskState.RUNNING,
            agent_step=AgentStep.ANALYZING_PRICING,
            message="Analyzing pricing..."
        )
        await safe_send_json(websocket, event.model_dump())
        await asyncio.sleep(1.5)
        
        # Step 3: DRAFTING_OUTREACH
        logger.info("Entering step: DRAFTING_OUTREACH")
        event = StatusUpdateEvent(
            correlation_id=correlation_id,
            task_id=task_id,
            task_state=TaskState.RUNNING,
            agent_step=AgentStep.DRAFTING_OUTREACH,
            message="Drafting outreach..."
        )
        await safe_send_json(websocket, event.model_dump())
        await asyncio.sleep(1.5)
        
        # Step 4: SELF_REFLECTION
        logger.info("Entering step: SELF_REFLECTION")
        event = StatusUpdateEvent(
            correlation_id=correlation_id,
            task_id=task_id,
            task_state=TaskState.RUNNING,
            agent_step=AgentStep.SELF_REFLECTION,
            message="Performing self-reflection..."
        )
        await safe_send_json(websocket, event.model_dump())
        await asyncio.sleep(1.5)
        
        # Step 5: WAITING_APPROVAL
        logger.info("Orchestration paused, waiting for user approval")
        active_tasks[task_id]["task_state"] = TaskState.WAITING_APPROVAL
        
        approval_event = active_tasks[task_id]["approval_event"]
        app_req_event = ApprovalRequiredEvent(
            correlation_id=correlation_id,
            task_id=task_id,
            task_state=TaskState.WAITING_APPROVAL,
            draft_message="Hello vendor, we would like to discuss pricing...",
            message="Draft generated. Awaiting user approval."
        )
        await safe_send_json(websocket, app_req_event.model_dump())
        
        # Pause workflow using asyncio.Event with a timeout
        logger.info("Approval timeout started (%s seconds)", config.APPROVAL_TIMEOUT_SECONDS)
        try:
            await asyncio.wait_for(approval_event.wait(), timeout=config.APPROVAL_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            logger.warning("Approval timeout exceeded. Task cancelled automatically.")
            active_tasks[task_id]["cancelled"] = True
            active_tasks[task_id]["task_state"] = TaskState.CANCELLED
            
            # Send TaskCancelledEvent
            cancelled_event = TaskCancelledEvent(
                correlation_id=correlation_id,
                task_id=task_id,
                task_state=TaskState.CANCELLED,
                message="Approval timeout exceeded. Task cancelled automatically."
            )
            await safe_send_json(websocket, cancelled_event.model_dump())
            logger.info("Orchestration auto-cancelled due to timeout")
            return
            
        # If task was cancelled while waiting, we exit
        if active_tasks[task_id].get("cancelled"):
            return
            
        # Step 6: SUCCESS
        logger.info("Orchestration resumed after approval")
        active_tasks[task_id]["task_state"] = TaskState.SUCCESS
        
        completed_event = TaskCompletedEvent(
            correlation_id=correlation_id,
            task_id=task_id,
            task_state=TaskState.SUCCESS,
            message="Task successfully executed. Outreach finalized."
        )
        await safe_send_json(websocket, completed_event.model_dump())
        logger.info("Orchestration completed successfully")
        
    except asyncio.CancelledError:
        logger.info("Orchestration cancelled")
        # Send TaskCancelledEvent
        cancelled_event = TaskCancelledEvent(
            correlation_id=correlation_id,
            task_id=task_id,
            task_state=TaskState.CANCELLED,
            message="Orchestration cancelled by client."
        )
        await safe_send_json(websocket, cancelled_event.model_dump())
    except Exception as e:
        logger.exception("Orchestration unexpected failure")
        error_event = ErrorEvent(
            correlation_id=correlation_id,
            task_id=task_id,
            task_state=TaskState.FAILED,
            error_code="ORCHESTRATION_FAILURE",
            message=f"Orchestration unexpected failure: {str(e)}"
        )
        await safe_send_json(websocket, error_event.model_dump())
    finally:
        correlation_id_ctx.reset(corr_token)
        task_id_ctx.reset(task_token)
        cleanup_task(task_id)
