import asyncio
import pytest
from unittest.mock import AsyncMock, patch
from starlette.websockets import WebSocketState
from fastapi.testclient import TestClient

from app.core import config
from app.runtime.workflow_runtime import _active_tasks
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

    with patch.object(config, "AGENT_STEP_DELAY_SECONDS", 0), \
         patch.object(config, "WAIT_FOR_HUMAN_TIMEOUT", 2), \
         patch.object(config, "APPROVAL_TIMEOUT_SECONDS", 2):
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

        # 2. Receive status updates and approval required events.
        # Runtime gates only vendor selection and final outreach approval; pricing is automatic.
        # We approve each step and continue
        for step_num in range(2):
            events = []
            for _ in range(5):
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
            assert task_id in _active_tasks

            # Send APPROVE with selected vendors list for vendor selection step
            payload = {
                "event_type": "APPROVAL_RESPONSE",
                "action": "APPROVE",
                "workflow_version": app_req.get("workflow_version", 1),
                "action_id": f"test-action-id-{step_num}"
            }
            if step_num == 0:
                payload["selected_vendors"] = [{"name": "TestVendor", "location": "TestLocation"}]
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
        for _ in range(5):
            msg = ws.receive_json()
            if msg.get("event_type") == "APPROVAL_REQUIRED":
                task_id = msg.get("task_id")
                break

        assert task_id is not None
        assert task_id in _active_tasks

        # Now close the connection (simulating unexpected client disconnect)
        ws.close()

    # Give async loop a tick to process the disconnect cleanup
    import time
    for _ in range(50):
        if task_id not in _active_tasks:
            break
        time.sleep(0.01)

    # Verify task registry was cleaned up and no orphan tasks remain
    assert task_id not in _active_tasks
