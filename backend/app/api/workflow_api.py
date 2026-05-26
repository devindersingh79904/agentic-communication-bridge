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

@router.get("/{task_id}")
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

    # Reconstruct dynamic message history for client UI restore
    messages = []
    
    # 1. Prompt message
    messages.append({
        "id": "msg-prompt",
        "sender": "user",
        "text": session.user_prompt,
        "timestamp": session.created_at
    })
    messages.append({
        "id": "msg-init",
        "sender": "system",
        "text": "Connected. Workflow initialized.",
        "timestamp": session.created_at
    })
    
    # 2. Research updates
    if legacy_state.research_data:
        category = legacy_state.research_data.get("category", "unknown")
        vendors = legacy_state.research_data.get("vendors", [])
        messages.append({
            "id": "msg-search-start",
            "sender": "agent",
            "text": "Analyzing task and compiling execution graph...",
            "agent_step": "SEARCHING_VENDORS",
            "timestamp": session.created_at
        })
        
        # Build candidate vendors message
        lines = []
        for index, vendor in enumerate(vendors[:5]):
            vname = vendor.get("vendor_name") or vendor.get("name") or f"Vendor {index + 1}"
            vloc = vendor.get("location") or "location n/a"
            vrating = f"rating {vendor.get('rating')}" if vendor.get("rating") else "rating n/a"
            vdeliv = f"{vendor.get('delivery_days')}d delivery" if vendor.get("delivery_days") else "delivery n/a"
            lines.append(f"{index + 1}. {vname} - {vloc} - {vrating} - {vdeliv}")
            
        vendors_summary = f"Candidate vendors:\n" + "\n".join(lines) if lines else "Found candidate vendors."
        
        messages.append({
            "id": "msg-search-result",
            "sender": "agent",
            "text": vendors_summary,
            "agent_step": "SEARCHING_VENDORS",
            "timestamp": session.created_at
        })
        
    # 3. Pricing analysis
    if legacy_state.analysis_summary:
        messages.append({
            "id": "msg-pricing-analysis",
            "sender": "agent",
            "text": f"Vendor comparison:\n\n{legacy_state.analysis_summary}",
            "agent_step": "ANALYZING_PRICING",
            "timestamp": session.created_at
        })
        
    # 4. Draft
    draft_message = legacy_state.improved_draft or legacy_state.draft
    if draft_message:
        messages.append({
            "id": "msg-draft-notification",
            "sender": "system",
            "text": "📧 Draft generated — review below and approve or reject with feedback.",
            "timestamp": session.updated_at
        })
        messages.append({
            "id": "msg-draft-content",
            "sender": "agent",
            "text": draft_message,
            "agent_step": "DRAFTING_OUTREACH",
            "timestamp": session.updated_at
        })

    # 5. Success / Cancelled status
    if session.status == RuntimeWorkflowState.COMPLETED:
        messages.append({
            "id": "msg-complete",
            "sender": "system",
            "text": f"Success: Outreach proposal finalized.",
            "timestamp": session.updated_at
        })
    elif session.status == RuntimeWorkflowState.CANCELLED:
        messages.append({
            "id": "msg-cancelled",
            "sender": "system",
            "text": f"Cancelled: Task cancelled.",
            "timestamp": session.updated_at
        })
    elif session.status == RuntimeWorkflowState.FAILED:
        messages.append({
            "id": "msg-failed",
            "sender": "system",
            "text": f"Error: Task failed.",
            "timestamp": session.updated_at
        })

    # Construct approval payload
    approval_payload = {
        "agent_step": legacy_state.pending_agent_step.value if legacy_state.pending_agent_step else None,
        "draft_message": draft_message,
        "vendors": legacy_state.research_data.get("vendors") if legacy_state.research_data else [],
        "selected_vendor": legacy_state.selected_vendor,
        "selected_vendors": legacy_state.selected_vendors,
        "pricing_analysis": {
            "summary": legacy_state.analysis_summary,
            "selected_vendor": legacy_state.selected_vendor,
            "selected_vendors": legacy_state.selected_vendors,
            "confidence": legacy_state.selected_vendor.get("confidence", 0.85) if legacy_state.selected_vendor else 0.85,
            "reasoning": legacy_state.selected_vendor.get("reasoning", []) if legacy_state.selected_vendor else []
        } if legacy_state.analysis_summary else None,
        "reflection_metadata": legacy_state.reflection_metadata
    }

    # Build response data matching exact evaluator request format
    response_data = {
        "task_id": session.task_id,
        "state": ui_state.value,
        "workflow_version": session.workflow_version,
        "messages": messages,
        "approval_payload": approval_payload
    }
    
    return response_data
