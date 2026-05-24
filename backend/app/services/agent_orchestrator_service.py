import asyncio
from typing import Dict, Any, Optional
from fastapi import WebSocket
from starlette.websockets import WebSocketState

from app.core.logger import get_logger, set_correlation_id, set_task_id, correlation_id_ctx, task_id_ctx
from app.core.enums import TaskState, AgentStep, ApprovalAction
from app.core import config
from app.services.llm_service import get_provider_name
from app.models.workflow_state import WorkflowState
from app.schemas.websocket_schema import (
    StatusUpdateEvent,
    ApprovalRequiredEvent,
    TaskCompletedEvent,
    TaskCancelledEvent,
    ErrorEvent
)
from app.tools.research_tool import research_tool
from app.tools.analysis_tool import analysis_tool
from app.tools.draft_tool import draft_tool
from app.tools.reflection_tool import reflection_tool
from app.tools.execution_tool import execution_tool
from app.repositories.task_repository import task_repo

logger = get_logger("services.agent_orchestrator")

# In-memory registry tracking active WebSocket orchestration sessions.
# Intentionally kept as runtime-only state — each task is scoped to a single
# WebSocket lifecycle and cleaned up on completion, cancellation, or disconnect.
# Databases/Redis are avoided: orchestration state is ephemeral and session-bound.
active_tasks: Dict[str, Dict[str, Any]] = {}

# Async lock protecting active_tasks registry mutations.
# Ensures concurrency-safe state transitions as recommended in coding guidelines (Section 4).
_tasks_lock = asyncio.Lock()

# Allowed state transitions for the workflow state machine
VALID_TRANSITIONS = {
    TaskState.SCHEDULED: {TaskState.RUNNING, TaskState.CANCELLED},
    TaskState.RUNNING: {
        TaskState.WAITING_APPROVAL,
        TaskState.EXECUTING,
        TaskState.FAILED,
        TaskState.CANCELLED,
        TaskState.EXTERNAL_SEARCHING,
        TaskState.FAILED_RETRYING
    },
    TaskState.EXTERNAL_SEARCHING: {
        TaskState.RUNNING,
        TaskState.WAITING_APPROVAL,
        TaskState.FAILED,
        TaskState.CANCELLED
    },
    TaskState.FAILED_RETRYING: {
        TaskState.RUNNING,
        TaskState.EXTERNAL_SEARCHING,
        TaskState.FAILED,
        TaskState.CANCELLED
    },
    TaskState.WAITING_APPROVAL: {
        TaskState.RUNNING,
        TaskState.EXECUTING,
        TaskState.CANCELLED,
        TaskState.FAILED
    },
    TaskState.EXECUTING: {
        TaskState.SUCCESS,
        TaskState.FAILED,
        TaskState.CANCELLED
    },
    TaskState.SUCCESS: set(),
    TaskState.FAILED: set(),
    TaskState.CANCELLED: set(),
}

def transition_task_state(task_id: str, new_state: TaskState) -> bool:
    """
    Safely transitions the task state if the transition is allowed.
    Returns True if transitioned successfully, False otherwise.
    """
    task_info = active_tasks.get(task_id)
    if not task_info:
        logger.warning("Attempted to transition non-existent task %s to state: %s", task_id, new_state)
        return False
        
    current_state = task_info.get("task_state")
    allowed = VALID_TRANSITIONS.get(current_state, set())
    if new_state not in allowed:
        logger.warning(
            "Invalid task state transition requested for task %s: %s -> %s (ignored)",
            task_id, current_state, new_state
        )
        return False
        
    task_info["task_state"] = new_state
    logger.info("Task %s transitioned: %s -> %s", task_id, current_state, new_state)
    return True

def is_websocket_active(websocket: WebSocket) -> bool:
    """
    Checks if a websocket connection is already associated with an active orchestration task.
    """
    for task_info in active_tasks.values():
        if task_info.get("websocket") == websocket:
            if task_info.get("task_state") not in (TaskState.SUCCESS, TaskState.FAILED, TaskState.CANCELLED):
                return True
    return False

async def register_task(task_id: str, websocket: WebSocket, approval_event: asyncio.Event, correlation_id: str = "") -> None:
    """
    Registers a new active WebSocket task session in the registry in SCHEDULED state.
    """
    async with _tasks_lock:
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
            "last_activity_time": asyncio.get_event_loop().time()
        }
    logger.info("Orchestration task registered in registry")

def set_task_reference(task_id: str, task: asyncio.Task) -> None:
    """
    Associates the asyncio.Task reference with the registered task.
    """
    if task_id in active_tasks:
        active_tasks[task_id]["task"] = task

def handle_approval_response(task_id: str, action: ApprovalAction, feedback: Optional[str] = None) -> None:
    """
    Handles human-in-the-loop approval actions (APPROVE/REJECT).
    Idempotent — duplicate events are safely ignored.
    """
    task_info = active_tasks.get(task_id)
    if not task_info:
        logger.warning("Approval received for unknown task %s (ignored)", task_id)
        return

    state = task_info.get("task_state")
    if state != TaskState.WAITING_APPROVAL:
        logger.warning("Task received approval response but is in state: %s", state)
        return

    approval_event = task_info.get("approval_event")
    if not approval_event:
        logger.warning("Approval event missing for task %s (ignored)", task_id)
        return

    if approval_event.is_set():
        logger.warning("Duplicate approval event received (ignored)")
        return

    workflow_state = task_info.get("workflow_state")
    if workflow_state:
        workflow_state.approval_action = action
        workflow_state.rejection_feedback = feedback

    logger.info("Approval response received: %s", action)
    approval_event.set()

async def cancel_task(task_id: str) -> None:
    """
    Cancels the active task and interrupts the background orchestration task.
    """
    async with _tasks_lock:
        task_info = active_tasks.get(task_id)
        if not task_info:
            return
            
        if task_info.get("task_state") == TaskState.CANCELLED:
            return
            
        if not transition_task_state(task_id, TaskState.CANCELLED):
            logger.debug("Task state transition to CANCELLED failed or already terminal")
            return
            
        task_info["cancelled"] = True
    
    asyncio_task = task_info.get("task")
    if asyncio_task and not asyncio_task.done():
        logger.info("Orchestration cancelled")
        asyncio_task.cancel()
        await asyncio.gather(asyncio_task, return_exceptions=True)
        
async def cleanup_task(task_id: str) -> None:
    """
    Removes the task from the registry to prevent memory leaks.
    """
    async with _tasks_lock:
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

async def send_terminal_event(websocket: WebSocket, task_id: str, event: Any) -> None:
    """
    Sends a terminal event ensuring it's emitted only once, and closes the WebSocket connection.
    Protected under the task's terminal lock.
    """
    task_info = active_tasks.get(task_id)
    if not task_info:
        await safe_send_json(websocket, event.model_dump())
        try:
            await websocket.close()
        except Exception:
            pass
        return
        
    terminal_lock = task_info.get("terminal_lock")
    if terminal_lock:
        async with terminal_lock:
            if task_info.get("terminal_emitted"):
                return
            task_info["terminal_emitted"] = True
            await safe_send_json(websocket, event.model_dump())
            try:
                await websocket.close()
            except Exception:
                pass
    else:
        if task_info.get("terminal_emitted"):
            return
        task_info["terminal_emitted"] = True
        await safe_send_json(websocket, event.model_dump())
        try:
            await websocket.close()
        except Exception:
            pass

async def cancel_task(task_id: str) -> None:
    """
    Cancels the active task and interrupts the background orchestration task.
    """
    task_info = active_tasks.get(task_id)
    if not task_info:
        return
        
    terminal_lock = task_info.get("terminal_lock")
    if terminal_lock:
        async with terminal_lock:
            if task_info.get("task_state") in (TaskState.SUCCESS, TaskState.FAILED, TaskState.CANCELLED):
                return
            old_state = task_info.get("task_state")
            if not transition_task_state(task_id, TaskState.CANCELLED):
                logger.debug("Task state transition to CANCELLED failed or already terminal")
                return
            task_info["cancelled"] = True
            task_repo.update_task_status(task_id, old_state, TaskState.CANCELLED)
            
            # Emit cancellation event to client
            websocket = task_info.get("websocket")
            if websocket:
                correlation_id = task_info.get("correlation_id", "")
                cancelled_event = TaskCancelledEvent(
                    correlation_id=correlation_id,
                    task_id=task_id,
                    task_state=TaskState.CANCELLED,
                    message="Orchestration cancelled by client."
                )
                task_info["terminal_emitted"] = True
                await safe_send_json(websocket, cancelled_event.model_dump())
                try:
                    await websocket.close()
                except Exception:
                    pass
    else:
        if task_info.get("task_state") == TaskState.CANCELLED:
            return
        old_state = task_info.get("task_state")
        if not transition_task_state(task_id, TaskState.CANCELLED):
            return
        task_info["cancelled"] = True
        task_repo.update_task_status(task_id, old_state, TaskState.CANCELLED)
        
    asyncio_task = task_info.get("task")
    if asyncio_task and not asyncio_task.done():
        logger.info("Orchestration cancelled")
        asyncio_task.cancel()
        await asyncio.gather(asyncio_task, return_exceptions=True)

async def _await_step_approval(
    websocket: WebSocket,
    correlation_id: str,
    task_id: str,
    state: WorkflowState,
    agent_step: AgentStep,
    step_data: str,
    message: str,
) -> bool:
    """
    Pauses the workflow and waits for human approval of a step's output.
    Returns True if APPROVED, False if REJECTED or MODIFY_REQUEST (for retry/regeneration).
    Raises asyncio.TimeoutError on timeout, cancels on cancellation.
    """
    state.pending_agent_step = agent_step
    state.pending_step_data = step_data

    task_info = active_tasks.get(task_id)
    if not task_info:
        logger.warning("Task %s removed during step approval (aborting)", task_id)
        return False
        
    old_state = task_info.get("task_state")
    if not transition_task_state(task_id, TaskState.WAITING_APPROVAL):
        logger.warning("Aborting: failed to transition to WAITING_APPROVAL for step %s", agent_step)
        return False
    task_repo.update_task_status(task_id, old_state, TaskState.WAITING_APPROVAL)

    approval_event = task_info.get("approval_event")
    if not approval_event:
        logger.warning("Approval event missing for task %s (aborting)", task_id)
        return False

    app_req_event = ApprovalRequiredEvent(
        correlation_id=correlation_id,
        task_id=task_id,
        task_state=TaskState.WAITING_APPROVAL,
        agent_step=agent_step,
        draft_message=step_data,
        step_data=step_data,
        message=message,
        approval_timeout_seconds=config.APPROVAL_TIMEOUT_SECONDS,
        reflection_metadata=state.reflection_metadata if agent_step == AgentStep.SELF_REFLECTION else None
    )
    await safe_send_json(websocket, app_req_event.model_dump())

    # Wait for approval with timeout
    logger.info("Approval timeout started (%s seconds) for step %s", config.APPROVAL_TIMEOUT_SECONDS, agent_step)
    try:
        await asyncio.wait_for(approval_event.wait(), timeout=config.APPROVAL_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        logger.warning("Approval timeout exceeded for step %s. Task cancelled automatically.", agent_step)
        
        async with task_info["terminal_lock"]:
            if task_info.get("task_state") not in (TaskState.SUCCESS, TaskState.FAILED, TaskState.CANCELLED):
                transition_task_state(task_id, TaskState.CANCELLED)
                task_info["cancelled"] = True
                task_repo.update_task_status(task_id, TaskState.WAITING_APPROVAL, TaskState.CANCELLED)
                
                cancelled_event = TaskCancelledEvent(
                    correlation_id=correlation_id,
                    task_id=task_id,
                    task_state=TaskState.CANCELLED,
                    message=f"Approval timeout exceeded at step {agent_step.value}. Task cancelled automatically.",
                )
                task_info["terminal_emitted"] = True
                await safe_send_json(websocket, cancelled_event.model_dump())
                try:
                    await websocket.close()
                except Exception:
                    pass
        raise asyncio.TimeoutError()

    # Check if task was cancelled while waiting
    task_info = active_tasks.get(task_id)
    if not task_info or task_info.get("cancelled"):
        raise asyncio.CancelledError()

    # Handle rejection / modification response
    action = state.approval_action
    if action in (ApprovalAction.REJECT, ApprovalAction.MODIFY_REQUEST):
        logger.info("Step %s received %s. Re-running step...", agent_step, action.value)
        transition_task_state(task_id, TaskState.RUNNING)
        task_repo.update_task_status(task_id, TaskState.WAITING_APPROVAL, TaskState.RUNNING)
        return False

    # APPROVED
    if state.rejection_feedback:
        logger.info("Step %s approved with feedback. Proceeding...", agent_step)
    else:
        logger.info("Step %s approved. Proceeding...", agent_step)
        
    transition_task_state(task_id, TaskState.RUNNING)
    task_repo.update_task_status(task_id, TaskState.WAITING_APPROVAL, TaskState.RUNNING)
    return True

def _extract_vendor_from_feedback(feedback: Optional[str], known_vendors: list) -> Optional[dict]:
    """
    Checks if the user's feedback mentions a specific vendor name from the
    known vendor list. Returns the matching vendor dict if found, else None.
    """
    if not feedback or not known_vendors:
        return None
    feedback_lower = feedback.lower()
    for vendor in known_vendors:
        vname = vendor.get("vendor_name", "").lower()
        if vname in feedback_lower:
            logger.info("User feedback matched vendor '%s' from research list", vendor.get("vendor_name"))
            return vendor
    return None

async def run_orchestration(websocket: WebSocket, correlation_id: str, task_id: str, state: WorkflowState) -> None:
    """
    Executes the async agent orchestration workflow using shared WorkflowState.
    Flow: SEARCHING_VENDORS -> ANALYZING_PRICING -> DRAFTING_OUTREACH -> SELF_REFLECTION -> EXECUTING
    Each step is evaluated, logged, and awaits approval.
    """
    corr_token = set_correlation_id(correlation_id)
    task_token = set_task_id(task_id)
    
    task_entry = active_tasks.get(task_id)
    if task_entry:
        task_entry["workflow_state"] = state
        
    logger.info("Orchestration workflow started with prompt: %.100s", state.prompt)
    
    try:
        # Optimistic load recent task memory context
        try:
            recent = task_repo.get_recent_successful_tasks(limit=3)
            if recent:
                memory_parts = []
                for t in recent:
                    memory_parts.append(
                        f"Prompt: {t['user_prompt']}\nTargeted: {t['memory'].get('vendor_name')}\nOutput: {t['final_output']}"
                    )
                state.memory_context = "\n---\n".join(memory_parts)
                logger.info("Loaded task history context into state.memory_context")
        except Exception as e:
            logger.warning("Failed to load task history for memory context: %s", e)

        # Transition SCHEDULED -> RUNNING
        if not transition_task_state(task_id, TaskState.RUNNING):
            logger.warning("Aborting orchestration run: failed to transition task state to RUNNING")
            return
        task_repo.update_task_status(task_id, TaskState.SCHEDULED, TaskState.RUNNING)

        # =====================================================================
        # Step 1: SEARCHING_VENDORS
        # =====================================================================
        research_approved = False
        while not research_approved:
            if active_tasks.get(task_id, {}).get("cancelled"):
                raise asyncio.CancelledError()

            logger.info("Entering step: SEARCHING_VENDORS")
            event = StatusUpdateEvent(
                correlation_id=correlation_id,
                task_id=task_id,
                task_state=TaskState.RUNNING,
                agent_step=AgentStep.SEARCHING_VENDORS,
                message="Searching local database and online catalogs..."
            )
            await safe_send_json(websocket, event.model_dump())

            # Call tool with transient retry state logging
            try:
                await research_tool(state)
            except Exception as e:
                logger.warning("Research tool failed, entering retry state...")
                transition_task_state(task_id, TaskState.FAILED_RETRYING)
                task_repo.update_task_status(task_id, TaskState.RUNNING, TaskState.FAILED_RETRYING)
                await asyncio.sleep(2.0)
                transition_task_state(task_id, TaskState.RUNNING)
                task_repo.update_task_status(task_id, TaskState.FAILED_RETRYING, TaskState.RUNNING)
                await research_tool(state)

            research_data_str = ""
            if state.research_data:
                vendors = state.research_data.get("vendors", [])
                market_insights = state.research_data.get("market_insights", "")
                vendor_list = "\n".join(
                    [f"• {v.get('vendor_name') or v.get('name', 'Unknown')} (Rating: {v.get('rating', 'N/A')}, Location: {v.get('location', 'N/A')})" for v in vendors]
                ) if vendors else "No vendors found."
                research_data_str = (
                    f"Vendors Discovered:\n{vendor_list}\n\n"
                    f"Market Insights: {market_insights}"
                )

            research_approved = await _await_step_approval(
                websocket=websocket,
                correlation_id=correlation_id,
                task_id=task_id,
                state=state,
                agent_step=AgentStep.SEARCHING_VENDORS,
                step_data=research_data_str,
                message="Vendor discovery complete. Review candidates and approve to analyze pricing.",
            )
            
            # Reset feedback
            if not research_approved:
                state.rejection_feedback = None

        state.pending_agent_step = None
        state.pending_step_data = None

        # =====================================================================
        # Step 2: ANALYZING_PRICING
        # =====================================================================
        analysis_approved = False
        while not analysis_approved:
            if active_tasks.get(task_id, {}).get("cancelled"):
                raise asyncio.CancelledError()

            logger.info("Entering step: ANALYZING_PRICING")
            event = StatusUpdateEvent(
                correlation_id=correlation_id,
                task_id=task_id,
                task_state=TaskState.RUNNING,
                agent_step=AgentStep.ANALYZING_PRICING,
                message="Analyzing catalogs and pricing..."
            )
            await safe_send_json(websocket, event.model_dump())

            try:
                await analysis_tool(state)
            except Exception as e:
                logger.warning("Analysis tool failed, retrying...")
                transition_task_state(task_id, TaskState.FAILED_RETRYING)
                task_repo.update_task_status(task_id, TaskState.RUNNING, TaskState.FAILED_RETRYING)
                await asyncio.sleep(2.0)
                transition_task_state(task_id, TaskState.RUNNING)
                task_repo.update_task_status(task_id, TaskState.FAILED_RETRYING, TaskState.RUNNING)
                await analysis_tool(state)

            analysis_str = state.analysis_summary or "Analysis completed."
            if state.selected_vendor:
                analysis_str += f"\n\nSelected Vendor: {state.selected_vendor.get('vendor_name') or state.selected_vendor.get('name', 'Unknown')} ({state.selected_vendor.get('location', 'N/A')})"

            analysis_approved = await _await_step_approval(
                websocket=websocket,
                correlation_id=correlation_id,
                task_id=task_id,
                state=state,
                agent_step=AgentStep.ANALYZING_PRICING,
                step_data=analysis_str,
                message="Pricing analysis complete. Approve to generate outreach draft, or reject to adjust criteria.",
            )

            if not analysis_approved:
                # Support forcing a specific vendor from user feedback
                vendors = state.research_data.get("vendors", []) if state.research_data else []
                matching_vendor = _extract_vendor_from_feedback(state.rejection_feedback, vendors)

                if matching_vendor:
                    logger.info("User requested vendor focus: '%s'. Re-running research...", matching_vendor.get("vendor_name") or matching_vendor.get("name", "Unknown"))
                    state.selected_vendor = matching_vendor
                    state.rejection_feedback = None
                    # Clear approval event
                    task_info = active_tasks.get(task_id)
                    if task_info:
                        ae = task_info.get("approval_event")
                        if ae:
                            ae.clear()
                    # Re-run research focusing on preference
                    await research_tool(state)
                    await analysis_tool(state)
                else:
                    state.approval_action = None
                    task_info = active_tasks.get(task_id)
                    if task_info:
                        ae = task_info.get("approval_event")
                        if ae:
                            ae.clear()

        state.pending_agent_step = None
        state.pending_step_data = None

        # =====================================================================
        # Step 3: DRAFTING_OUTREACH
        # =====================================================================
        draft_approved = False
        while not draft_approved:
            if active_tasks.get(task_id, {}).get("cancelled"):
                raise asyncio.CancelledError()

            logger.info("Entering step: DRAFTING_OUTREACH")
            event = StatusUpdateEvent(
                correlation_id=correlation_id,
                task_id=task_id,
                task_state=TaskState.RUNNING,
                agent_step=AgentStep.DRAFTING_OUTREACH,
                message="Drafting outreach communication..."
            )
            await safe_send_json(websocket, event.model_dump())

            try:
                await draft_tool(state)
            except Exception as e:
                logger.warning("Draft tool failed, retrying...")
                transition_task_state(task_id, TaskState.FAILED_RETRYING)
                task_repo.update_task_status(task_id, TaskState.RUNNING, TaskState.FAILED_RETRYING)
                await asyncio.sleep(2.0)
                transition_task_state(task_id, TaskState.RUNNING)
                task_repo.update_task_status(task_id, TaskState.FAILED_RETRYING, TaskState.RUNNING)
                await draft_tool(state)

            draft_str = state.draft or "Draft completed."

            draft_approved = await _await_step_approval(
                websocket=websocket,
                correlation_id=correlation_id,
                task_id=task_id,
                state=state,
                agent_step=AgentStep.DRAFTING_OUTREACH,
                step_data=draft_str,
                message="Outreach draft generated. Approve to run self-reflection audit, or modify.",
            )
            
            if not draft_approved:
                # Capture modification instructions
                state.approval_action = None
                task_info = active_tasks.get(task_id)
                if task_info:
                    ae = task_info.get("approval_event")
                    if ae:
                        ae.clear()

        state.pending_agent_step = None
        state.pending_step_data = None

        # =====================================================================
        # Step 4: SELF_REFLECTION
        # =====================================================================
        reflection_approved = False
        while not reflection_approved:
            if active_tasks.get(task_id, {}).get("cancelled"):
                raise asyncio.CancelledError()

            logger.info("Entering step: SELF_REFLECTION")
            event = StatusUpdateEvent(
                correlation_id=correlation_id,
                task_id=task_id,
                task_state=TaskState.RUNNING,
                agent_step=AgentStep.SELF_REFLECTION,
                message="Running self-reflection quality audit..."
            )
            await safe_send_json(websocket, event.model_dump())

            try:
                await reflection_tool(state)
            except Exception as e:
                logger.warning("Reflection tool failed, retrying...")
                transition_task_state(task_id, TaskState.FAILED_RETRYING)
                task_repo.update_task_status(task_id, TaskState.RUNNING, TaskState.FAILED_RETRYING)
                await asyncio.sleep(2.0)
                transition_task_state(task_id, TaskState.RUNNING)
                task_repo.update_task_status(task_id, TaskState.FAILED_RETRYING, TaskState.RUNNING)
                await reflection_tool(state)

            reflect_str = state.improved_draft or "Reflection completed."

            reflection_approved = await _await_step_approval(
                websocket=websocket,
                correlation_id=correlation_id,
                task_id=task_id,
                state=state,
                agent_step=AgentStep.SELF_REFLECTION,
                step_data=reflect_str,
                message="Self-reflection completed. Approve to execute final outreach, or modify.",
            )
            
            if not reflection_approved:
                # If modification requested, loop back to regenerate draft incorporating feedback
                state.approval_action = None
                task_info = active_tasks.get(task_id)
                if task_info:
                    ae = task_info.get("approval_event")
                    if ae:
                        ae.clear()

        state.pending_agent_step = None
        state.pending_step_data = None

        # =====================================================================
        # Step 5: EXECUTING
        # =====================================================================
        logger.info("Entering step: EXECUTING")
        if not transition_task_state(task_id, TaskState.EXECUTING):
            logger.warning("Aborting: failed to transition to EXECUTING")
            return
        task_repo.update_task_status(task_id, TaskState.RUNNING, TaskState.EXECUTING)

        event = StatusUpdateEvent(
            correlation_id=correlation_id,
            task_id=task_id,
            task_state=TaskState.EXECUTING,
            agent_step=AgentStep.EXECUTING,
            message="Executing final procurement outreach..."
        )
        await safe_send_json(websocket, event.model_dump())

        try:
            await execution_tool(state)
        except Exception as e:
            logger.exception("Execution approved, but workflow execution failed")
            
            async with task_info["terminal_lock"]:
                if task_info.get("task_state") not in (TaskState.SUCCESS, TaskState.FAILED, TaskState.CANCELLED):
                    transition_task_state(task_id, TaskState.FAILED)
                    task_repo.update_task_status(task_id, TaskState.EXECUTING, TaskState.FAILED)
                    
                    error_event = ErrorEvent(
                        correlation_id=correlation_id,
                        task_id=task_id,
                        task_state=TaskState.FAILED,
                        error_code="EXECUTION_FAILED",
                        message="Execution approved, but final delivery failed. Please verify configuration."
                    )
                    task_info["terminal_emitted"] = True
                    await safe_send_json(websocket, error_event.model_dump())
                    try:
                        await websocket.close()
                    except Exception:
                        pass
            return

        # SUCCESS
        async with task_info["terminal_lock"]:
            if task_info.get("task_state") not in (TaskState.SUCCESS, TaskState.FAILED, TaskState.CANCELLED):
                if transition_task_state(task_id, TaskState.SUCCESS):
                    # Save DB Task status
                    task_repo.update_task_status(task_id, TaskState.EXECUTING, TaskState.SUCCESS)
                    task_repo.update_task_final_output(task_id, state.execution_result or "")
                    
                    # Store memory context for future preferences
                    memory_data = {
                        "category": state.research_data.get("category") if state.research_data else None,
                        "vendor_name": (state.selected_vendor.get("vendor_name") or state.selected_vendor.get("name")) if state.selected_vendor else None,
                        "draft": state.improved_draft or state.draft
                    }
                    task_repo.update_task_memory(task_id, memory_data)
                    
                    completed_event = TaskCompletedEvent(
                        correlation_id=correlation_id,
                        task_id=task_id,
                        task_state=TaskState.SUCCESS,
                        message=f"Task successfully completed. Result: {state.execution_result}",
                        final_response=state.improved_draft or state.draft or ""
                    )
                    task_info["terminal_emitted"] = True
                    await safe_send_json(websocket, completed_event.model_dump())
                    try:
                        await websocket.close()
                    except Exception:
                        pass
                    logger.info("Orchestration completed successfully")

    except asyncio.TimeoutError:
        logger.info("Orchestration ended due to approval timeout")
    except asyncio.CancelledError:
        logger.info("Orchestration cancelled exception caught")
        if websocket.client_state != WebSocketState.CONNECTED:
            return
            
        task_info = active_tasks.get(task_id)
        if task_info:
            async with task_info["terminal_lock"]:
                if task_info.get("task_state") in (TaskState.SUCCESS, TaskState.FAILED, TaskState.CANCELLED):
                    return
                current_state = task_info.get("task_state")
                transition_task_state(task_id, TaskState.CANCELLED)
                task_repo.update_task_status(task_id, current_state, TaskState.CANCELLED)
                
                cancelled_event = TaskCancelledEvent(
                    correlation_id=correlation_id,
                    task_id=task_id,
                    task_state=TaskState.CANCELLED,
                    message="Orchestration cancelled by client."
                )
                task_info["terminal_emitted"] = True
                await safe_send_json(websocket, cancelled_event.model_dump())
                try:
                    await websocket.close()
                except Exception:
                    pass
    except Exception as e:
        logger.exception("Orchestration unexpected failure")
        task_info = active_tasks.get(task_id)
        if task_info:
            async with task_info["terminal_lock"]:
                if task_info.get("task_state") not in (TaskState.SUCCESS, TaskState.FAILED, TaskState.CANCELLED):
                    current_state = task_info.get("task_state")
                    transition_task_state(task_id, TaskState.FAILED)
                    task_repo.update_task_status(task_id, current_state, TaskState.FAILED)
                    
                    error_event = ErrorEvent(
                        correlation_id=correlation_id,
                        task_id=task_id,
                        task_state=TaskState.FAILED,
                        error_code="ORCHESTRATION_FAILURE",
                        message="An unexpected system error occurred. Please verify configuration."
                    )
                    task_info["terminal_emitted"] = True
                    await safe_send_json(websocket, error_event.model_dump())
                    try:
                        await websocket.close()
                    except Exception:
                        pass
    finally:
        correlation_id_ctx.reset(corr_token)
        task_id_ctx.reset(task_token)
        await cleanup_task(task_id)
