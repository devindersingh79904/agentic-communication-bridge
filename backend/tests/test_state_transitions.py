import pytest
from unittest.mock import MagicMock
from app.core.enums import TaskState
from app.services.agent_orchestrator_service import (
    transition_task_state,
    register_task,
    active_tasks
)

@pytest.mark.asyncio
async def test_valid_transitions():
    task_id = "test-task-123"
    mock_websocket = MagicMock()
    mock_event = MagicMock()
    
    # Register task sets it to SCHEDULED
    await register_task(task_id, mock_websocket, mock_event)
    assert active_tasks[task_id]["task_state"] == TaskState.SCHEDULED
    
    # SCHEDULED -> RUNNING
    assert transition_task_state(task_id, TaskState.RUNNING) is True
    assert active_tasks[task_id]["task_state"] == TaskState.RUNNING
    
    # RUNNING -> WAITING_VENDOR_SELECTION
    assert transition_task_state(task_id, TaskState.WAITING_VENDOR_SELECTION) is True
    assert active_tasks[task_id]["task_state"] == TaskState.WAITING_VENDOR_SELECTION
    
    # WAITING_VENDOR_SELECTION -> RUNNING (e.g. Reject flow)
    assert transition_task_state(task_id, TaskState.RUNNING) is True
    assert active_tasks[task_id]["task_state"] == TaskState.RUNNING
    
    # RUNNING -> WAITING_PRICE_APPROVAL
    assert transition_task_state(task_id, TaskState.WAITING_PRICE_APPROVAL) is True
    
    # WAITING_PRICE_APPROVAL -> COMPLETED
    assert transition_task_state(task_id, TaskState.COMPLETED) is True
    assert active_tasks[task_id]["task_state"] == TaskState.COMPLETED

@pytest.mark.asyncio
async def test_invalid_transitions():
    task_id = "test-task-456"
    mock_websocket = MagicMock()
    mock_event = MagicMock()
    
    await register_task(task_id, mock_websocket, mock_event)
    
    # Invalid: COMPLETED is terminal, check that outgoing transitions from COMPLETED fail
    transition_task_state(task_id, TaskState.COMPLETED)
    assert active_tasks[task_id]["task_state"] == TaskState.COMPLETED
    
    # Invalid: COMPLETED is terminal state, cannot move to any other state
    assert transition_task_state(task_id, TaskState.RUNNING) is False
    assert transition_task_state(task_id, TaskState.CANCELLED) is False

@pytest.mark.asyncio
async def test_non_existent_task():
    assert transition_task_state("non-existent-task", TaskState.RUNNING) is False
