from typing import Optional

from app.core.enums import AgentStep, TaskState
from app.models.workflow_state import WorkflowState
from app.schemas.websocket_schema import (
    ApprovalRequiredEvent,
    StatusUpdateEvent,
    TaskCancelledEvent,
    TaskCompletedEvent,
)


def build_pricing_payload(state: WorkflowState) -> Optional[dict]:
    if not state.analysis_summary:
        return None

    return {
        "summary": state.analysis_summary,
        "selected_vendor": state.selected_vendor,
        "selected_vendors": state.selected_vendors,
        "confidence": state.selected_vendor.get("confidence", 0.85) if state.selected_vendor else 0.85,
        "reasoning": state.selected_vendor.get("reasoning", []) if state.selected_vendor else [],
    }


def build_vendor_summary(state: WorkflowState) -> Optional[str]:
    vendors = state.research_data.get("vendors", []) if state.research_data else []
    if not vendors:
        return None

    lines = ["Vendor search completed. Select candidates and approve.", ""]
    for index, vendor in enumerate(vendors[:5], start=1):
        name = vendor.get("vendor_name") or vendor.get("name") or f"Vendor {index}"
        location = vendor.get("location", "Location unavailable")
        rating = vendor.get("rating", "n/a")
        delivery_days = vendor.get("delivery_days")
        delivery = f"{delivery_days} days" if delivery_days is not None else "delivery n/a"
        lines.append(f"{index}. {name} - {location} - rating {rating} - {delivery}")

    return "\n".join(lines)


def build_status_event(
    *,
    correlation_id: str,
    task_id: str,
    workflow_version: int,
    task_state: TaskState,
    agent_step: AgentStep,
    message: str,
    state: WorkflowState,
) -> StatusUpdateEvent:
    return StatusUpdateEvent(
        correlation_id=correlation_id,
        task_id=task_id,
        workflow_version=workflow_version,
        task_state=task_state,
        agent_step=agent_step,
        message=message,
        vendors=state.research_data.get("vendors") if state.research_data else None,
        selected_vendor=state.selected_vendor,
        selected_vendors=state.selected_vendors,
        pricing_analysis=build_pricing_payload(state),
    )


def build_approval_required_event(
    *,
    correlation_id: str,
    task_id: str,
    workflow_version: int,
    task_state: TaskState,
    agent_step: AgentStep,
    message: str,
    state: WorkflowState,
    approval_timeout_seconds: int,
) -> ApprovalRequiredEvent:
    vendor_summary = build_vendor_summary(state) if task_state == TaskState.WAITING_VENDOR_SELECTION else None
    draft = state.improved_draft or state.draft or vendor_summary or message
    return ApprovalRequiredEvent(
        correlation_id=correlation_id,
        task_id=task_id,
        workflow_version=workflow_version,
        task_state=task_state,
        agent_step=agent_step,
        draft_message=draft,
        step_data=vendor_summary or draft,
        message=message,
        approval_timeout_seconds=approval_timeout_seconds,
        reflection_metadata=state.reflection_metadata,
        vendors=state.research_data.get("vendors") if state.research_data else None,
        selected_vendor=state.selected_vendor,
        selected_vendors=state.selected_vendors,
        pricing_analysis=build_pricing_payload(state),
    )


def build_completed_event(
    *,
    correlation_id: str,
    task_id: str,
    workflow_version: int,
    state: WorkflowState,
) -> TaskCompletedEvent:
    return TaskCompletedEvent(
        correlation_id=correlation_id,
        task_id=task_id,
        workflow_version=workflow_version,
        task_state=TaskState.COMPLETED,
        message="Procurement outreach simulation succeeded.",
        final_response=state.improved_draft or state.draft or "",
        vendors=state.research_data.get("vendors") if state.research_data else None,
        selected_vendor=state.selected_vendor,
        selected_vendors=state.selected_vendors,
        pricing_analysis=build_pricing_payload(state),
    )


def build_cancelled_event(
    *,
    correlation_id: str,
    task_id: str,
    workflow_version: int,
    message: str,
) -> TaskCancelledEvent:
    return TaskCancelledEvent(
        correlation_id=correlation_id,
        task_id=task_id,
        workflow_version=workflow_version,
        task_state=TaskState.CANCELLED,
        message=message,
    )
