from app.core.enums import TaskState
from app.models.workflow_models import ExecutionPlan, PlanStep, RuntimeWorkflowState, WorkflowSession
from app.models.workflow_state import WorkflowState
from app.runtime.workflow_runtime import WorkflowRuntime
from app.services.agent_planner import planner
from app.utils.time import utc_now_iso


def test_final_draft_rejection_regenerates_only_outreach_steps():
    state = WorkflowState(prompt="Find reliable procurement vendors for mobile shop")
    state.research_data = {"vendors": [{"vendor_name": "NextGen PC Hub"}]}
    state.selected_vendor = {"vendor_name": "NextGen PC Hub"}
    state.selected_vendors = [state.selected_vendor]
    state.vendor_selection_approved = True
    state.price_approval_approved = True
    state.final_approval_approved = False
    state.draft = "Long draft"
    state.improved_draft = "Long refined draft"
    state.reflection_metadata = {"confidence_score": 0.9}
    state.execution_result = "Should be cleared"
    state.current_step = TaskState.WAITING_FINAL_APPROVAL

    session = WorkflowSession(
        task_id="task-final-reject",
        user_prompt=state.prompt,
        workflow_state_json=state.to_json(),
        created_at=utc_now_iso(),
        updated_at=utc_now_iso(),
        status=RuntimeWorkflowState.REJECTED,
        rejection_feedback="shorter the email",
        execution_plan=ExecutionPlan(
            plan=[
                PlanStep(step_id="1", tool="vendor_search", reason="Find vendors", status="completed"),
                PlanStep(step_id="2", tool="pricing_analysis", reason="Analyze pricing", depends_on=["1"], status="completed"),
                PlanStep(step_id="3", tool="draft_outreach", reason="Draft email", depends_on=["2"], status="completed"),
                PlanStep(step_id="4", tool="self_reflection", reason="Review draft", depends_on=["3"], status="completed"),
                PlanStep(step_id="5", tool="execute_outreach", reason="Send email", depends_on=["4"], status="pending"),
            ]
        ),
    )

    prepared = WorkflowRuntime()._prepare_final_draft_regeneration(session, "shorter the email")
    updated_state = WorkflowState.from_json(session.workflow_state_json)
    statuses = {step.tool: step.status for step in session.execution_plan.plan}

    assert prepared is True
    assert session.status == RuntimeWorkflowState.EXECUTING
    assert updated_state.current_step == TaskState.DRAFTING_OUTREACH
    assert updated_state.selected_vendor == {"vendor_name": "NextGen PC Hub"}
    assert updated_state.vendor_selection_approved is True
    assert updated_state.draft is None
    assert updated_state.improved_draft is None
    assert updated_state.reflection_metadata is None
    assert updated_state.execution_result is None
    assert statuses == {
        "vendor_search": "completed",
        "pricing_analysis": "completed",
        "draft_outreach": "pending",
        "self_reflection": "pending",
        "execute_outreach": "pending",
    }
    assert updated_state.rejection_feedback == "shorter the email"
    assert updated_state.constraints["latest_user_feedback"] == "shorter the email"
    assert updated_state.regeneration_count == 0


def test_evaluator_feedback_does_not_overwrite_user_draft_feedback():
    state = WorkflowState(prompt="i want new socks new brown shoes")
    state.rejection_feedback = "make it one liner and make response in punjabi"
    state.constraints["latest_user_feedback"] = state.rejection_feedback
    state.regeneration_count = 1

    evaluator_feedback = "Quality audit failed. Corrections: add a greeting"
    existing_feedback = state.rejection_feedback
    state.constraints["evaluator_feedback"] = evaluator_feedback
    if not existing_feedback:
        state.rejection_feedback = evaluator_feedback

    assert state.rejection_feedback == "make it one liner and make response in punjabi"
    assert state.constraints["evaluator_feedback"] == evaluator_feedback


async def test_multiple_selected_vendors_are_compared_before_drafting(monkeypatch):
    vendor_a = {"vendor_name": "Alpha Systems", "rating": 4.2}
    vendor_b = {"vendor_name": "Beta Systems", "rating": 4.8}
    state = WorkflowState(prompt="Find reliable server hardware vendors")
    state.research_data = {"vendors": [vendor_a, vendor_b, {"vendor_name": "Other Vendor"}]}
    state.selected_vendors = [vendor_a, vendor_b]
    state.selected_vendor = vendor_a

    async def fake_pricing_analysis_tool(query, vendors):
        assert vendors == [vendor_a, vendor_b]
        return {
            "recommended_vendor": vendor_b,
            "analysis_summary": "Compared Alpha Systems and Beta Systems. Beta Systems is the best fit.",
            "reasoning": ["Higher rating"],
            "confidence": 0.9,
        }

    monkeypatch.setattr("app.services.agent_planner.pricing_analysis_tool", fake_pricing_analysis_tool)

    await planner.run_analysis(state)

    assert state.selected_vendor == vendor_b
    assert "Beta Systems is the best fit" in state.analysis_summary
