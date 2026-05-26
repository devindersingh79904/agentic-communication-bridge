from app.core.enums import AgentStep, TaskState
from app.models.workflow_state import WorkflowState
from app.runtime.event_streamer import build_approval_required_event


def test_vendor_approval_event_contains_vendor_payload_and_summary():
    state = WorkflowState(prompt="Find server hardware vendors")
    state.research_data = {
        "vendors": [
            {
                "vendor_name": "Cloud Server Hub",
                "location": "Electronic City",
                "rating": 4.5,
                "delivery_days": 4,
            },
            {
                "vendor_name": "ByteEdge Systems",
                "location": "Whitefield",
                "rating": 4.3,
                "delivery_days": 3,
            },
        ]
    }

    event = build_approval_required_event(
        correlation_id="corr-1",
        task_id="task-1",
        workflow_version=3,
        task_state=TaskState.WAITING_VENDOR_SELECTION,
        agent_step=AgentStep.SEARCHING_VENDORS,
        message="Vendor search completed. Select candidates and approve.",
        state=state,
        approval_timeout_seconds=60,
    )

    assert event.vendors == state.research_data["vendors"]
    assert event.workflow_version == 3
    assert "Cloud Server Hub" in event.step_data
    assert "ByteEdge Systems" in event.step_data
    assert event.draft_message == event.step_data


def test_final_approval_event_contains_selected_vendors_and_comparison():
    state = WorkflowState(prompt="Find server hardware vendors")
    state.selected_vendor = {"vendor_name": "ByteEdge Systems", "rating": 4.3}
    state.selected_vendors = [
        {"vendor_name": "Cloud Server Hub", "rating": 4.5},
        {"vendor_name": "ByteEdge Systems", "rating": 4.3},
    ]
    state.analysis_summary = "Compared selected vendors and recommended ByteEdge Systems."
    state.improved_draft = "Dear ByteEdge Systems Team,\n\nPlease share server hardware options."

    event = build_approval_required_event(
        correlation_id="corr-1",
        task_id="task-1",
        workflow_version=4,
        task_state=TaskState.WAITING_FINAL_APPROVAL,
        agent_step=AgentStep.SELF_REFLECTION,
        message="Self-reflection completed. Approve outreach proposal draft.",
        state=state,
        approval_timeout_seconds=60,
    )

    assert event.selected_vendor == state.selected_vendor
    assert event.selected_vendors == state.selected_vendors
    assert event.pricing_analysis["selected_vendors"] == state.selected_vendors
    assert "recommended ByteEdge Systems" in event.pricing_analysis["summary"]
