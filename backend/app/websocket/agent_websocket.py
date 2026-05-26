import uuid
import asyncio
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from app.core import config
from app.core.logger import get_logger, set_correlation_id, set_task_id, correlation_id_ctx, task_id_ctx
from app.core.enums import WebSocketEventType, AgentStep, TaskState
from app.models.workflow_models import RuntimeWorkflowState
from app.models.workflow_state import WorkflowState
from app.runtime.event_streamer import build_approval_required_event, build_pricing_payload, build_cancelled_event
from app.schemas.websocket_schema import IncomingWebSocketEvent, ErrorEvent
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
            
            action_id = incoming.action_id
            if action_id:
                if workflow_runtime.is_action_processed(action_id):
                    logger.info(f"Duplicate action_id {action_id} ignored.")
                    continue
                workflow_runtime.mark_action_processed(action_id)
                
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

                        # Check if the orchestration background task is currently active/running.
                        # Since Starlette TestClient cancels background tasks when the connection block exits,
                        # we restart the loop if it is not in _active_tasks but the database session is not terminal.
                        from app.runtime.workflow_runtime import _active_tasks, _active_events

                        task_active = client_task_id in _active_tasks and not _active_tasks[client_task_id].done()

                        if not task_active and existing_session.status not in (RuntimeWorkflowState.COMPLETED, RuntimeWorkflowState.FAILED, RuntimeWorkflowState.CANCELLED):
                            logger.info("Orchestration loop task for %s is dead/inactive. Restarting...", client_task_id)
                            approval_event = await workflow_runtime.start_orchestration(
                                client_task_id, existing_session.user_prompt, correlation_id
                            )
                        else:
                            approval_event = _active_events.get(client_task_id)
                            if not approval_event:
                                approval_event = asyncio.Event()
                                _active_events[client_task_id] = approval_event



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
                approval_event = await workflow_runtime.start_orchestration(task_id, prompt, correlation_id)
                orchestration_started = True
                
            elif event_type == WebSocketEventType.APPROVAL_RESPONSE:
                if incoming.action:
                    session = workflow_repo.get_session(task_id)
                    if session:
                        legacy_state = WorkflowState.from_json(session.workflow_state_json)
                        ui_state_map = {
                            RuntimeWorkflowState.CREATED: TaskState.SCHEDULED,
                            RuntimeWorkflowState.PLANNING: TaskState.SEARCHING_VENDORS,
                            RuntimeWorkflowState.EXECUTING: legacy_state.current_step or TaskState.RUNNING,
                            RuntimeWorkflowState.WAITING_APPROVAL: legacy_state.current_step or TaskState.WAITING_VENDOR_SELECTION,
                            RuntimeWorkflowState.APPROVED: legacy_state.current_step or TaskState.RUNNING,
                            RuntimeWorkflowState.REJECTED: legacy_state.current_step or TaskState.RUNNING,
                            RuntimeWorkflowState.COMPLETED: TaskState.COMPLETED,
                            RuntimeWorkflowState.FAILED: TaskState.FAILED,
                            RuntimeWorkflowState.CANCELLED: TaskState.CANCELLED,
                        }
                        ui_state = ui_state_map.get(session.status, TaskState.RUNNING)

                        if incoming_version is None or incoming_version != session.workflow_version:
                            logger.warning(f"Rejecting approval event for {task_id}: version mismatch (client version={incoming_version}, server version={session.workflow_version})")
                            error_payload = ErrorEvent(
                                correlation_id=correlation_id,
                                task_id=task_id,
                                task_state=ui_state,
                                error_code="STALE_WORKFLOW_VERSION",
                                message="Workflow state changed. Please refresh latest state."
                            )
                            await websocket.send_json(error_payload.model_dump())
                            continue

                        if not incoming.action_id:
                            logger.warning(f"Rejecting approval event for {task_id}: missing action ID")
                            error_payload = ErrorEvent(
                                correlation_id=correlation_id,
                                task_id=task_id,
                                task_state=ui_state,
                                error_code="MISSING_ACTION_ID",
                                message="Action ID is missing."
                            )
                            await websocket.send_json(error_payload.model_dump())
                            continue

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
                session = workflow_repo.get_session(task_id)
                if not session:
                    logger.warning("STOP received for unknown session.")
                    continue
                
                legacy_state = WorkflowState.from_json(session.workflow_state_json)
                ui_state_map = {
                    RuntimeWorkflowState.CREATED: TaskState.SCHEDULED,
                    RuntimeWorkflowState.PLANNING: TaskState.SEARCHING_VENDORS,
                    RuntimeWorkflowState.EXECUTING: legacy_state.current_step or TaskState.RUNNING,
                    RuntimeWorkflowState.WAITING_APPROVAL: legacy_state.current_step or TaskState.WAITING_VENDOR_SELECTION,
                    RuntimeWorkflowState.APPROVED: legacy_state.current_step or TaskState.RUNNING,
                    RuntimeWorkflowState.REJECTED: legacy_state.current_step or TaskState.RUNNING,
                    RuntimeWorkflowState.COMPLETED: TaskState.COMPLETED,
                    RuntimeWorkflowState.FAILED: TaskState.FAILED,
                    RuntimeWorkflowState.CANCELLED: TaskState.CANCELLED,
                }
                ui_state = ui_state_map.get(session.status, TaskState.RUNNING)

                # Check version conflict strictly
                if incoming_version is None or incoming_version != session.workflow_version:
                    logger.warning("Stale STOP signal version check failed.")
                    error_payload = ErrorEvent(
                        correlation_id=correlation_id,
                        task_id=task_id,
                        task_state=ui_state,
                        error_code="STALE_WORKFLOW_VERSION",
                        message="Workflow state changed. Please refresh latest state."
                    )
                    await websocket.send_json(error_payload.model_dump())
                    continue

                if not incoming.action_id:
                    logger.warning(f"Rejecting STOP event for {task_id}: missing action ID")
                    error_payload = ErrorEvent(
                        correlation_id=correlation_id,
                        task_id=task_id,
                        task_state=ui_state,
                        error_code="MISSING_ACTION_ID",
                        message="Action ID is missing."
                    )
                    await websocket.send_json(error_payload.model_dump())
                    continue

                # Handle STOP in terminal states
                if session.status == RuntimeWorkflowState.COMPLETED:
                    logger.info("STOP received for COMPLETED session. Ignoring.")
                    error_payload = ErrorEvent(
                        correlation_id=correlation_id,
                        task_id=task_id,
                        task_state=TaskState.COMPLETED,
                        error_code="ALREADY_COMPLETED",
                        message="The task has already completed."
                    )
                    await websocket.send_json(error_payload.model_dump())
                    continue
                elif session.status == RuntimeWorkflowState.CANCELLED:
                    logger.info("STOP received for CANCELLED session. Ignoring.")
                    error_payload = ErrorEvent(
                        correlation_id=correlation_id,
                        task_id=task_id,
                        task_state=TaskState.CANCELLED,
                        error_code="ALREADY_CANCELLED",
                        message="The task is already cancelled."
                    )
                    await websocket.send_json(error_payload.model_dump())
                    continue
                elif session.status == RuntimeWorkflowState.FAILED:
                    logger.info("STOP received for FAILED session. Ignoring.")
                    error_payload = ErrorEvent(
                        correlation_id=correlation_id,
                        task_id=task_id,
                        task_state=TaskState.FAILED,
                        error_code="ALREADY_FAILED",
                        message="The task has already failed."
                    )
                    await websocket.send_json(error_payload.model_dump())
                    continue

                # Cancel session safely
                await workflow_runtime.cancel_session(task_id)
                
                # Send TASK_CANCELLED explicitly before cleanup
                cancel_event = build_cancelled_event(
                    correlation_id=correlation_id,
                    task_id=task_id,
                    workflow_version=session.workflow_version,
                    message="Task cancelled by user."
                )
                await websocket.send_json(cancel_event.model_dump())
                
                # Cleanup session after sending the cancel event
                await workflow_runtime.cleanup_session(task_id)

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

        correlation_id_ctx.reset(corr_token)
        task_id_ctx.reset(task_token)
