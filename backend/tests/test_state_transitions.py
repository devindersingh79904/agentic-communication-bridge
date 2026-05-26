import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from app.core.enums import TaskState, ApprovalAction, AgentStep
from app.models.workflow_models import WorkflowSession, RuntimeWorkflowState, PlanStep, ExecutionPlan
from app.models.workflow_state import WorkflowState
from app.runtime.workflow_runtime import workflow_runtime, _active_events
from app.storage.workflow_repository import workflow_repo
from app.websocket.connection_manager import connection_manager

@pytest.mark.asyncio
async def test_check_approval_gate_states():
    task_id = "test-gate-task"
    prompt = "Test prompt"
    session = await workflow_runtime.get_or_create_session(task_id, prompt)
    
    # Enable HIL, disable auto approve
    with patch("app.core.config.HUMAN_IN_LOOP", True), \
         patch("app.core.config.AUTO_APPROVE", False):
        
        # 1. Test step: pricing_analysis
        pricing_step = PlanStep(step_id="1", tool="pricing_analysis", reason="Test", status="pending")
        
        # Initially, vendor_selection_approved is False, selected_vendor is None
        requires_gate, agent_step, waiting_state, gate_msg = workflow_runtime._check_approval_gate(pricing_step, session)
        assert requires_gate is True
        assert waiting_state == TaskState.WAITING_VENDOR_SELECTION
        assert agent_step == AgentStep.SEARCHING_VENDORS
        
        # Set vendor_selection_approved to True
        legacy_state = WorkflowState.from_json(session.workflow_state_json)
        legacy_state.vendor_selection_approved = True
        session.workflow_state_json = legacy_state.to_json()
        workflow_repo.save_session(session)
        
        # Now it shouldn't require gate
        requires_gate, _, _, _ = workflow_runtime._check_approval_gate(pricing_step, session)
        assert requires_gate is False

        # 2. Test step: execute_outreach
        outreach_step = PlanStep(step_id="2", tool="execute_outreach", reason="Test", status="pending")
        
        # Initially final_approval_approved is False, and status is not APPROVED
        requires_gate, agent_step, waiting_state, gate_msg = workflow_runtime._check_approval_gate(outreach_step, session)
        assert requires_gate is True
        assert waiting_state == TaskState.WAITING_FINAL_APPROVAL
        assert agent_step == AgentStep.SELF_REFLECTION

        # Set final_approval_approved to True
        legacy_state.final_approval_approved = True
        session.workflow_state_json = legacy_state.to_json()
        workflow_repo.save_session(session)
        
        # Now it shouldn't require gate
        requires_gate, _, _, _ = workflow_runtime._check_approval_gate(outreach_step, session)
        assert requires_gate is False

@pytest.mark.asyncio
async def test_handle_approval_response_validation():
    task_id = "test-approval-task"
    prompt = "Test prompt"
    session = await workflow_runtime.get_or_create_session(task_id, prompt)
    
    # Mock connection manager send_json to prevent websocket errors
    with patch.object(connection_manager, "send_json", new_callable=AsyncMock) as mock_send:
        # Case 1: Session not in WAITING_APPROVAL status. Should ignore.
        session.status = RuntimeWorkflowState.EXECUTING
        workflow_repo.save_session(session)
        
        await workflow_runtime.handle_approval_response(
            task_id=task_id,
            action=ApprovalAction.APPROVE,
            incoming_version=session.workflow_version
        )
        # Session status should remain EXECUTING
        updated = workflow_repo.get_session(task_id)
        assert updated.status == RuntimeWorkflowState.EXECUTING
        
        # Case 2: Session in WAITING_APPROVAL, but version mismatch. Should emit error.
        session.status = RuntimeWorkflowState.WAITING_APPROVAL
        workflow_repo.save_session(session)
        
        await workflow_runtime.handle_approval_response(
            task_id=task_id,
            action=ApprovalAction.APPROVE,
            incoming_version=session.workflow_version + 10 # wrong version
        )
        updated = workflow_repo.get_session(task_id)
        assert updated.status == RuntimeWorkflowState.WAITING_APPROVAL
        mock_send.assert_called_once()
        assert mock_send.call_args[0][1]["error_code"] == "STALE_WORKFLOW_VERSION"
        
        # Case 3: Valid approval
        mock_send.reset_mock()
        # Set waiting state on state json
        legacy_state = WorkflowState.from_json(session.workflow_state_json)
        legacy_state.current_step = TaskState.WAITING_VENDOR_SELECTION
        session.workflow_state_json = legacy_state.to_json()
        session.execution_plan = ExecutionPlan(plan=[
            PlanStep(step_id="1", tool="pricing_analysis", reason="Test", status="pending")
        ])
        workflow_repo.save_session(session)
        
        # Setup approval event
        event = asyncio.Event()
        _active_events[task_id] = event
        
        await workflow_runtime.handle_approval_response(
            task_id=task_id,
            action=ApprovalAction.APPROVE,
            selected_vendors=[{"vendor_name": "ApprovedVendor"}],
            incoming_version=session.workflow_version
        )
        
        updated = workflow_repo.get_session(task_id)
        assert updated.status == RuntimeWorkflowState.APPROVED
        assert updated.workflow_version == session.workflow_version + 1
        updated_state = WorkflowState.from_json(updated.workflow_state_json)
        assert updated_state.vendor_selection_approved is True
        assert updated_state.selected_vendor == {"vendor_name": "ApprovedVendor"}
        assert event.is_set()
