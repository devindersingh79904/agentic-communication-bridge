import asyncio
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from starlette.websockets import WebSocketState

from app.core import config
from app.core.enums import TaskState, ApprovalAction, AgentStep
from app.models.workflow_models import ExecutionPlan, PlanStep, RuntimeWorkflowState
from app.models.workflow_state import WorkflowState
from app.runtime.workflow_runtime import workflow_runtime, _active_tasks, _active_events
from app.storage.workflow_repository import workflow_repo
from app.websocket.connection_manager import connection_manager

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

@pytest.fixture
def mock_lifecycle_tools():
    """Mock registry tools and planner for workflow runtime tests."""
    from app.core.tool_registry import tool_registry
    original_tools = dict(tool_registry._registry)
    
    m_res = AsyncMock()
    m_ana = AsyncMock()
    m_dra = AsyncMock()
    m_ref = AsyncMock()
    m_exe = AsyncMock()
    
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
        state.improved_draft = "Refined Draft Message"
    m_ref.side_effect = side_effect_reflect

    async def side_effect_execute(state, *args, **kwargs):
        state.execution_result = "Execution Succeeded"
    m_exe.side_effect = side_effect_execute

    tool_registry.register("vendor_search", m_res)
    tool_registry.register("pricing_analysis", m_ana)
    tool_registry.register("draft_outreach", m_dra)
    tool_registry.register("self_reflection", m_ref)
    tool_registry.register("execute_outreach", m_exe)

    yield m_res, m_ana, m_dra, m_ref, m_exe
    
    tool_registry._registry = original_tools

async def wait_for_task_state(task_id, target_state, timeout=2.0):
    steps = int(timeout / 0.01)
    for _ in range(steps):
        session = workflow_repo.get_session(task_id)
        if session:
            legacy_state = WorkflowState.from_json(session.workflow_state_json)
            if legacy_state.current_step == target_state:
                return True
        await asyncio.sleep(0.01)
    return False

async def wait_for_session_status_cycle(task_id, timeout=2.0):
    # 1. Wait for status to leave WAITING_APPROVAL
    steps = int(timeout / 0.01)
    for _ in range(steps):
        session = workflow_repo.get_session(task_id)
        if session and session.status != RuntimeWorkflowState.WAITING_APPROVAL:
            break
        await asyncio.sleep(0.01)
        
    # 2. Wait for status to become WAITING_APPROVAL again
    for _ in range(steps):
        session = workflow_repo.get_session(task_id)
        if session and session.status == RuntimeWorkflowState.WAITING_APPROVAL:
            return True
        await asyncio.sleep(0.01)
    return False

@pytest.mark.asyncio
async def test_full_approval_flow(mock_lifecycle_tools):
    task_id = "task-lifecycle-approve"
    correlation_id = "corr-lifecycle-approve"
    ws = MockWebSocket()
    await connection_manager.register(task_id, ws, correlation_id)
    
    with patch("app.core.config.AGENT_STEP_DELAY_SECONDS", 0), \
         patch("app.core.config.WAIT_FOR_HUMAN_TIMEOUT", 2.0), \
         patch("app.agents.planner_agent.planner_agent.generate_plan", new_callable=AsyncMock) as mock_gen:
        
        from app.agents.planner_agent import planner_agent
        mock_gen.return_value = planner_agent._get_fallback_plan()
        
        # Start
        await workflow_runtime.start_orchestration(task_id, "Need server parts", correlation_id)
        
        # 1. Vendor Selection Gate
        assert await wait_for_task_state(task_id, TaskState.WAITING_VENDOR_SELECTION) is True
        
        session = workflow_repo.get_session(task_id)
        await workflow_runtime.handle_approval_response(
            task_id,
            ApprovalAction.APPROVE,
            selected_vendors=[{"name": "TestVendor", "location": "TestLocation"}],
            incoming_version=session.workflow_version
        )
        
        # 2. Final Outreach Gate
        assert await wait_for_task_state(task_id, TaskState.WAITING_FINAL_APPROVAL) is True
        
        session = workflow_repo.get_session(task_id)
        await workflow_runtime.handle_approval_response(
            task_id,
            ApprovalAction.APPROVE,
            incoming_version=session.workflow_version
        )
        
        # Wait for the task loop to finish
        background_task = _active_tasks.get(task_id)
        if background_task:
            await background_task
            
        # Verify complete status
        session = workflow_repo.get_session(task_id)
        assert session.status == RuntimeWorkflowState.COMPLETED
        
        event_types = [m.get("event_type") for m in ws.sent_messages]
        assert "APPROVAL_REQUIRED" in event_types
        assert "TASK_COMPLETED" in event_types

@pytest.mark.asyncio
async def test_rejection_regeneration_loop(mock_lifecycle_tools):
    task_id = "task-lifecycle-reject"
    correlation_id = "corr-lifecycle-reject"
    ws = MockWebSocket()
    await connection_manager.register(task_id, ws, correlation_id)
    
    with patch("app.core.config.AGENT_STEP_DELAY_SECONDS", 0), \
         patch("app.core.config.WAIT_FOR_HUMAN_TIMEOUT", 2.0), \
         patch("app.agents.planner_agent.planner_agent.generate_plan", new_callable=AsyncMock) as mock_gen, \
         patch("app.agents.planner_agent.planner_agent.replan", new_callable=AsyncMock) as mock_replan:
        
        from app.agents.planner_agent import planner_agent
        mock_gen.return_value = planner_agent._get_fallback_plan()
        mock_replan.return_value = planner_agent._get_fallback_plan()
        
        # Start
        await workflow_runtime.start_orchestration(task_id, "Need server parts", correlation_id)
        
        # Wait for vendor selection gate
        assert await wait_for_task_state(task_id, TaskState.WAITING_VENDOR_SELECTION) is True
        
        # Reject
        session = workflow_repo.get_session(task_id)
        await workflow_runtime.handle_approval_response(
            task_id,
            ApprovalAction.REJECT,
            feedback="Find cheaper option",
            incoming_version=session.workflow_version
        )
        
        # Wait for vendor selection gate again (due to replanning loopback)
        assert await wait_for_session_status_cycle(task_id) is True
        
        # Now approve vendor selection
        session = workflow_repo.get_session(task_id)
        await workflow_runtime.handle_approval_response(
            task_id,
            ApprovalAction.APPROVE,
            selected_vendors=[{"name": "CheapVendor", "location": "TestLocation"}],
            incoming_version=session.workflow_version
        )
        
        # Wait for final outreach gate
        assert await wait_for_task_state(task_id, TaskState.WAITING_FINAL_APPROVAL) is True
        
        session = workflow_repo.get_session(task_id)
        await workflow_runtime.handle_approval_response(
            task_id,
            ApprovalAction.APPROVE,
            incoming_version=session.workflow_version
        )
        
        background_task = _active_tasks.get(task_id)
        if background_task:
            await background_task
            
        session = workflow_repo.get_session(task_id)
        assert session.status == RuntimeWorkflowState.COMPLETED

@pytest.mark.asyncio
async def test_timeout_cancellation(mock_lifecycle_tools):
    task_id = "task-lifecycle-timeout"
    correlation_id = "corr-lifecycle-timeout"
    ws = MockWebSocket()
    await connection_manager.register(task_id, ws, correlation_id)
    
    with patch("app.core.config.AGENT_STEP_DELAY_SECONDS", 0), \
         patch("app.core.config.WAIT_FOR_HUMAN_TIMEOUT", 1), \
         patch("app.agents.planner_agent.planner_agent.generate_plan", new_callable=AsyncMock) as mock_gen:
        
        from app.agents.planner_agent import planner_agent
        mock_gen.return_value = planner_agent._get_fallback_plan()
        
        await workflow_runtime.start_orchestration(task_id, "Need server parts", correlation_id)
        
        background_task = _active_tasks.get(task_id)
        if background_task:
            await background_task
            
        session = workflow_repo.get_session(task_id)
        assert session.status == RuntimeWorkflowState.CANCELLED
        
        event_types = [m.get("event_type") for m in ws.sent_messages]
        assert "TASK_CANCELLED" in event_types

@pytest.mark.asyncio
async def test_stop_cancellation(mock_lifecycle_tools):
    task_id = "task-lifecycle-stop"
    correlation_id = "corr-lifecycle-stop"
    ws = MockWebSocket()
    await connection_manager.register(task_id, ws, correlation_id)
    
    with patch("app.core.config.AGENT_STEP_DELAY_SECONDS", 0), \
         patch("app.core.config.WAIT_FOR_HUMAN_TIMEOUT", 5.0), \
         patch("app.agents.planner_agent.planner_agent.generate_plan", new_callable=AsyncMock) as mock_gen:
        
        from app.agents.planner_agent import planner_agent
        mock_gen.return_value = planner_agent._get_fallback_plan()
        
        await workflow_runtime.start_orchestration(task_id, "Need server parts", correlation_id)
        
        # Wait until it hits first gate
        assert await wait_for_task_state(task_id, TaskState.WAITING_VENDOR_SELECTION) is True
        
        # Cancel the task
        await workflow_runtime.cancel_session(task_id)
        
        background_task = _active_tasks.get(task_id)
        if background_task:
            try:
                await background_task
            except asyncio.CancelledError:
                pass
                
        session = workflow_repo.get_session(task_id)
        assert session.status == RuntimeWorkflowState.CANCELLED
        
        # Idempotency check
        await workflow_runtime.cancel_session(task_id)
