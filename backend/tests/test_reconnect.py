import asyncio
import pytest
from unittest.mock import AsyncMock, patch
from starlette.websockets import WebSocketDisconnect
from app.core import config
from app.core.enums import TaskState, ApprovalAction
from app.storage.workflow_repository import workflow_repo
from app.models.workflow_models import RuntimeWorkflowState, WorkflowSession
from app.models.workflow_state import WorkflowState

@pytest.mark.asyncio
async def test_reconnection_and_restore_flow(test_client):
    """
    Mimics client disconnect and reconnect flow:
    1. Start task and advance to WAITING_FINAL_APPROVAL.
    2. Disconnect socket connection.
    3. Verify GET /v1/workflow/{task_id} returns state WAITING_FINAL_APPROVAL.
    4. Reconnect socket and approve.
    """
    # Force the planner to advance the state sequentially
    from app.core.tool_registry import tool_registry
    
    # Store original tools
    original_registry = dict(tool_registry._registry)
    
    m_res = AsyncMock()
    m_ana = AsyncMock()
    m_dra = AsyncMock()
    m_ref = AsyncMock()
    m_exe = AsyncMock()

    async def side_effect_research(state, *args, **kwargs):
        state.research_data = {
            "vendors": [{"name": "TestVendor", "location": "TestLocation"}],
            "market_insights": "Test insights"
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
         patch.object(config, "WAIT_FOR_HUMAN_TIMEOUT", 30), \
         patch.object(config, "APPROVAL_TIMEOUT_SECONDS", 30):
         
        # Connect client 1
        with test_client.websocket_connect("/v1/agent/connect") as ws:
            ws.send_json({
                "event_type": "START_TASK",
                "prompt": "Test reconnect task"
            })
            
            # Step 1: Wait for vendor selection HIL
            task_id = None
            for _ in range(10):
                msg = ws.receive_json()
                if msg.get("event_type") == "APPROVAL_REQUIRED":
                    task_id = msg.get("task_id")
                    break
            assert task_id is not None
            
            # Approve vendor selection
            session = workflow_repo.get_session(task_id)
            ws.send_json({
                "event_type": "APPROVAL_RESPONSE",
                "action": "APPROVE",
                "task_id": task_id,
                "workflow_version": session.workflow_version,
                "selected_vendors": [{"name": "TestVendor", "location": "TestLocation"}],
                "action_id": "act-vendor-approve"
            })
            
            # Step 2: Wait for final approval HIL
            for _ in range(10):
                msg = ws.receive_json()
                if msg.get("event_type") == "APPROVAL_REQUIRED" and msg.get("task_state") == "WAITING_FINAL_APPROVAL":
                    break
                    
            # 2. Simulate socket disconnect (context block exit closes socket)
            
        # 3. Retrieve state via GET REST API
        response = test_client.get(f"/v1/workflow/{task_id}")
        assert response.status_code == 200
        rest_data = response.json()
        assert rest_data["state"] == "WAITING_FINAL_APPROVAL"
        assert rest_data["workflow_version"] > 1
        
        # 4. Reconnect to WebSocket and approve final draft
        with test_client.websocket_connect("/v1/agent/connect") as ws2:
            # Send START_TASK with existing task_id to reconnect
            ws2.send_json({
                "event_type": "START_TASK",
                "prompt": "Test reconnect task",
                "task_id": task_id
            })
            
            # Receive status updates and approval required event
            resumed_version = None
            for _ in range(5):
                msg = ws2.receive_json()
                if msg.get("event_type") == "STATUS_UPDATE":
                    resumed_version = msg.get("workflow_version")
                if msg.get("event_type") == "APPROVAL_REQUIRED":
                    break
            
            # Send final approval
            ws2.send_json({
                "event_type": "APPROVAL_RESPONSE",
                "action": "APPROVE",
                "task_id": task_id,
                "workflow_version": resumed_version,
                "action_id": "act-final-approve"
            })
            
            # Receive completion event
            completed_msg = None
            for _ in range(5):
                msg = ws2.receive_json()
                if msg.get("event_type") == "TASK_COMPLETED":
                    completed_msg = msg
                    break
            assert completed_msg is not None
            assert completed_msg["task_state"] == "COMPLETED"
            
    # Clean registry
    tool_registry._registry = original_registry
