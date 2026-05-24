import uuid
import asyncio
from datetime import datetime
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core import config
from app.core.logger import get_logger, set_correlation_id, set_task_id, correlation_id_ctx, task_id_ctx
from app.core.enums import WebSocketEventType, ApprovalAction
from app.models.workflow_state import WorkflowState
from app.repositories.task_repository import task_repo
from app.services.agent_orchestrator_service import (
    register_task,
    set_task_reference,
    handle_approval_response,
    cancel_task,
    run_orchestration,
    is_websocket_active,
    active_tasks
)

router = APIRouter(tags=["WebSocket"])
logger = get_logger("websocket.agent")

async def heartbeat_loop(websocket: WebSocket, task_id: str, correlation_id: str):
    from app.core.enums import WebSocketEventType
    interval = config.HEARTBEAT_INTERVAL_SECONDS
    timeout = config.HEARTBEAT_TIMEOUT_SECONDS
    logger.info("Starting heartbeat PING/PONG checker for task %s", task_id)
    try:
        while True:
            await asyncio.sleep(interval)
            task_info = active_tasks.get(task_id)
            if not task_info or websocket.client_state.value == 2: # DISCONNECTED
                break
                
            last_activity = task_info.get("last_activity_time", 0.0)
            now = asyncio.get_event_loop().time()
            if now - last_activity > timeout:
                logger.warning("Heartbeat timeout exceeded (%ds) for task %s. Disconnecting.", int(now - last_activity), task_id)
                try:
                    await websocket.close()
                except Exception:
                    pass
                await cancel_task(task_id)
                break
                
            # Send server-initiated ping message
            try:
                await websocket.send_json({
                    "event_type": WebSocketEventType.PING,
                    "correlation_id": correlation_id,
                    "task_id": task_id,
                    "timestamp": datetime.utcnow().isoformat() + "Z"
                })
            except Exception:
                logger.warning("Heartbeat PING write failed. Closing socket.")
                await cancel_task(task_id)
                break
    except asyncio.CancelledError:
        pass

@router.websocket("/v1/agent/connect")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    
    if is_websocket_active(websocket):
        logger.warning("Duplicate task creation attempted for active websocket connection")
        return
        
    correlation_id = websocket.headers.get("x-correlation-id") or str(uuid.uuid4())
    task_id = str(uuid.uuid4())
    
    corr_token = set_correlation_id(correlation_id)
    task_token = set_task_id(task_id)
    
    logger.info("WebSocket connection established")
    
    approval_event = asyncio.Event()
    await register_task(task_id, websocket, approval_event, correlation_id)
    
    # Start heartbeat loop in background
    heartbeat_task = asyncio.create_task(heartbeat_loop(websocket, task_id, correlation_id))
    
    orchestration_started = False
    
    try:
        while True:
            payload = await websocket.receive_json()
            
            # Update last activity timestamp
            task_info = active_tasks.get(task_id)
            if task_info:
                task_info["last_activity_time"] = asyncio.get_event_loop().time()
                
            if not isinstance(payload, dict):
                logger.warning("Invalid websocket payload: %s", payload)
                continue
                
            event_type = payload.get("event_type")
            
            if event_type == WebSocketEventType.START_TASK:
                if orchestration_started:
                    continue
                
                # Check for task resumption / reconnection
                client_task_id = payload.get("task_id")
                if client_task_id:
                    existing_task = task_repo.get_task(client_task_id)
                    if existing_task:
                        logger.info("Resuming existing task %s from client reconnect", client_task_id)
                        from app.services.agent_orchestrator_service import _tasks_lock
                        async with _tasks_lock:
                            if task_id in active_tasks:
                                active_info = active_tasks.pop(task_id)
                                task_id = client_task_id
                                active_tasks[task_id] = active_info
                                set_task_id(task_id)
                        prompt = existing_task.get("user_prompt", "")
                        state = WorkflowState(prompt=prompt)
                    else:
                        raw_prompt = payload.get("prompt", "")
                        prompt = raw_prompt.strip()
                        if not prompt:
                            continue
                        task_repo.create_task(task_id, "SCHEDULED", prompt)
                        state = WorkflowState(prompt=prompt)
                else:
                    raw_prompt = payload.get("prompt", "")
                    prompt = raw_prompt.strip()
                    if not prompt:
                        continue
                    task_repo.create_task(task_id, "SCHEDULED", prompt)
                    state = WorkflowState(prompt=prompt)
                
                orchestration_task = asyncio.create_task(
                    run_orchestration(websocket, correlation_id, task_id, state)
                )
                set_task_reference(task_id, orchestration_task)
                orchestration_started = True
                
            elif event_type == WebSocketEventType.APPROVAL_RESPONSE:
                action_str = payload.get("action")
                try:
                    action = ApprovalAction(action_str)
                    task_repo.update_task_approval(task_id, action.value, payload.get("feedback"))
                    handle_approval_response(
                        task_id,
                        action,
                        payload.get("feedback"),
                        selected_vendors=payload.get("selected_vendors")
                    )
                except ValueError:
                    logger.warning("Invalid approval action: %s", action_str)
                    
            elif event_type == WebSocketEventType.STOP:
                await cancel_task(task_id)
                
            elif event_type == WebSocketEventType.PONG:
                logger.debug("Received PONG from client")
                continue
                
            elif event_type == WebSocketEventType.PING:
                # Client-initiated ping, respond with pong
                try:
                    await websocket.send_json({
                        "event_type": WebSocketEventType.PONG,
                        "correlation_id": correlation_id,
                        "task_id": task_id,
                        "timestamp": datetime.utcnow().isoformat() + "Z"
                    })
                except Exception:
                    pass
            else:
                logger.warning("Unknown websocket event: %s", event_type)
                
    except WebSocketDisconnect:
        logger.info("WebSocket disconnected")
    except Exception:
        logger.exception("WebSocket unexpected failure")
    finally:
        # Cancel heartbeat checker task
        heartbeat_task.cancel()
        # Cancel orchestration task and cleanup
        await cancel_task(task_id)
        # Clear contextvars
        correlation_id_ctx.reset(corr_token)
        task_id_ctx.reset(task_token)
