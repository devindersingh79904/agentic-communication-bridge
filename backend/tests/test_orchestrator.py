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
         patch.object(config, "WAIT_FOR_HUMAN_TIMEOUT", 2), \
         patch.object(config, "APPROVAL_TIMEOUT_SECONDS", 2):
        yield

@pytest.fixture
def mock_tools():
    """Mock out all orchestrator tool dependencies registered in ToolRegistry."""
    from app.core.tool_registry import tool_registry
    
    # Keep track of original tools to restore them after the test
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
        state.improved_draft = "Refined Draft Message"
    m_ref.side_effect = side_effect_reflect

    async def side_effect_execute(state):
        state.execution_result = "Execution Succeeded"
    m_exe.side_effect = side_effect_execute

    # Re-register tools in registry with mocked functions
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

    with patch("app.services.agent_planner.planner.decide_next_action", new=m_decide):
        yield m_res, m_ana, m_dra, m_ref, m_exe
    
    # Restore original tools
    tool_registry._registry = original_tools

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
    Test full approval through the single final approval checkpoint and execution completion.
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
    
    # Approve WAITING_FINAL_APPROVAL step
    assert await wait_for_state(task_id, TaskState.WAITING_FINAL_APPROVAL) is True
    handle_approval_response(task_id, ApprovalAction.APPROVE)
    
    await orchestration_task
    
    # Assert successful termination events
    assert ws.closed is True
    event_types = [msg.get("event_type") for msg in ws.sent_messages]
    assert "APPROVAL_REQUIRED" in event_types
    assert "TASK_COMPLETED" in event_types
    
    # Count approval events
    approval_events = sum(1 for m in ws.sent_messages if m.get("event_type") == "APPROVAL_REQUIRED")
    assert approval_events == 1, f"Expected 1 approval event, got {approval_events}"
    
    # Final state in completed event must be COMPLETED
    completed_msg = ws.sent_messages[-1]
    assert completed_msg["event_type"] == "TASK_COMPLETED"
    assert completed_msg["task_state"] == TaskState.COMPLETED

@pytest.mark.asyncio
async def test_rejection_regeneration_loop(mock_tools):
    """
    Test: reject outreach draft/proposal, re-run, then approve.
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
    
    # Wait for first approval (final approval)
    assert await wait_for_state(task_id, TaskState.WAITING_FINAL_APPROVAL) is True
    
    # Reject once
    handle_approval_response(task_id, ApprovalAction.REJECT, feedback="Too expensive")
    
    # Give orchestrator time to process rejection and re-enter WAITING_FINAL_APPROVAL
    await asyncio.sleep(0.05)
    assert await wait_for_state(task_id, TaskState.WAITING_FINAL_APPROVAL) is True
    
    # Now approve final outreach proposal
    handle_approval_response(task_id, ApprovalAction.APPROVE)
    
    await orchestration_task
    
    completed_msg = ws.sent_messages[-1]
    assert completed_msg["event_type"] == "TASK_COMPLETED"
    assert completed_msg["task_state"] == TaskState.COMPLETED
    
    # 1 reject + 1 approve = 2 total approval events
    approval_events = sum(1 for m in ws.sent_messages if m.get("event_type") == "APPROVAL_REQUIRED")
    assert approval_events == 2, f"Expected 2 approval events, got {approval_events}"

@pytest.mark.asyncio
async def test_max_regeneration_attempts(mock_tools):
    """
    Test multiple rejections on final approval step, then approve and complete.
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
    
    # Reject final approval step 3 times
    reject_count = 3
    for i in range(reject_count):
        assert await wait_for_state(task_id, TaskState.WAITING_FINAL_APPROVAL) is True
        handle_approval_response(task_id, ApprovalAction.REJECT, feedback=f"Too expensive {i}")
        await asyncio.sleep(0.05)
    
    # Give orchestrator time to process rejection and re-enter WAITING_FINAL_APPROVAL
    assert await wait_for_state(task_id, TaskState.WAITING_FINAL_APPROVAL) is True
    handle_approval_response(task_id, ApprovalAction.APPROVE)
    
    await orchestration_task
    
    completed_msg = ws.sent_messages[-1]
    assert completed_msg["event_type"] == "TASK_COMPLETED"
    assert completed_msg["task_state"] == TaskState.COMPLETED
    
    # Total = reject_count + 1 approve event = 4
    approval_events = sum(1 for m in ws.sent_messages if m.get("event_type") == "APPROVAL_REQUIRED")
    assert approval_events == reject_count + 1, f"Expected {reject_count + 1} approval events, got {approval_events}"

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
    
    # Let it timeout (WAIT_FOR_HUMAN_TIMEOUT is patched to 2s)
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