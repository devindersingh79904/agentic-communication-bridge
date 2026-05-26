from fastapi import APIRouter, HTTPException
import logging

from app.schemas.base_response import BaseSuccessResponse
from app.utils.response_builder import success_response
from app.storage.workflow_repository import workflow_repo
from app.models.workflow_state import WorkflowState
from app.models.workflow_models import RuntimeWorkflowState
from app.core.enums import TaskState
from app.core.logger import get_logger

router = APIRouter(prefix="/v1/workflow", tags=["Workflow"])
logger = get_logger("api.workflow")

@router.get("/{task_id}", response_model=BaseSuccessResponse)
async def get_workflow_session(task_id: str):
    """
    Retrieves the complete state of a workflow task, including reasoning traces,
    completed steps, active status, and approval payloads for mobile recovery.
    """
    logger.info(f"Retrieving session state for task {task_id}")
    session = workflow_repo.get_session(task_id)
    if not session:
        raise HTTPException(status_code=404, detail="Workflow session not found")
        
    legacy_state = WorkflowState.from_json(session.workflow_state_json)
    
    # Map completed steps
    completed_steps = [s.tool for s in session.execution_plan.plan if s.status == "completed"]
    # Find active step
    active_step_obj = next((s for s in session.execution_plan.plan if s.status == "running"), None)
    active_step = active_step_obj.tool if active_step_obj else None
    
    # Map pending approval
    pending_approval = session.status.value == "WAITING_APPROVAL"
    
    # Map reasoning traces
    reasoning_traces = []
    for step in session.execution_plan.plan:
        reasoning_traces.append({
            "decision": f"Execute tool {step.tool}",
            "reason": step.reason,
            "status": step.status
        })

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

    response_data = {
        "task_id": session.task_id,
        "runtime_status": session.status.value,
        "current_state": ui_state.value,
        "completed_steps": completed_steps,
        "active_step": active_step,
        "pending_approval": pending_approval,
        "reasoning_traces": reasoning_traces,
        "execution_history": session.event_history,
        "workflow_version": session.workflow_version,
        "vendors": legacy_state.research_data.get("vendors") if legacy_state.research_data else None,
        "selected_vendor": legacy_state.selected_vendor,
        "selected_vendors": legacy_state.selected_vendors,
        "pricing_analysis": {
            "summary": legacy_state.analysis_summary,
            "selected_vendor": legacy_state.selected_vendor,
            "selected_vendors": legacy_state.selected_vendors,
            "confidence": legacy_state.selected_vendor.get("confidence", 0.85) if legacy_state.selected_vendor else 0.85,
            "reasoning": legacy_state.selected_vendor.get("reasoning", []) if legacy_state.selected_vendor else []
        } if legacy_state.analysis_summary else None,
        "draft_message": legacy_state.improved_draft or legacy_state.draft,
        "reflection_metadata": legacy_state.reflection_metadata
    }
    
    return success_response(
        message="Workflow session retrieved successfully",
        data=response_data
    )
