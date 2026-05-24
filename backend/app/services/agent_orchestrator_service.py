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

async def register_task(task_id: str, websocket: WebSocket, approval_event: asyncio.Event) -> None:
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
            "workflow_state": None
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
    """
    task_info = active_tasks.get(task_id)
    if not task_info:
        await safe_send_json(websocket, event.model_dump())
        try:
            await websocket.close()
        except Exception:
            pass
        return
        
    if task_info.get("terminal_emitted"):
        logger.warning(
            "Prevented duplicate terminal event emission for task %s (event: %s)",
            task_id, event.event_type
        )
        return
        
    task_info["terminal_emitted"] = True
    await safe_send_json(websocket, event.model_dump())
    try:
        await websocket.close()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Step approval helper
# ---------------------------------------------------------------------------

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
    Returns True if APPROVED, False if REJECTED (for retry).
    Raises asyncio.TimeoutError on timeout, cancels on cancellation.
    """
    state.pending_agent_step = agent_step
    state.pending_step_data = step_data

    if not transition_task_state(task_id, TaskState.WAITING_APPROVAL):
        logger.warning("Aborting: failed to transition to WAITING_APPROVAL for step %s", agent_step)
        return False

    task_info = active_tasks.get(task_id)
    if not task_info:
        logger.warning("Task %s removed during step approval (aborting)", task_id)
        return False

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
    )
    await safe_send_json(websocket, app_req_event.model_dump())

    # Wait for approval with timeout
    logger.info("Approval timeout started (%s seconds) for step %s", config.APPROVAL_TIMEOUT_SECONDS, agent_step)
    try:
        await asyncio.wait_for(approval_event.wait(), timeout=config.APPROVAL_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        logger.warning("Approval timeout exceeded for step %s. Task cancelled automatically.", agent_step)
        if transition_task_state(task_id, TaskState.CANCELLED):
            task_info = active_tasks.get(task_id)
            if task_info:
                task_info["cancelled"] = True
            cancelled_event = TaskCancelledEvent(
                correlation_id=correlation_id,
                task_id=task_id,
                task_state=TaskState.CANCELLED,
                message=f"Approval timeout exceeded at step {agent_step.value}. Task cancelled automatically.",
            )
            await send_terminal_event(websocket, task_id, cancelled_event)
        raise asyncio.TimeoutError()

    # Check if task was cancelled while waiting
    task_info = active_tasks.get(task_id)
    if not task_info or task_info.get("cancelled"):
        raise asyncio.CancelledError()

    if state.approval_action == ApprovalAction.REJECT:
        logger.info("Step %s rejected with feedback. Re-running step...", agent_step)
        # Transition back to RUNNING so the step can be re-run
        transition_task_state(task_id, TaskState.RUNNING)
        return False

    # APPROVED — pass feedback to next step if provided, then proceed
    if state.rejection_feedback:
        logger.info("Step %s approved with feedback. Passing to next step...", agent_step)
    else:
        logger.info("Step %s approved. Proceeding...", agent_step)
    transition_task_state(task_id, TaskState.RUNNING)
    return True


async def _run_step_with_approval(
    websocket: WebSocket,
    correlation_id: str,
    task_id: str,
    state: WorkflowState,
    agent_step: AgentStep,
    step_msg: str,
    tool_fn,
    data_extractor,
) -> Optional[bool]:
    """
    Runs a tool step, then pauses for approval.
    If rejected, re-runs the tool. If approved, proceeds.
    Returns True if approved and should continue, raises on cancellation/timeout.
    """
    while True:
        # Check for cancellation
        if active_tasks.get(task_id, {}).get("cancelled"):
            raise asyncio.CancelledError()

        logger.info("Entering step: %s", agent_step)
        status_event = StatusUpdateEvent(
            correlation_id=correlation_id,
            task_id=task_id,
            task_state=TaskState.RUNNING,
            agent_step=agent_step,
            message=step_msg,
        )
        await safe_send_json(websocket, status_event.model_dump())

        try:
            await tool_fn(state)
        except Exception as e:
            logger.exception("Tool execution failed for step %s", agent_step)
            if transition_task_state(task_id, TaskState.FAILED):
                error_event = ErrorEvent(
                    correlation_id=correlation_id,
                    task_id=task_id,
                    task_state=TaskState.FAILED,
                    error_code="TOOL_EXECUTION_FAILED",
                    message=(
                        f"Execution failed at step {agent_step.value}. "
                        f"Please verify {get_provider_name().capitalize()} configuration and try again."
                    ),
                )
                await send_terminal_event(websocket, task_id, error_event)
            raise

        # Extract data for approval display
        step_data = data_extractor(state)

        approved = await _await_step_approval(
            websocket=websocket,
            correlation_id=correlation_id,
            task_id=task_id,
            state=state,
            agent_step=agent_step,
            step_data=step_data,
            message=step_msg,
        )

        if approved:
            state.pending_agent_step = None
            state.pending_step_data = None
            return True

        # REJECTED – clear approval action/event and re-run step
        state.approval_action = None
        task_info = active_tasks.get(task_id)
        if task_info:
            approval_event = task_info.get("approval_event")
            if approval_event:
                approval_event.clear()

        # Transition back to RUNNING for re-run
        if not transition_task_state(task_id, TaskState.RUNNING):
            raise asyncio.CancelledError()


# ---------------------------------------------------------------------------
# Main orchestration runner
# ---------------------------------------------------------------------------

async def run_orchestration(websocket: WebSocket, correlation_id: str, task_id: str, state: WorkflowState) -> None:
    """
    Executes the async agent orchestration workflow using shared WorkflowState.
    Each step pauses for human approval before proceeding.
    """
    # Propagate correlation_id and task_id inside the background task context
    corr_token = set_correlation_id(correlation_id)
    task_token = set_task_id(task_id)
    
    # Store workflow state in registry for runtime visibility
    task_entry = active_tasks.get(task_id)
    if task_entry:
        task_entry["workflow_state"] = state
    
    logger.info("Orchestration workflow started with prompt: %.100s", state.prompt)
    
    try:
        # Transition SCHEDULED -> RUNNING before first tool
        if not transition_task_state(task_id, TaskState.RUNNING):
            logger.warning("Aborting orchestration run: failed to transition task state to RUNNING")
            return

        # =====================================================================
        # Step 1: SEARCHING_VENDORS
        # =====================================================================
        if active_tasks.get(task_id, {}).get("cancelled"):
            raise asyncio.CancelledError()

        logger.info("Entering step: SEARCHING_VENDORS")
        event = StatusUpdateEvent(
            correlation_id=correlation_id,
            task_id=task_id,
            task_state=TaskState.RUNNING,
            agent_step=AgentStep.SEARCHING_VENDORS,
            message="Searching for vendors..."
        )
        await safe_send_json(websocket, event.model_dump())

        await research_tool(state)

        research_data_str = ""
        if state.research_data:
            vendors = state.research_data.get("vendors", [])
            market_insights = state.research_data.get("market_insights", "")
            recommended = state.research_data.get("recommended_approach", "")
            vendor_list = "\n".join(
                [f"• {v['name']} ({v['location']})" for v in vendors]
            ) if vendors else "No vendors found."
            research_data_str = (
                f"Vendors Found:\n{vendor_list}\n\n"
                f"Market Insights: {market_insights}\n\n"
                f"Recommended Approach: {recommended}"
            )

        if not await _await_step_approval(
            websocket=websocket,
            correlation_id=correlation_id,
            task_id=task_id,
            state=state,
            agent_step=AgentStep.SEARCHING_VENDORS,
            step_data=research_data_str,
            message="Vendor search completed. Review findings and approve to continue.",
        ):
            # REJECTED – re-run research step
            continue_research = True
            while continue_research:
                if active_tasks.get(task_id, {}).get("cancelled"):
                    raise asyncio.CancelledError()
                # Clear and re-run
                state.approval_action = None
                task_info = active_tasks.get(task_id)
                if task_info:
                    ae = task_info.get("approval_event")
                    if ae:
                        ae.clear()

                logger.info("Re-running step: SEARCHING_VENDORS")
                event = StatusUpdateEvent(
                    correlation_id=correlation_id,
                    task_id=task_id,
                    task_state=TaskState.RUNNING,
                    agent_step=AgentStep.SEARCHING_VENDORS,
                    message="Re-searching vendors based on feedback..."
                )
                await safe_send_json(websocket, event.model_dump())

                await research_tool(state)

                research_data_str = ""
                if state.research_data:
                    vendors = state.research_data.get("vendors", [])
                    market_insights = state.research_data.get("market_insights", "")
                    recommended = state.research_data.get("recommended_approach", "")
                    vendor_list = "\n".join(
                        [f"• {v['name']} ({v['location']})" for v in vendors]
                    ) if vendors else "No vendors found."
                    research_data_str = (
                        f"Vendors Found:\n{vendor_list}\n\n"
                        f"Market Insights: {market_insights}\n\n"
                        f"Recommended Approach: {recommended}"
                    )

                continue_research = not await _await_step_approval(
                    websocket=websocket,
                    correlation_id=correlation_id,
                    task_id=task_id,
                    state=state,
                    agent_step=AgentStep.SEARCHING_VENDORS,
                    step_data=research_data_str,
                    message="Vendor search completed. Review findings and approve to continue.",
                )

        state.pending_agent_step = None
        state.pending_step_data = None

        # =====================================================================
        # Step 2: ANALYZING_PRICING
        # =====================================================================
        if active_tasks.get(task_id, {}).get("cancelled"):
            raise asyncio.CancelledError()

        logger.info("Entering step: ANALYZING_PRICING")
        event = StatusUpdateEvent(
            correlation_id=correlation_id,
            task_id=task_id,
            task_state=TaskState.RUNNING,
            agent_step=AgentStep.ANALYZING_PRICING,
            message="Analyzing pricing..."
        )
        await safe_send_json(websocket, event.model_dump())

        await analysis_tool(state)

        analysis_str = state.analysis_summary or "Analysis completed."
        if state.selected_vendor:
            analysis_str += f"\n\nSelected Vendor: {state.selected_vendor['name']} ({state.selected_vendor['location']})"

        if not await _await_step_approval(
            websocket=websocket,
            correlation_id=correlation_id,
            task_id=task_id,
            state=state,
            agent_step=AgentStep.ANALYZING_PRICING,
            step_data=analysis_str,
            message="Pricing analysis completed. Review and approve to proceed to drafting.",
        ):
            continue_analysis = True
            while continue_analysis:
                if active_tasks.get(task_id, {}).get("cancelled"):
                    raise asyncio.CancelledError()
                state.approval_action = None
                task_info = active_tasks.get(task_id)
                if task_info:
                    ae = task_info.get("approval_event")
                    if ae:
                        ae.clear()

                logger.info("Re-running step: ANALYZING_PRICING")
                event = StatusUpdateEvent(
                    correlation_id=correlation_id,
                    task_id=task_id,
                    task_state=TaskState.RUNNING,
                    agent_step=AgentStep.ANALYZING_PRICING,
                    message="Re-analyzing pricing based on feedback..."
                )
                await safe_send_json(websocket, event.model_dump())

                await analysis_tool(state)

                analysis_str = state.analysis_summary or "Analysis completed."
                if state.selected_vendor:
                    analysis_str += f"\n\nSelected Vendor: {state.selected_vendor['name']} ({state.selected_vendor['location']})"

                continue_analysis = not await _await_step_approval(
                    websocket=websocket,
                    correlation_id=correlation_id,
                    task_id=task_id,
                    state=state,
                    agent_step=AgentStep.ANALYZING_PRICING,
                    step_data=analysis_str,
                    message="Pricing analysis completed. Review and approve to proceed to drafting.",
                )

        state.pending_agent_step = None
        state.pending_step_data = None

        # =====================================================================
        # Step 3: DRAFTING_OUTREACH
        # =====================================================================
        if active_tasks.get(task_id, {}).get("cancelled"):
            raise asyncio.CancelledError()

        logger.info("Entering step: DRAFTING_OUTREACH")
        event = StatusUpdateEvent(
            correlation_id=correlation_id,
            task_id=task_id,
            task_state=TaskState.RUNNING,
            agent_step=AgentStep.DRAFTING_OUTREACH,
            message="Drafting outreach..."
        )
        await safe_send_json(websocket, event.model_dump())

        try:
            await draft_tool(state)
        except Exception as e:
            logger.exception("Draft generation failed")
            if transition_task_state(task_id, TaskState.FAILED):
                error_event = ErrorEvent(
                    correlation_id=correlation_id,
                    task_id=task_id,
                    task_state=TaskState.FAILED,
                    error_code="LLM_EXECUTION_FAILED",
                    message=(
                        f"AI outreach generation failed. "
                        f"Please verify {get_provider_name().capitalize()} configuration and try again."
                    )
                )
                await send_terminal_event(websocket, task_id, error_event)
            return

        draft_str = state.draft or ""

        if not await _await_step_approval(
            websocket=websocket,
            correlation_id=correlation_id,
            task_id=task_id,
            state=state,
            agent_step=AgentStep.DRAFTING_OUTREACH,
            step_data=draft_str,
            message="Outreach draft generated. Review and approve to continue to self-reflection.",
        ):
            continue_draft = True
            while continue_draft:
                if active_tasks.get(task_id, {}).get("cancelled"):
                    raise asyncio.CancelledError()
                state.approval_action = None
                task_info = active_tasks.get(task_id)
                if task_info:
                    ae = task_info.get("approval_event")
                    if ae:
                        ae.clear()

                logger.info("Re-running step: DRAFTING_OUTREACH")
                event = StatusUpdateEvent(
                    correlation_id=correlation_id,
                    task_id=task_id,
                    task_state=TaskState.RUNNING,
                    agent_step=AgentStep.DRAFTING_OUTREACH,
                    message="Re-drafting outreach based on feedback..."
                )
                await safe_send_json(websocket, event.model_dump())

                try:
                    await draft_tool(state)
                except Exception as e:
                    logger.exception("Draft re-generation failed")
                    if transition_task_state(task_id, TaskState.FAILED):
                        error_event = ErrorEvent(
                            correlation_id=correlation_id,
                            task_id=task_id,
                            task_state=TaskState.FAILED,
                            error_code="LLM_EXECUTION_FAILED",
                            message="AI outreach re-generation failed. Please verify configuration and try again."
                        )
                        await send_terminal_event(websocket, task_id, error_event)
                    return

                draft_str = state.draft or ""
                continue_draft = not await _await_step_approval(
                    websocket=websocket,
                    correlation_id=correlation_id,
                    task_id=task_id,
                    state=state,
                    agent_step=AgentStep.DRAFTING_OUTREACH,
                    step_data=draft_str,
                    message="Outreach draft generated. Review and approve to continue to self-reflection.",
                )

        state.pending_agent_step = None
        state.pending_step_data = None

        # =====================================================================
        # Step 4: SELF_REFLECTION
        # =====================================================================
        if active_tasks.get(task_id, {}).get("cancelled"):
            raise asyncio.CancelledError()

        logger.info("Entering step: SELF_REFLECTION")
        event = StatusUpdateEvent(
            correlation_id=correlation_id,
            task_id=task_id,
            task_state=TaskState.RUNNING,
            agent_step=AgentStep.SELF_REFLECTION,
            message="Performing self-reflection..."
        )
        await safe_send_json(websocket, event.model_dump())

        try:
            await reflection_tool(state)
        except Exception as e:
            logger.exception("Self-reflection failed")
            if transition_task_state(task_id, TaskState.FAILED):
                error_event = ErrorEvent(
                    correlation_id=correlation_id,
                    task_id=task_id,
                    task_state=TaskState.FAILED,
                    error_code="LLM_EXECUTION_FAILED",
                    message=(
                        f"AI draft self-reflection failed. "
                        f"Please verify {get_provider_name().capitalize()} configuration and try again."
                    )
                )
                await send_terminal_event(websocket, task_id, error_event)
            return

        improved_str = state.improved_draft or state.draft or ""

        if not await _await_step_approval(
            websocket=websocket,
            correlation_id=correlation_id,
            task_id=task_id,
            state=state,
            agent_step=AgentStep.SELF_REFLECTION,
            step_data=improved_str,
            message="Self-reflection completed. Review the refined draft and approve to execute.",
        ):
            continue_reflection = True
            while continue_reflection:
                if active_tasks.get(task_id, {}).get("cancelled"):
                    raise asyncio.CancelledError()
                state.approval_action = None
                task_info = active_tasks.get(task_id)
                if task_info:
                    ae = task_info.get("approval_event")
                    if ae:
                        ae.clear()

                logger.info("Re-running step: SELF_REFLECTION")
                event = StatusUpdateEvent(
                    correlation_id=correlation_id,
                    task_id=task_id,
                    task_state=TaskState.RUNNING,
                    agent_step=AgentStep.SELF_REFLECTION,
                    message="Re-running self-reflection..."
                )
                await safe_send_json(websocket, event.model_dump())

                try:
                    await reflection_tool(state)
                except Exception as e:
                    logger.exception("Self-reflection re-run failed")
                    if transition_task_state(task_id, TaskState.FAILED):
                        error_event = ErrorEvent(
                            correlation_id=correlation_id,
                            task_id=task_id,
                            task_state=TaskState.FAILED,
                            error_code="LLM_EXECUTION_FAILED",
                            message="AI self-reflection re-run failed. Please verify configuration and try again."
                        )
                        await send_terminal_event(websocket, task_id, error_event)
                    return

                improved_str = state.improved_draft or state.draft or ""
                continue_reflection = not await _await_step_approval(
                    websocket=websocket,
                    correlation_id=correlation_id,
                    task_id=task_id,
                    state=state,
                    agent_step=AgentStep.SELF_REFLECTION,
                    step_data=improved_str,
                    message="Self-reflection completed. Review the refined draft and approve to execute.",
                )

        state.pending_agent_step = None
        state.pending_step_data = None

        # =====================================================================
        # Step 5: EXECUTING (final step — no approval needed after this)
        # =====================================================================
        logger.info("Orchestration resumed after final approval")
        if not transition_task_state(task_id, TaskState.EXECUTING):
            logger.warning("Aborting: failed to transition to EXECUTING")
            return

        event = StatusUpdateEvent(
            correlation_id=correlation_id,
            task_id=task_id,
            task_state=TaskState.EXECUTING,
            agent_step=AgentStep.EXECUTING,
            message="Executing approved workflow..."
        )
        await safe_send_json(websocket, event.model_dump())

        try:
            await execution_tool(state)
        except Exception as e:
            logger.exception("Execution approved, but workflow execution failed")
            if transition_task_state(task_id, TaskState.FAILED):
                error_event = ErrorEvent(
                    correlation_id=correlation_id,
                    task_id=task_id,
                    task_state=TaskState.FAILED,
                    error_code="EXECUTION_FAILED",
                    message=(
                        "Execution approved, but workflow execution failed. "
                        "Please verify system settings and try again."
                    )
                )
                await send_terminal_event(websocket, task_id, error_event)
            return

        # SUCCESS
        if transition_task_state(task_id, TaskState.SUCCESS):
            completed_event = TaskCompletedEvent(
                correlation_id=correlation_id,
                task_id=task_id,
                task_state=TaskState.SUCCESS,
                message=f"Task successfully executed. Outreach finalized. Result: {state.execution_result}",
                final_response=state.improved_draft or state.draft
            )
            await send_terminal_event(websocket, task_id, completed_event)
            logger.info("Orchestration completed successfully")

    except asyncio.TimeoutError:
        # Timeout already handled in _await_step_approval, just exit
        logger.info("Orchestration ended due to approval timeout")
    except asyncio.CancelledError:
        logger.info("Orchestration cancelled exception caught")
        task_info = active_tasks.get(task_id)

        if websocket.client_state != WebSocketState.CONNECTED:
            return

        if task_info and task_info.get("task_state") in (TaskState.SUCCESS, TaskState.FAILED):
            return

        if task_info and task_info.get("task_state") != TaskState.CANCELLED:
            transition_task_state(task_id, TaskState.CANCELLED)

        cancelled_event = TaskCancelledEvent(
            correlation_id=correlation_id,
            task_id=task_id,
            task_state=TaskState.CANCELLED,
            message="Orchestration cancelled by client."
        )
        await send_terminal_event(websocket, task_id, cancelled_event)
    except Exception as e:
        logger.exception("Orchestration unexpected failure")
        if transition_task_state(task_id, TaskState.FAILED):
            error_event = ErrorEvent(
                correlation_id=correlation_id,
                task_id=task_id,
                task_state=TaskState.FAILED,
                error_code="ORCHESTRATION_FAILURE",
                message="An unexpected system error occurred. Please verify configuration and try again."
            )
            await send_terminal_event(websocket, task_id, error_event)
    finally:
        correlation_id_ctx.reset(corr_token)
        task_id_ctx.reset(task_token)
        await cleanup_task(task_id)