import uuid
import asyncio
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from app.core import config
from app.core.logger import get_logger, set_correlation_id, set_task_id, correlation_id_ctx, task_id_ctx
from app.core.enums import WebSocketEventType, AgentStep, TaskState
from app.models.workflow_models import RuntimeWorkflowState
from app.models.workflow_state import WorkflowState
from app.runtime.event_streamer import build_approval_required_event, build_pricing_payload
from app.schemas.websocket_schema import IncomingWebSocketEvent
from app.services.agent_orchestrator_service import active_tasks
from app.storage.workflow_repository import workflow_repo
from app.utils.time import utc_now_iso
from app.websocket.connection_manager import connection_manager
from app.runtime.workflow_runtime import workflow_runtime

router = APIRouter(tags=["WebSocket"])
logger = get_logger("websocket.agent")

async def heartbeat_loop(websocket: WebSocket, task_id: str, correlation_id: str):
    """
    Heartbeat checker that sends PING messages to client and closes socket if timeout exceeded.
    """
    interval = config.HEARTBEAT_INTERVAL_SECONDS
    timeout = config.HEARTBEAT_TIMEOUT_SECONDS
    logger.info("Starting heartbeat loop for task %s", task_id)
    try:
        while True:
            await asyncio.sleep(interval)
            if websocket.client_state.value == 2: # DISCONNECTED
                break
                
            # Perform ping
            try:
                await websocket.send_json({
                    "event_type": WebSocketEventType.PING,
                    "correlation_id": correlation_id,
                    "task_id": task_id,
                    "timestamp": utc_now_iso()
                })
            except Exception:
                logger.warning("Heartbeat PING write failed. Closing socket.")
                await workflow_runtime.cleanup_session(task_id)
                break
    except asyncio.CancelledError:
        pass

@router.websocket("/v1/agent/connect")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    
    correlation_id = websocket.headers.get("x-correlation-id") or str(uuid.uuid4())
    # Generate temporary task id, replaced if resuming
    task_id = str(uuid.uuid4())
    
    corr_token = set_correlation_id(correlation_id)
    task_token = set_task_id(task_id)
    
    logger.info("New WebSocket connection accepted.")
    
    # Register connection temporarily
    await connection_manager.register(task_id, websocket, correlation_id)
    heartbeat_task = asyncio.create_task(heartbeat_loop(websocket, task_id, correlation_id))
    
    orchestration_started = False
    
    try:
        while True:
            payload = await websocket.receive_json()
            await connection_manager.update_activity(task_id)
            
            if not isinstance(payload, dict):
                logger.warning("Invalid WebSocket JSON payload type.")
                continue

            try:
                incoming = IncomingWebSocketEvent.model_validate(payload)
            except ValidationError as exc:
                logger.warning("Invalid WebSocket event payload: %s", exc)
                continue
                
            event_type = incoming.event_type
            incoming_version = incoming.workflow_version if "workflow_version" in payload else None
            
            if event_type == WebSocketEventType.START_TASK:
                if orchestration_started:
                    continue
                
                client_task_id = incoming.task_id
                if client_task_id:
                    # Reconnection flow: check if task exists in DB
                    existing_session = workflow_repo.get_session(client_task_id)
                    if existing_session:
                        logger.info("Resuming active session task %s", client_task_id)
                        # Re-bind websocket
                        await connection_manager.rebind(client_task_id, websocket)
                        # Remove temporary mapping
                        await connection_manager.unregister(task_id)
                        task_id = client_task_id
                        set_task_id(task_id)

                        # Trigger status stream update upon reconnect to restore UI state
                        legacy_state = WorkflowState.from_json(existing_session.workflow_state_json)

                        tool_state_map = {
                            RuntimeWorkflowState.PLANNING: TaskState.SEARCHING_VENDORS,
                            RuntimeWorkflowState.EXECUTING: TaskState.RUNNING,
                            RuntimeWorkflowState.WAITING_APPROVAL: legacy_state.current_step or TaskState.WAITING_VENDOR_SELECTION,
                            RuntimeWorkflowState.COMPLETED: TaskState.COMPLETED,
                            RuntimeWorkflowState.FAILED: TaskState.FAILED,
                            RuntimeWorkflowState.CANCELLED: TaskState.CANCELLED
                        }
                        ui_state = tool_state_map.get(existing_session.status, TaskState.RUNNING)

                        await websocket.send_json({
                            "event_type": WebSocketEventType.STATUS_UPDATE,
                            "correlation_id": correlation_id,
                            "task_id": task_id,
                            "workflow_version": existing_session.workflow_version,
                            "task_state": ui_state.value,
                            "agent_step": legacy_state.pending_agent_step.value if legacy_state.pending_agent_step else AgentStep.SEARCHING_VENDORS.value,
                            "message": "Connection restored. Resuming workflow view...",
                            "vendors": legacy_state.research_data.get("vendors") if legacy_state.research_data else None,
                            "selected_vendor": legacy_state.selected_vendor,
                            "selected_vendors": legacy_state.selected_vendors,
                            "pricing_analysis": build_pricing_payload(legacy_state),
                            "reflection_metadata": legacy_state.reflection_metadata,
                            "draft_message": legacy_state.improved_draft or legacy_state.draft,
                            "timestamp": utc_now_iso()
                        })

                        if existing_session.status == RuntimeWorkflowState.WAITING_APPROVAL:
                            approval_messages = {
                                TaskState.WAITING_VENDOR_SELECTION: "Vendor search completed. Select candidates and approve.",
                                TaskState.WAITING_FINAL_APPROVAL: "Self-reflection completed. Approve outreach proposal draft.",
                            }
                            await websocket.send_json(
                                build_approval_required_event(
                                    correlation_id=correlation_id,
                                    task_id=task_id,
                                    workflow_version=existing_session.workflow_version,
                                    task_state=ui_state,
                                    agent_step=legacy_state.pending_agent_step or AgentStep.SELF_REFLECTION,
                                    message=approval_messages.get(ui_state, "Approval required to continue."),
                                    state=legacy_state,
                                    approval_timeout_seconds=config.WAIT_FOR_HUMAN_TIMEOUT,
                                ).model_dump()
                            )
                        orchestration_started = True
                        continue

                # Fresh task initialization path
                raw_prompt = incoming.prompt or ""
                prompt = raw_prompt.strip()
                if not prompt:
                    continue
                
                # Register in database as CREATED
                session = await workflow_runtime.get_or_create_session(task_id, prompt)
                # Rebind map
                await connection_manager.register(task_id, websocket, correlation_id)
                # Spawn background runtime execution loop
                approval_event = await workflow_runtime.start_orchestration(task_id, prompt, correlation_id)
                active_tasks[task_id] = {
                    "websocket": websocket,
                    "task": None,
                    "approval_event": approval_event,
                    "task_state": TaskState.SCHEDULED,
                    "cancelled": False,
                    "terminal_emitted": False,
                    "workflow_state": None,
                    "correlation_id": correlation_id,
                    "terminal_lock": asyncio.Lock(),
                    "last_activity_time": asyncio.get_event_loop().time(),
                }
                orchestration_started = True
                
            elif event_type == WebSocketEventType.APPROVAL_RESPONSE:
                if incoming.action:
                    await workflow_runtime.handle_approval_response(
                        task_id=task_id,
                        action=incoming.action,
                        feedback=incoming.feedback,
                        selected_vendors=incoming.selected_vendors,
                        incoming_version=incoming_version
                    )
                else:
                    logger.warning("APPROVAL_RESPONSE missing approval action.")
                    
            elif event_type == WebSocketEventType.STOP:
                logger.info("STOP signal received from client.")
                # Verify version to prevent stale STOP signals
                session = workflow_repo.get_session(task_id)
                if session and (incoming_version is None or incoming_version == session.workflow_version):
                    await workflow_runtime.cleanup_session(task_id)
                else:
                    logger.warning("Stale STOP signal version check failed.")

            elif event_type == WebSocketEventType.PONG:
                logger.debug("Received PONG heartbeat response.")

            elif event_type == WebSocketEventType.PING:
                try:
                    await websocket.send_json({
                        "event_type": WebSocketEventType.PONG,
                        "correlation_id": correlation_id,
                        "task_id": task_id,
                        "timestamp": utc_now_iso()
                    })
                except Exception:
                    pass
            else:
                logger.warning("Unknown WebSocket event: %s", event_type)

    except WebSocketDisconnect:
        logger.info("WebSocket connection closed by client.")
    except Exception as e:
        logger.exception("Unexpected error inside WebSocket loop: %s", e)
    finally:
        heartbeat_task.cancel()
        # Unregister socket but DO NOT cancel task immediately to allow reconnection within grace period
        await connection_manager.unregister(task_id)
        active_tasks.pop(task_id, None)

        correlation_id_ctx.reset(corr_token)
        task_id_ctx.reset(task_token)
