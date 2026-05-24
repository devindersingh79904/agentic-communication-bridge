import asyncio
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from starlette.websockets import WebSocketState

from app.core.enums import TaskState, ApprovalAction, AgentStep
from app.models.workflow_state import WorkflowState
from app.services.agent_orchestrator_service import (
    run_orchestration,
    register_task,
    set_task_reference,
    cancel_task,
    handle_approval_response,
    active_tasks,
    transition_task_state
)
from app.core import config

class MockWebSocket:
    def __init__(self):
        self.sent_messages = []
        self.client_state = WebSocketState.CONNECTED
        self.closed = False
        
    async def send_json(self, data):
        self.sent_messages.append(data)
        
    async def close(self, code=1000):
        self.closed = True
        self.client_state = WebSocketState.DISCONNECTED

@pytest.fixture(autouse=True)
def speed_up_config():
    """Speeds up delay and timeout for tests."""
    with patch.object(config, "AGENT_STEP_DELAY_SECONDS", 0), \
         patch.object(config, "APPROVAL_TIMEOUT_SECONDS", 2):
        yield

@pytest.fixture
def mock_tools():
    """Mock out all orchestrator tool dependencies."""
    with patch("app.services.agent_orchestrator_service.research_tool", new_callable=AsyncMock) as m_res, \
         patch("app.services.agent_orchestrator_service.analysis_tool", new_callable=AsyncMock) as m_ana, \
         patch("app.services.agent_orchestrator_service.draft_tool", new_callable=AsyncMock) as m_dra, \
         patch("app.services.agent_orchestrator_service.reflection_tool", new_callable=AsyncMock) as m_ref, \
         patch("app.services.agent_orchestrator_service.execution_tool", new_callable=AsyncMock) as m_exe:
        
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
            state.improved_draft = "Refined Draft Message"
        m_ref.side_effect = side_effect_reflect

        async def side_effect_execute(state):
            state.execution_result = "Execution Succeeded"
        m_exe.side_effect = side_effect_execute

        yield m_res, m_ana, m_dra, m_ref, m_exe

async def wait_for_state(task_id, target_state, timeout=5.0):
    steps = int(timeout / 0.01)
    for _ in range(steps):
        if active_tasks.get(task_id, {}).get("task_state") == target_state:
            return True
        await asyncio.sleep(0.01)
    return False

@pytest.mark.asyncio
async def test_full_approval_flow(mock_tools):
    """
    Test full approval through all 4 steps (research, analysis, draft, reflection)
    and execution. Each step is approved.
    """
    ws = MockWebSocket()
    task_id = "task-approve"
    correlation_id = "corr-approve"
    state = WorkflowState(prompt="Need server parts")
    approval_event = asyncio.Event()
    
    await register_task(task_id, ws, approval_event)
    
    orchestration_task = asyncio.create_task(
        run_orchestration(ws, correlation_id, task_id, state)
    )
    set_task_reference(task_id, orchestration_task)
    
    # Approve 4 steps (research, analysis, draft, reflection)
    for i in range(4):
        assert await wait_for_state(task_id, TaskState.WAITING_APPROVAL) is True
        handle_approval_response(task_id, ApprovalAction.APPROVE)
    
    await orchestration_task
    
    # Assert successful termination events
    assert ws.closed is True
    event_types = [msg.get("event_type") for msg in ws.sent_messages]
    assert "APPROVAL_REQUIRED" in event_types
    assert "TASK_COMPLETED" in event_types
    
    # Count approval events
    approval_events = sum(1 for m in ws.sent_messages if m.get("event_type") == "APPROVAL_REQUIRED")
    assert approval_events == 4, f"Expected 4 approval events, got {approval_events}"
    
    # Final state in completed event must be SUCCESS
    completed_msg = ws.sent_messages[-1]
    assert completed_msg["event_type"] == "TASK_COMPLETED"
    assert completed_msg["task_state"] == "SUCCESS"

@pytest.mark.asyncio
async def test_rejection_regeneration_loop(mock_tools):
    """
    Test: reject at research step, re-run, then approve all remaining steps.
    """
    ws = MockWebSocket()
    task_id = "task-reject"
    correlation_id = "corr-reject"
    state = WorkflowState(prompt="Need server parts")
    approval_event = asyncio.Event()
    
    await register_task(task_id, ws, approval_event)
    
    orchestration_task = asyncio.create_task(
        run_orchestration(ws, correlation_id, task_id, state)
    )
    set_task_reference(task_id, orchestration_task)
    
    # Wait for first approval (research)
    assert await wait_for_state(task_id, TaskState.WAITING_APPROVAL) is True
    
    # Reject once
    handle_approval_response(task_id, ApprovalAction.REJECT, feedback="Too many vendors")
    
    # Give orchestrator time to process rejection and re-enter WAITING_APPROVAL
    await asyncio.sleep(0.05)
    assert await wait_for_state(task_id, TaskState.WAITING_APPROVAL) is True
    
    # Now approve research
    handle_approval_response(task_id, ApprovalAction.APPROVE)
    
    # Approve remaining 3 steps (analysis, draft, reflection)
    for i in range(3):
        assert await wait_for_state(task_id, TaskState.WAITING_APPROVAL) is True
        handle_approval_response(task_id, ApprovalAction.APPROVE)
    
    await orchestration_task
    
    completed_msg = ws.sent_messages[-1]
    assert completed_msg["event_type"] == "TASK_COMPLETED"
    assert completed_msg["task_state"] == "SUCCESS"
    
    # 1 reject + 1 approve for step 1 + 3 remaining approves = 5 total approval events
    approval_events = sum(1 for m in ws.sent_messages if m.get("event_type") == "APPROVAL_REQUIRED")
    assert approval_events == 5, f"Expected 5 approval events, got {approval_events}"

@pytest.mark.asyncio
async def test_max_regeneration_attempts(mock_tools):
    """
    Test multiple rejections on research step, then approve and complete.
    """
    ws = MockWebSocket()
    task_id = "task-max-regen"
    correlation_id = "corr-max-regen"
    state = WorkflowState(prompt="Need server parts")
    approval_event = asyncio.Event()
    
    await register_task(task_id, ws, approval_event)
    
    orchestration_task = asyncio.create_task(
        run_orchestration(ws, correlation_id, task_id, state)
    )
    set_task_reference(task_id, orchestration_task)
    
    # Reject research step 3 times
    reject_count = 3
    for i in range(reject_count):
        assert await wait_for_state(task_id, TaskState.WAITING_APPROVAL) is True
        handle_approval_response(task_id, ApprovalAction.REJECT, feedback=f"Feedback {i}")
    
    # Give orchestrator time to process rejection and re-enter WAITING_APPROVAL
    await asyncio.sleep(0.05)
    assert await wait_for_state(task_id, TaskState.WAITING_APPROVAL) is True
    handle_approval_response(task_id, ApprovalAction.APPROVE)
    
    # Approve remaining 3 steps
    for i in range(3):
        assert await wait_for_state(task_id, TaskState.WAITING_APPROVAL) is True
        handle_approval_response(task_id, ApprovalAction.APPROVE)
    
    await orchestration_task
    
    completed_msg = ws.sent_messages[-1]
    assert completed_msg["event_type"] == "TASK_COMPLETED"
    assert completed_msg["task_state"] == "SUCCESS"
    
    # Total = reject_count + 4 approve events (research, analysis, draft, reflection)
    approval_events = sum(1 for m in ws.sent_messages if m.get("event_type") == "APPROVAL_REQUIRED")
    assert approval_events == reject_count + 4, f"Expected {reject_count + 4} approval events, got {approval_events}"

@pytest.mark.asyncio
async def test_timeout_cancellation(mock_tools):
    ws = MockWebSocket()
    task_id = "task-timeout"
    correlation_id = "corr-timeout"
    state = WorkflowState(prompt="Need server parts")
    approval_event = asyncio.Event()
    
    await register_task(task_id, ws, approval_event)
    
    orchestration_task = asyncio.create_task(
        run_orchestration(ws, correlation_id, task_id, state)
    )
    set_task_reference(task_id, orchestration_task)
    
    # Let it timeout (APPROVAL_TIMEOUT_SECONDS is patched to 1s)
    await orchestration_task
    
    completed_msg = ws.sent_messages[-1]
    assert completed_msg["event_type"] == "TASK_CANCELLED"
    assert "timeout" in completed_msg["message"].lower()

@pytest.mark.asyncio
async def test_stop_cancellation(mock_tools):
    ws = MockWebSocket()
    task_id = "task-stop"
    correlation_id = "corr-stop"
    state = WorkflowState(prompt="Need server parts")
    approval_event = asyncio.Event()
    
    await register_task(task_id, ws, approval_event)
    
    orchestration_task = asyncio.create_task(
        run_orchestration(ws, correlation_id, task_id, state)
    )
    set_task_reference(task_id, orchestration_task)
    
    await asyncio.sleep(0.01)
    
    await cancel_task(task_id)
    await orchestration_task
    
    completed_msg = ws.sent_messages[-1]
    assert completed_msg["event_type"] == "TASK_CANCELLED"

@pytest.mark.asyncio
async def test_stop_spam_idempotency(mock_tools):
    ws = MockWebSocket()
    task_id = "task-stop-spam"
    correlation_id = "corr-stop-spam"
    state = WorkflowState(prompt="Need server parts")
    approval_event = asyncio.Event()
    
    await register_task(task_id, ws, approval_event)
    
    orchestration_task = asyncio.create_task(
        run_orchestration(ws, correlation_id, task_id, state)
    )
    set_task_reference(task_id, orchestration_task)
    
    await asyncio.sleep(0.01)
    
    await cancel_task(task_id)
    await cancel_task(task_id)
    await cancel_task(task_id)
    
    await orchestration_task
    
    assert task_id not in active_tasks
    assert ws.closed is True
    cancelled_messages = [msg for msg in ws.sent_messages if msg.get("event_type") == "TASK_CANCELLED"]
    assert len(cancelled_messages) == 1