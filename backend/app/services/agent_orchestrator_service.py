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
# Flexible and planner-friendly: any active state can transition to any other active/terminal state.
VALID_TRANSITIONS = {
    TaskState.SCHEDULED: {
        TaskState.RUNNING,
        TaskState.SEARCHING_VENDORS,
        TaskState.EXTERNAL_SEARCHING,
        TaskState.ANALYZING_PRICING,
        TaskState.DRAFTING_OUTREACH,
        TaskState.SELF_REFLECTION,
        TaskState.WAITING_FINAL_APPROVAL,
        TaskState.COMPLETED,
        TaskState.FAILED,
        TaskState.CANCELLED,
        TaskState.FAILED_RETRYING
    },
    TaskState.RUNNING: {
        TaskState.SEARCHING_VENDORS,
        TaskState.EXTERNAL_SEARCHING,
        TaskState.ANALYZING_PRICING,
        TaskState.DRAFTING_OUTREACH,
        TaskState.SELF_REFLECTION,
        TaskState.WAITING_FINAL_APPROVAL,
        TaskState.COMPLETED,
        TaskState.FAILED,
        TaskState.CANCELLED,
        TaskState.FAILED_RETRYING
    },
    TaskState.SEARCHING_VENDORS: {
        TaskState.RUNNING,
        TaskState.EXTERNAL_SEARCHING,
        TaskState.ANALYZING_PRICING,
        TaskState.DRAFTING_OUTREACH,
        TaskState.SELF_REFLECTION,
        TaskState.WAITING_FINAL_APPROVAL,
        TaskState.COMPLETED,
        TaskState.FAILED,
        TaskState.CANCELLED,
        TaskState.FAILED_RETRYING
    },
    TaskState.EXTERNAL_SEARCHING: {
        TaskState.RUNNING,
        TaskState.SEARCHING_VENDORS,
        TaskState.ANALYZING_PRICING,
        TaskState.DRAFTING_OUTREACH,
        TaskState.SELF_REFLECTION,
        TaskState.WAITING_FINAL_APPROVAL,
        TaskState.COMPLETED,
        TaskState.FAILED,
        TaskState.CANCELLED,
        TaskState.FAILED_RETRYING
    },
    TaskState.ANALYZING_PRICING: {
        TaskState.RUNNING,
        TaskState.SEARCHING_VENDORS,
        TaskState.EXTERNAL_SEARCHING,
        TaskState.DRAFTING_OUTREACH,
        TaskState.SELF_REFLECTION,
        TaskState.WAITING_FINAL_APPROVAL,
        TaskState.COMPLETED,
        TaskState.FAILED,
        TaskState.CANCELLED,
        TaskState.FAILED_RETRYING
    },
    TaskState.DRAFTING_OUTREACH: {
        TaskState.RUNNING,
        TaskState.SEARCHING_VENDORS,
        TaskState.EXTERNAL_SEARCHING,
        TaskState.ANALYZING_PRICING,
        TaskState.SELF_REFLECTION,
        TaskState.WAITING_FINAL_APPROVAL,
        TaskState.COMPLETED,
        TaskState.FAILED,
        TaskState.CANCELLED,
        TaskState.FAILED_RETRYING
    },
    TaskState.SELF_REFLECTION: {
        TaskState.RUNNING,
        TaskState.SEARCHING_VENDORS,
        TaskState.EXTERNAL_SEARCHING,
        TaskState.ANALYZING_PRICING,
        TaskState.DRAFTING_OUTREACH,
        TaskState.WAITING_FINAL_APPROVAL,
        TaskState.COMPLETED,
        TaskState.FAILED,
        TaskState.CANCELLED,
        TaskState.FAILED_RETRYING
    },
    TaskState.WAITING_FINAL_APPROVAL: {
        TaskState.RUNNING,
        TaskState.SEARCHING_VENDORS,
        TaskState.EXTERNAL_SEARCHING,
        TaskState.ANALYZING_PRICING,
        TaskState.DRAFTING_OUTREACH,
        TaskState.SELF_REFLECTION,
        TaskState.COMPLETED,
        TaskState.FAILED,
        TaskState.CANCELLED,
        TaskState.FAILED_RETRYING
    },
    TaskState.FAILED_RETRYING: {
        TaskState.RUNNING,
        TaskState.SEARCHING_VENDORS,
        TaskState.EXTERNAL_SEARCHING,
        TaskState.ANALYZING_PRICING,
        TaskState.DRAFTING_OUTREACH,
        TaskState.SELF_REFLECTION,
        TaskState.WAITING_FINAL_APPROVAL,
        TaskState.COMPLETED,
        TaskState.FAILED,
        TaskState.CANCELLED
    },
    TaskState.COMPLETED: set(),
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
    if current_state == new_state:
        return True
        
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
            if task_info.get("task_state") not in (TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELLED):
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

def handle_approval_response(
    task_id: str,
    action: ApprovalAction,
    feedback: Optional[str] = None,
    selected_vendors: Optional[list] = None
) -> None:
    """
    Handles human-in-the-loop approval actions.
    Idempotent — duplicate events are safely ignored.
    """
    task_info = active_tasks.get(task_id)
    if not task_info:
        logger.warning("Approval received for unknown task %s (ignored)", task_id)
        return

    state = task_info.get("task_state")
    if state != TaskState.WAITING_FINAL_APPROVAL:
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
        if selected_vendors is not None:
            workflow_state.selected_vendors = selected_vendors

    logger.info("Approval response received: %s (vendors: %s)", action, selected_vendors)
    
    # Transition out of waiting state immediately to prevent race conditions
    transition_task_state(task_id, TaskState.RUNNING)
    task_repo.update_task_status(task_id, state, TaskState.RUNNING)
    
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
            if task_info.get("task_state") in (TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELLED):
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
    waiting_state: TaskState,
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

    approval_event = task_info.get("approval_event")
    if not approval_event:
        logger.warning("Approval event missing for task %s (aborting)", task_id)
        return False

    # Clear the approval event and state action before transitioning/waiting
    approval_event.clear()
    state.approval_action = None
        
    old_state = task_info.get("task_state")
    if not transition_task_state(task_id, waiting_state):
        logger.warning("Aborting: failed to transition to %s for step %s", waiting_state, agent_step)
        return False
    task_repo.update_task_status(task_id, old_state, waiting_state)

    app_req_event = ApprovalRequiredEvent(
        correlation_id=correlation_id,
        task_id=task_id,
        task_state=waiting_state,
        agent_step=agent_step,
        draft_message=step_data,
        step_data=step_data,
        message=message,
        approval_timeout_seconds=config.WAIT_FOR_HUMAN_TIMEOUT,
        reflection_metadata=state.reflection_metadata if agent_step == AgentStep.SELF_REFLECTION else None,
        vendors=state.research_data.get("vendors") if state.research_data else None,
        selected_vendor=state.selected_vendor,
        pricing_analysis={
            "summary": state.analysis_summary,
            "selected_vendor": state.selected_vendor,
            "confidence": state.selected_vendor.get("confidence", 0.85) if state.selected_vendor else 0.85,
            "reasoning": state.selected_vendor.get("reasoning", []) if state.selected_vendor else []
        } if state.analysis_summary else None
    )
    await safe_send_json(websocket, app_req_event.model_dump())

    # Wait for approval with timeout
    logger.info("Approval timeout started (%s seconds) for step %s", config.WAIT_FOR_HUMAN_TIMEOUT, agent_step)
    try:
        await asyncio.wait_for(approval_event.wait(), timeout=config.WAIT_FOR_HUMAN_TIMEOUT)
    except asyncio.TimeoutError:
        logger.warning("Approval timeout exceeded for step %s. Task cancelled automatically.", agent_step)
        
        async with task_info["terminal_lock"]:
            if task_info.get("task_state") not in (TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELLED):
                transition_task_state(task_id, TaskState.CANCELLED)
                task_info["cancelled"] = True
                task_repo.update_task_status(task_id, waiting_state, TaskState.CANCELLED)
                
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

    # Handle rejection response
    action = state.approval_action
    if action == ApprovalAction.REJECT:
        logger.info("Step %s received REJECT. Re-running step...", agent_step)
        transition_task_state(task_id, TaskState.RUNNING)
        task_repo.update_task_status(task_id, waiting_state, TaskState.RUNNING)
        return False

    # APPROVED
    if state.rejection_feedback:
        logger.info("Step %s approved with feedback. Proceeding...", agent_step)
    else:
        logger.info("Step %s approved. Proceeding...", agent_step)
        
    transition_task_state(task_id, TaskState.RUNNING)
    task_repo.update_task_status(task_id, waiting_state, TaskState.RUNNING)
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
    Decoupled from sequential code, running as a true state machine guided by LLM Planner
    and ToolRegistry.
    """
    corr_token = set_correlation_id(correlation_id)
    task_token = set_task_id(task_id)
    
    # 1. Resumption check: check if task workflow state is already in database
    from app.core.tool_registry import tool_registry
    from app.services.agent_planner import planner
    
    persisted_state_json = task_repo.get_task_workflow_state(task_id)
    if persisted_state_json:
        logger.info("Resuming orchestration session for task %s from database", task_id)
        try:
            state = WorkflowState.from_json(persisted_state_json)
            # Link to active tasks registry
            task_info = active_tasks.get(task_id)
            if task_info:
                task_info["workflow_state"] = state
                task_info["task_state"] = state.current_step
        except Exception as e:
            logger.warning("Failed to deserialize persisted state: %s. Starting fresh.", e)
            state.current_step = TaskState.SCHEDULED
    else:
        # Fresh schedule
        state.current_step = TaskState.SCHEDULED
        task_repo.update_task_workflow_state(task_id, state.to_json())
        
    task_info = active_tasks.get(task_id)
    if task_info:
        task_info["workflow_state"] = state
        
    logger.info("Orchestration workflow loop started for task %s", task_id)
    
    try:
        # Load task history context optimistically for memory
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
            logger.warning("Failed to load task history for memory: %s", e)

        # Transition task to RUNNING initial state if SCHEDULED
        if state.current_step == TaskState.SCHEDULED:
            if not transition_task_state(task_id, TaskState.RUNNING):
                logger.warning("Aborting orchestration run: failed to transition task state to RUNNING")
                return
            task_repo.update_task_status(task_id, TaskState.SCHEDULED, TaskState.RUNNING)
            state.current_step = TaskState.RUNNING

        # 2. Main State Machine Loop
        while state.current_step not in (TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELLED):
            # Check cancellation
            task_info = active_tasks.get(task_id)
            if not task_info or task_info.get("cancelled"):
                raise asyncio.CancelledError()

            # Ask Planner Decision Engine what to do next
            decision = await planner.decide_next_action(state)
            action = decision.get("next_action")
            reason = decision.get("reason", "")
            params = decision.get("parameters", {})
            
            logger.info("Planner Decision: Action='%s', Reason='%s'", action, reason)

            # Update DB with current state
            task_repo.update_task_workflow_state(task_id, state.to_json())

            # Check if planner decided to complete
            if action == "complete":
                state.current_step = TaskState.COMPLETED
                break
            
            # Check if planner decided to wait for human selection or approval
            elif action == "wait_for_human":
                step_name = params.get("step")
                
                # Check environment overrides (AUTO_APPROVE or HUMAN_IN_LOOP = false)
                is_auto_approve = config.AUTO_APPROVE or not config.HUMAN_IN_LOOP
                if is_auto_approve:
                    logger.info("AUTO_APPROVE is enabled. Automatically approving '%s' step.", step_name)
                    state.approval_action = ApprovalAction.APPROVE
                    state.current_step = TaskState.RUNNING
                    continue

                # Pause execution and prompt user
                waiting_state = TaskState.WAITING_FINAL_APPROVAL
                agent_step = AgentStep.SELF_REFLECTION
                step_data = state.improved_draft or state.draft or ""
                message = "✅ Final proposal ready for review."

                state.current_step = waiting_state
                task_repo.update_task_workflow_state(task_id, state.to_json())

                approved = await _await_step_approval(
                    websocket=websocket,
                    correlation_id=correlation_id,
                    task_id=task_id,
                    state=state,
                    agent_step=agent_step,
                    step_data=step_data,
                    message=message,
                    waiting_state=waiting_state
                )

                # Post-approval verification
                if active_tasks.get(task_id, {}).get("cancelled"):
                    raise asyncio.CancelledError()

                if not approved:
                    # User rejected
                    if state.rejection_feedback:
                        state.feedback_history.append(state.rejection_feedback)
                else:
                    # User approved
                    pass

            else:
                # Tool Execution via ToolRegistry
                tool_name = action
                
                # Execute registered tool and update corresponding step state
                if tool_name == "vendor_search":
                    state.current_step = TaskState.SEARCHING_VENDORS
                    task_repo.update_task_status(task_id, TaskState.RUNNING, TaskState.SEARCHING_VENDORS)
                    
                    event = StatusUpdateEvent(
                        correlation_id=correlation_id,
                        task_id=task_id,
                        task_state=TaskState.SEARCHING_VENDORS,
                        agent_step=AgentStep.SEARCHING_VENDORS,
                        message="🤖 Searching vendors..."
                    )
                    await safe_send_json(websocket, event.model_dump())
                    
                    await tool_registry.execute("vendor_search", state)
                    
                    # Emit VENDORS_FOUND status update event with details
                    event = StatusUpdateEvent(
                        correlation_id=correlation_id,
                        task_id=task_id,
                        task_state=TaskState.SEARCHING_VENDORS,
                        agent_step=AgentStep.SEARCHING_VENDORS,
                        message="🤖 Discovered vendors complete. Found vendor catalogs.",
                        vendors=state.research_data.get("vendors") if state.research_data else None
                    )
                    await safe_send_json(websocket, event.model_dump())
                    
                elif tool_name == "pricing_analysis":
                    state.current_step = TaskState.ANALYZING_PRICING
                    task_repo.update_task_status(task_id, TaskState.RUNNING, TaskState.ANALYZING_PRICING)
                    
                    event = StatusUpdateEvent(
                        correlation_id=correlation_id,
                        task_id=task_id,
                        task_state=TaskState.ANALYZING_PRICING,
                        agent_step=AgentStep.ANALYZING_PRICING,
                        message="🤖 Comparing pricing..."
                    )
                    await safe_send_json(websocket, event.model_dump())
                    
                    await tool_registry.execute("pricing_analysis", state)
                    
                    # Emit PRICING_ANALYZED status update event with details
                    event = StatusUpdateEvent(
                        correlation_id=correlation_id,
                        task_id=task_id,
                        task_state=TaskState.ANALYZING_PRICING,
                        agent_step=AgentStep.ANALYZING_PRICING,
                        message="🤖 Pricing analysis complete.",
                        vendors=state.research_data.get("vendors") if state.research_data else None,
                        selected_vendor=state.selected_vendor,
                        pricing_analysis={
                            "summary": state.analysis_summary,
                            "selected_vendor": state.selected_vendor,
                            "confidence": state.selected_vendor.get("confidence", 0.85) if state.selected_vendor else 0.85,
                            "reasoning": state.selected_vendor.get("reasoning", []) if state.selected_vendor else []
                        } if state.analysis_summary else None
                    )
                    await safe_send_json(websocket, event.model_dump())
                    
                elif tool_name == "draft_outreach":
                    state.current_step = TaskState.DRAFTING_OUTREACH
                    task_repo.update_task_status(task_id, TaskState.RUNNING, TaskState.DRAFTING_OUTREACH)
                    
                    event = StatusUpdateEvent(
                        correlation_id=correlation_id,
                        task_id=task_id,
                        task_state=TaskState.DRAFTING_OUTREACH,
                        agent_step=AgentStep.DRAFTING_OUTREACH,
                        message="🤖 Drafting outreach..."
                    )
                    await safe_send_json(websocket, event.model_dump())
                    
                    await tool_registry.execute("draft_outreach", state)
                    
                    # Emit OUTREACH_DRAFTED status update event with details
                    event = StatusUpdateEvent(
                        correlation_id=correlation_id,
                        task_id=task_id,
                        task_state=TaskState.DRAFTING_OUTREACH,
                        agent_step=AgentStep.DRAFTING_OUTREACH,
                        message="🤖 Outreach draft generated.",
                        vendors=state.research_data.get("vendors") if state.research_data else None,
                        selected_vendor=state.selected_vendor,
                        pricing_analysis={
                            "summary": state.analysis_summary,
                            "selected_vendor": state.selected_vendor,
                            "confidence": state.selected_vendor.get("confidence", 0.85) if state.selected_vendor else 0.85,
                            "reasoning": state.selected_vendor.get("reasoning", []) if state.selected_vendor else []
                        } if state.analysis_summary else None
                    )
                    await safe_send_json(websocket, event.model_dump())
                    
                elif tool_name == "self_reflection":
                    state.current_step = TaskState.SELF_REFLECTION
                    task_repo.update_task_status(task_id, TaskState.RUNNING, TaskState.SELF_REFLECTION)
                    
                    event = StatusUpdateEvent(
                        correlation_id=correlation_id,
                        task_id=task_id,
                        task_state=TaskState.SELF_REFLECTION,
                        agent_step=AgentStep.SELF_REFLECTION,
                        message="🤖 Running self-reflection audit..."
                    )
                    await safe_send_json(websocket, event.model_dump())
                    
                    await tool_registry.execute("self_reflection", state)
                    
                    # Emit REFLECTION_COMPLETED status update event with details
                    event = StatusUpdateEvent(
                        correlation_id=correlation_id,
                        task_id=task_id,
                        task_state=TaskState.SELF_REFLECTION,
                        agent_step=AgentStep.SELF_REFLECTION,
                        message="🤖 Self-reflection audit complete.",
                        vendors=state.research_data.get("vendors") if state.research_data else None,
                        selected_vendor=state.selected_vendor,
                        pricing_analysis={
                            "summary": state.analysis_summary,
                            "selected_vendor": state.selected_vendor,
                            "confidence": state.selected_vendor.get("confidence", 0.85) if state.selected_vendor else 0.85,
                            "reasoning": state.selected_vendor.get("reasoning", []) if state.selected_vendor else []
                        } if state.analysis_summary else None
                    )
                    await safe_send_json(websocket, event.model_dump())
                    
                elif tool_name == "execute_outreach":
                    task_repo.update_task_status(task_id, TaskState.RUNNING, TaskState.RUNNING)
                    
                    event = StatusUpdateEvent(
                        correlation_id=correlation_id,
                        task_id=task_id,
                        task_state=TaskState.RUNNING,
                        agent_step=AgentStep.EXECUTING,
                        message="🤖 Executing final procurement outreach..."
                    )
                    await safe_send_json(websocket, event.model_dump())
                    
                    await tool_registry.execute("execute_outreach", state)
                    state.current_step = TaskState.COMPLETED
                    
                # Small delay to make workflow UI feel natural and premium
                await asyncio.sleep(config.AGENT_STEP_DELAY_SECONDS)

        # 3. Success Finalization
        if state.current_step == TaskState.COMPLETED:
            task_info = active_tasks.get(task_id)
            if task_info:
                async with task_info["terminal_lock"]:
                    if task_info.get("task_state") not in (TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELLED):
                        transition_task_state(task_id, TaskState.COMPLETED)
                        task_repo.update_task_status(task_id, TaskState.RUNNING, TaskState.COMPLETED)
                        task_repo.update_task_final_output(task_id, state.execution_result or "")
                        
                        # Store memory preference context
                        memory_data = {
                            "category": state.research_data.get("category") if state.research_data else None,
                            "vendor_name": (state.selected_vendor.get("vendor_name") or state.selected_vendor.get("name")) if state.selected_vendor else None,
                            "draft": state.improved_draft or state.draft
                        }
                        task_repo.update_task_memory(task_id, memory_data)
                        
                        completed_event = TaskCompletedEvent(
                            correlation_id=correlation_id,
                            task_id=task_id,
                            task_state=TaskState.COMPLETED,
                            message=f"Task successfully completed. Result: {state.execution_result}",
                            final_response=state.improved_draft or state.draft or "",
                            vendors=state.research_data.get("vendors") if state.research_data else None,
                            selected_vendor=state.selected_vendor,
                            pricing_analysis={
                                "summary": state.analysis_summary,
                                "selected_vendor": state.selected_vendor,
                                "confidence": state.selected_vendor.get("confidence", 0.85) if state.selected_vendor else 0.85,
                                "reasoning": state.selected_vendor.get("reasoning", []) if state.selected_vendor else []
                            } if state.analysis_summary else None
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
                if task_info.get("task_state") in (TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELLED):
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
                if task_info.get("task_state") not in (TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELLED):
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
