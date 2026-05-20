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
         patch.object(config, "APPROVAL_TIMEOUT_SECONDS", 1):
        yield

@pytest.fixture
def mock_tools():
    """Mock out all orchestrator tool dependencies."""
    with patch("app.services.agent_orchestrator_service.research_tool", new_callable=AsyncMock) as m_res, \
         patch("app.services.agent_orchestrator_service.analysis_tool", new_callable=AsyncMock) as m_ana, \
         patch("app.services.agent_orchestrator_service.draft_tool", new_callable=AsyncMock) as m_dra, \
         patch("app.services.agent_orchestrator_service.reflection_tool", new_callable=AsyncMock) as m_ref, \
         patch("app.services.agent_orchestrator_service.execution_tool", new_callable=AsyncMock) as m_exe:
        
        # Populate draft on draft_tool and reflection_tool
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

async def wait_for_regeneration_count(state, expected_count, timeout=5.0):
    steps = int(timeout / 0.01)
    for _ in range(steps):
        if state.regeneration_count == expected_count:
            return True
        await asyncio.sleep(0.01)
    return False

@pytest.mark.asyncio
async def test_full_approval_flow(mock_tools):
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
    
    # Wait until task transitions to WAITING_APPROVAL
    assert await wait_for_state(task_id, TaskState.WAITING_APPROVAL) is True
    
    # Simulate user approval
    handle_approval_response(task_id, ApprovalAction.APPROVE)
    
    await orchestration_task
    
    # Assert successful termination events
    assert ws.closed is True
    event_types = [msg.get("event_type") for msg in ws.sent_messages]
    assert "APPROVAL_REQUIRED" in event_types
    assert "TASK_COMPLETED" in event_types
    
    # Final state in completed event must be SUCCESS
    completed_msg = ws.sent_messages[-1]
    assert completed_msg["event_type"] == "TASK_COMPLETED"
    assert completed_msg["task_state"] == "SUCCESS"

@pytest.mark.asyncio
async def test_rejection_regeneration_loop(mock_tools):
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
    
    # Wait until task transitions to WAITING_APPROVAL
    assert await wait_for_state(task_id, TaskState.WAITING_APPROVAL) is True
    
    # First Reject
    handle_approval_response(task_id, ApprovalAction.REJECT, feedback="Too casual")
    
    # Wait for task to increment regeneration count (regeneration starts)
    assert await wait_for_regeneration_count(state, 1) is True
    
    # Task should loop back to WAITING_APPROVAL
    assert await wait_for_state(task_id, TaskState.WAITING_APPROVAL) is True
        
    assert state.regeneration_count == 1
    assert active_tasks[task_id]["task_state"] == TaskState.WAITING_APPROVAL
    
    # Second Approve
    handle_approval_response(task_id, ApprovalAction.APPROVE)
    await orchestration_task
    
    completed_msg = ws.sent_messages[-1]
    assert completed_msg["event_type"] == "TASK_COMPLETED"
    assert completed_msg["task_state"] == "SUCCESS"

@pytest.mark.asyncio
async def test_max_regeneration_attempts(mock_tools):
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
    
    # Loop max regeneration times + 1 to exceed limits
    for i in range(config.MAX_REGENERATION_ATTEMPTS + 1):
        assert await wait_for_state(task_id, TaskState.WAITING_APPROVAL) is True
        handle_approval_response(task_id, ApprovalAction.REJECT, feedback=f"Feedback {i}")
        if i < config.MAX_REGENERATION_ATTEMPTS:
            assert await wait_for_regeneration_count(state, i + 1) is True
            
    await orchestration_task
    
    # Final event should be ERROR due to exceeding limit
    completed_msg = ws.sent_messages[-1]
    assert completed_msg["event_type"] == "ERROR"
    assert completed_msg["error_code"] == "MAX_REGENERATION_EXCEEDED"

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
    
    # Wait until it is running/waiting
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
    
    # First cancel call - this will wait for the task to finish cancel/cleanup
    await cancel_task(task_id)
    
    # Second and third cancel calls (STOP spam)
    # They should return without raising errors or crashing
    await cancel_task(task_id)
    await cancel_task(task_id)
    
    await orchestration_task
    
    # Task should be cleaned up from active_tasks
    assert task_id not in active_tasks
    
    # Event should be emitted exactly once
    assert ws.closed is True
    cancelled_messages = [msg for msg in ws.sent_messages if msg.get("event_type") == "TASK_CANCELLED"]
    assert len(cancelled_messages) == 1
