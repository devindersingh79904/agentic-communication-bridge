import asyncio
import pytest
from starlette.websockets import WebSocketDisconnect
from fastapi.testclient import TestClient

from app.core import config
from app.core.enums import TaskState, ApprovalAction

from app.storage.workflow_repository import workflow_repo
from app.models.workflow_models import RuntimeWorkflowState, WorkflowSession
from app.models.workflow_state import WorkflowState

from unittest.mock import AsyncMock, patch

@pytest.fixture(autouse=True)
def speed_up_and_mock_tools():
    """Speeds up delays and mocks orchestrator tool dependencies and planner for websocket tests."""
    from app.core.tool_registry import tool_registry
    
    original_tools = dict(tool_registry._registry)
    
    m_res = AsyncMock()
    m_ana = AsyncMock()
    m_dra = AsyncMock()
    m_ref = AsyncMock()
    m_exe = AsyncMock()

    # Populate state on tools so step_data is non-empty
    async def side_effect_research(state, *args, **kwargs):
        state.research_data = {
            "vendors": [{"name": "TestVendor", "location": "TestLocation"}],
            "market_insights": "Test insights",
            "recommended_approach": "Test approach",
        }
    m_res.side_effect = side_effect_research

    async def side_effect_analysis(state, *args, **kwargs):
        state.analysis_summary = "Analysis complete"
        state.selected_vendor = {"name": "TestVendor", "location": "TestLocation"}
    m_ana.side_effect = side_effect_analysis

    async def side_effect_draft(state, *args, **kwargs):
        state.draft = "Draft Message"
    m_dra.side_effect = side_effect_draft

    async def side_effect_reflect(state, *args, **kwargs):
        state.improved_draft = "Refined Draft"
    m_ref.side_effect = side_effect_reflect

    async def side_effect_execute(state, *args, **kwargs):
        state.execution_result = "Execution Succeeded"
    m_exe.side_effect = side_effect_execute

    tool_registry.register("vendor_search", m_res)
    tool_registry.register("pricing_analysis", m_ana)
    tool_registry.register("draft_outreach", m_dra)
    tool_registry.register("self_reflection", m_ref)
    tool_registry.register("execute_outreach", m_exe)

    # Mock planner decide_next_action to return sequential orchestration decisions
    m_decide = AsyncMock()
    async def side_effect_decide(state):
        from app.core.enums import TaskState, ApprovalAction
        
        # 1. Vendor search phase
        if not state.research_data:
            return {
                "next_action": "vendor_search",
                "reason": "Test search",
                "parameters": {}
            }
            
        # 2. Wait for vendor selection
        if state.research_data and not state.selected_vendors:
            if state.approval_action in (ApprovalAction.REJECT, ApprovalAction.MODIFY_REQUEST):
                state.approval_action = None
                state.research_data = None # trigger re-search
                return {
                    "next_action": "vendor_search",
                    "reason": "Retry search due to rejection",
                    "parameters": {}
                }
            return {
                "next_action": "wait_for_human",
                "reason": "Test wait vendor",
                "parameters": {"step": "vendor_selection"}
            }
            
        # 3. Pricing analysis phase
        if state.selected_vendors and not state.analysis_summary:
            return {
                "next_action": "pricing_analysis",
                "reason": "Test pricing",
                "parameters": {}
            }
            
        # 4. Wait for price approval
        if state.analysis_summary and not state.draft:
            if not hasattr(state, "mock_price_waiting"):
                state.mock_price_waiting = False
            if not state.mock_price_waiting:
                state.mock_price_waiting = True
                return {
                    "next_action": "wait_for_human",
                    "reason": "Test wait pricing",
                    "parameters": {"step": "price_approval"}
                }
            else:
                return {
                    "next_action": "draft_outreach",
                    "reason": "Test drafting",
                    "parameters": {}
                }
            
        # 5. Draft and reflection phases
        if state.draft and not state.improved_draft:
            return {
                "next_action": "self_reflection",
                "reason": "Test reflection",
                "parameters": {}
            }
            
        # 6. Wait for final approval
        if state.improved_draft and not state.execution_result:
            if not hasattr(state, "mock_final_waiting"):
                state.mock_final_waiting = False
            if not state.mock_final_waiting:
                state.mock_final_waiting = True
                return {
                    "next_action": "wait_for_human",
                    "reason": "Test wait final",
                    "parameters": {"step": "final_approval"}
                }
            else:
                return {
                    "next_action": "execute_outreach",
                    "reason": "Test execute",
                    "parameters": {}
                }
            
        # 7. Complete
        return {
            "next_action": "complete",
            "reason": "Test complete",
            "parameters": {}
        }
    m_decide.side_effect = side_effect_decide

    with patch.object(config, "AGENT_STEP_DELAY_SECONDS", 0), \
         patch.object(config, "WAIT_FOR_HUMAN_TIMEOUT", 2), \
         patch.object(config, "APPROVAL_TIMEOUT_SECONDS", 2), \
         patch("app.services.agent_planner.planner.decide_next_action", new=m_decide):
        yield
        
    tool_registry._registry = original_tools


def test_websocket_stop_in_waiting_approval(test_client):
    """Test that sending STOP during WAITING_APPROVAL cancels task and returns TASK_CANCELLED."""
    with test_client.websocket_connect("/v1/agent/connect") as ws:
        # 1. Start task
        ws.send_json({
            "event_type": "START_TASK",
            "prompt": "Test prompt"
        })
        
        # 2. Wait for approval required event to reach WAITING_APPROVAL state
        task_id = None
        for _ in range(10):
            msg = ws.receive_json()
            if msg.get("event_type") == "APPROVAL_REQUIRED":
                task_id = msg.get("task_id")
                break
                
        assert task_id is not None
        session = workflow_repo.get_session(task_id)
        assert session.status == RuntimeWorkflowState.WAITING_APPROVAL
        
        # 3. Send STOP with matching version and action_id
        ws.send_json({
            "event_type": "STOP",
            "task_id": task_id,
            "workflow_version": session.workflow_version,
            "action_id": "stop-action-123"
        })
        
        # 4. Receive TASK_CANCELLED
        cancelled_msg = ws.receive_json()
        assert cancelled_msg.get("event_type") == "TASK_CANCELLED"
        
        # 5. Verify database state is CANCELLED
        updated_session = workflow_repo.get_session(task_id)
        assert updated_session.status == RuntimeWorkflowState.CANCELLED


def test_websocket_stop_already_completed(test_client):
    """Test that sending STOP when task is already completed returns ALREADY_COMPLETED error."""
    # Pre-populate a completed session in database
    task_id = "test-completed-task-id"
    now = "2026-05-26T18:00:00Z"
    legacy_state = WorkflowState(prompt="Test completed task")
    session = WorkflowSession(
        task_id=task_id,
        status=RuntimeWorkflowState.COMPLETED,
        user_prompt="Test completed task",
        workflow_state_json=legacy_state.to_json(),
        created_at=now,
        updated_at=now,
        workflow_version=5
    )
    workflow_repo.save_session(session)
    
    with test_client.websocket_connect("/v1/agent/connect") as ws:
        # Reconnect/Start the task
        ws.send_json({
            "event_type": "START_TASK",
            "prompt": "Test completed task",
            "task_id": task_id
        })
        
        # Receive connection success or status
        ws.receive_json()
        
        # Send STOP
        ws.send_json({
            "event_type": "STOP",
            "task_id": task_id,
            "workflow_version": 5,
            "action_id": "stop-action-completed"
        })
        
        # Expect ALREADY_COMPLETED error
        err_msg = ws.receive_json()
        assert err_msg.get("event_type") == "ERROR"
        assert err_msg.get("error_code") == "ALREADY_COMPLETED"


def test_websocket_version_mismatch(test_client):
    """Test that sending mismatch version in APPROVAL_RESPONSE or STOP returns STALE_WORKFLOW_VERSION."""
    with test_client.websocket_connect("/v1/agent/connect") as ws:
        # Start task
        ws.send_json({
            "event_type": "START_TASK",
            "prompt": "Test version mismatch prompt"
        })
        
        # Wait for approval
        task_id = None
        for _ in range(10):
            msg = ws.receive_json()
            if msg.get("event_type") == "APPROVAL_REQUIRED":
                task_id = msg.get("task_id")
                break
                
        assert task_id is not None
        
        # Send APPROVAL_RESPONSE with STALE version
        ws.send_json({
            "event_type": "APPROVAL_RESPONSE",
            "action": "APPROVE",
            "task_id": task_id,
            "workflow_version": 999,  # Mismatched version
            "action_id": "approval-stale"
        })
        
        # Expect STALE_WORKFLOW_VERSION error
        err_msg = ws.receive_json()
        assert err_msg.get("event_type") == "ERROR"
        assert err_msg.get("error_code") == "STALE_WORKFLOW_VERSION"
        
        # Send STOP with STALE version
        ws.send_json({
            "event_type": "STOP",
            "task_id": task_id,
            "workflow_version": 999,  # Mismatched version
            "action_id": "stop-stale"
        })
        
        # Expect STALE_WORKFLOW_VERSION error
        err_msg_stop = ws.receive_json()
        assert err_msg_stop.get("event_type") == "ERROR"
        assert err_msg_stop.get("error_code") == "STALE_WORKFLOW_VERSION"


def test_rest_state_recovery_schema(test_client):
    """Test that GET /v1/workflow/{task_id} returns the exact required schema."""
    task_id = "test-rest-schema-task-id"
    now = "2026-05-26T18:00:00Z"
    legacy_state = WorkflowState(prompt="REST Schema task")
    legacy_state.current_step = TaskState.WAITING_FINAL_APPROVAL
    legacy_state.draft = "Outreach Draft Email"
    legacy_state.research_data = {
        "category": "computer",
        "vendors": [{"name": "TestVendor1", "location": "Marathahalli"}]
    }
    
    session = WorkflowSession(
        task_id=task_id,
        status=RuntimeWorkflowState.WAITING_APPROVAL,
        user_prompt="REST Schema task",
        workflow_state_json=legacy_state.to_json(),
        created_at=now,
        updated_at=now,
        workflow_version=3
    )
    workflow_repo.save_session(session)
    
    # Retrieve workflow session via GET API
    response = test_client.get(f"/v1/workflow/{task_id}")
    assert response.status_code == 200
    
    data = response.json()
    
    # Verify exact schema keys and values
    assert data["task_id"] == task_id
    assert data["state"] == "WAITING_FINAL_APPROVAL"
    assert data["workflow_version"] == 3
    assert isinstance(data["messages"], list)
    assert len(data["messages"]) > 0
    
    # Verify approval_payload structure
    payload = data["approval_payload"]
    assert payload["draft_message"] == "Outreach Draft Email"
    assert len(payload["vendors"]) == 1
    assert payload["vendors"][0]["name"] == "TestVendor1"
