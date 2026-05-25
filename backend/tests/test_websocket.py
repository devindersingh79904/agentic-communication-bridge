import asyncio
import pytest
from unittest.mock import AsyncMock, patch
from starlette.websockets import WebSocketState
from fastapi.testclient import TestClient

from app.core import config
from app.services.agent_orchestrator_service import active_tasks
from app.core.enums import TaskState, ApprovalAction

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
    async def side_effect_research(state):
        state.research_data = {
            "vendors": [{"name": "TestVendor", "location": "TestLocation"}],
            "market_insights": "Test insights",
            "recommended_approach": "Test approach",
        }
    m_res.side_effect = side_effect_research

    async def side_effect_analysis(state):
        state.analysis_summary = "Analysis complete"
        state.selected_vendor = {"name": "TestVendor", "location": "TestLocation"}
    m_ana.side_effect = side_effect_analysis

    async def side_effect_draft(state):
        state.draft = "Draft Message"
    m_dra.side_effect = side_effect_draft

    async def side_effect_reflect(state):
        state.improved_draft = "Refined Draft"
    m_ref.side_effect = side_effect_reflect

    async def side_effect_execute(state):
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
        
        if state.current_step in (TaskState.SCHEDULED, TaskState.RUNNING):
            return {
                "next_action": "vendor_search",
                "reason": "Test search",
                "parameters": {}
            }
            
        if state.current_step == TaskState.SEARCHING_VENDORS:
            return {
                "next_action": "pricing_analysis",
                "reason": "Test pricing",
                "parameters": {}
            }
            
        if state.current_step == TaskState.ANALYZING_PRICING:
            return {
                "next_action": "draft_outreach",
                "reason": "Test drafting",
                "parameters": {}
            }
            
        if state.current_step == TaskState.DRAFTING_OUTREACH:
            return {
                "next_action": "self_reflection",
                "reason": "Test reflection",
                "parameters": {}
            }
            
        if state.current_step == TaskState.SELF_REFLECTION:
            return {
                "next_action": "wait_for_human",
                "reason": "Test wait final",
                "parameters": {"step": "final_approval"}
            }
            
        if state.current_step == TaskState.WAITING_FINAL_APPROVAL:
            if state.approval_action == ApprovalAction.APPROVE:
                state.approval_action = None
                return {
                    "next_action": "execute_outreach",
                    "reason": "Test execute",
                    "parameters": {}
                }
            elif state.approval_action == ApprovalAction.REJECT:
                feedback = state.rejection_feedback or ""
                state.approval_action = None
                state.rejection_feedback = None
                
                # Check feedback to trigger search or draft rewrite
                if "expensive" in feedback.lower() or "too many" in feedback.lower():
                    state.research_data = None
                    state.analysis_summary = None
                    state.selected_vendor = None
                    state.draft = None
                    state.improved_draft = None
                    return {
                        "next_action": "vendor_search",
                        "reason": f"Retry search: {feedback}",
                        "parameters": {}
                    }
                else:
                    state.draft = None
                    state.improved_draft = None
                    return {
                        "next_action": "draft_outreach",
                        "reason": f"Retry draft: {feedback}",
                        "parameters": {}
                    }
            else:
                return {
                    "next_action": "wait_for_human",
                    "reason": "Test wait final",
                    "parameters": {"step": "final_approval"}
                }
                
        if state.current_step == TaskState.COMPLETED:
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


def test_websocket_approval_flow(test_client):
    """Test full approval flow over real WebSocket using TestClient."""
    with test_client.websocket_connect("/v1/agent/connect") as ws:
        # 1. Send START_TASK
        ws.send_json({
            "event_type": "START_TASK",
            "prompt": "Find hardware suppliers"
        })

        # 2. Receive status updates and approval required events (1 wait step now)
        events = []
        for _ in range(12):
            msg = ws.receive_json()
            events.append(msg)
            if msg.get("event_type") == "APPROVAL_REQUIRED":
                break

        event_types = [e.get("event_type") for e in events]
        assert "STATUS_UPDATE" in event_types
        assert "APPROVAL_REQUIRED" in event_types

        app_req = events[-1]
        assert app_req["draft_message"]  # Non-empty

        # Verify task is active in registry
        task_id = app_req["task_id"]
        assert task_id in active_tasks

        # Send APPROVE
        payload = {
            "event_type": "APPROVAL_RESPONSE",
            "action": "APPROVE"
        }
        ws.send_json(payload)

        # 3. Receive final success event
        events_after = []
        for _ in range(5):
            msg = ws.receive_json()
            events_after.append(msg)
            if msg.get("event_type") == "TASK_COMPLETED":
                break

        assert events_after[-1]["event_type"] == "TASK_COMPLETED"
        assert events_after[-1]["task_state"] == "COMPLETED"


def test_websocket_disconnect_cleanup(test_client):
    """Test that unexpected websocket disconnect cleans up the task registry."""
    task_id = None

    # Connect and start task
    with test_client.websocket_connect("/v1/agent/connect") as ws:
        ws.send_json({
            "event_type": "START_TASK",
            "prompt": "Find hardware suppliers"
        })

        # Wait for approval required to ensure task is fully registered
        for _ in range(15):
            msg = ws.receive_json()
            if msg.get("event_type") == "APPROVAL_REQUIRED":
                task_id = msg.get("task_id")
                break

        assert task_id is not None
        assert task_id in active_tasks

        # Now close the connection (simulating unexpected client disconnect)
        ws.close()

    # Give async loop a tick to process the disconnect cleanup
    import time
    for _ in range(50):
        if task_id not in active_tasks:
            break
        time.sleep(0.01)

    # Verify task registry was cleaned up and no orphan tasks remain
    assert task_id not in active_tasks