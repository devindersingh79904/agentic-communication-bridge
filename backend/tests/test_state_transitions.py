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
    
    # RUNNING -> WAITING_APPROVAL
    assert transition_task_state(task_id, TaskState.WAITING_APPROVAL) is True
    assert active_tasks[task_id]["task_state"] == TaskState.WAITING_APPROVAL
    
    # WAITING_APPROVAL -> RUNNING (e.g. Reject flow)
    assert transition_task_state(task_id, TaskState.RUNNING) is True
    assert active_tasks[task_id]["task_state"] == TaskState.RUNNING
    
    # RUNNING -> WAITING_APPROVAL
    assert transition_task_state(task_id, TaskState.WAITING_APPROVAL) is True
    
    # WAITING_APPROVAL -> EXECUTING
    assert transition_task_state(task_id, TaskState.EXECUTING) is True
    assert active_tasks[task_id]["task_state"] == TaskState.EXECUTING
    
    # EXECUTING -> SUCCESS
    assert transition_task_state(task_id, TaskState.SUCCESS) is True
    assert active_tasks[task_id]["task_state"] == TaskState.SUCCESS

@pytest.mark.asyncio
async def test_invalid_transitions():
    task_id = "test-task-456"
    mock_websocket = MagicMock()
    mock_event = MagicMock()
    
    await register_task(task_id, mock_websocket, mock_event)
    
    # Invalid: SCHEDULED -> SUCCESS
    assert transition_task_state(task_id, TaskState.SUCCESS) is False
    assert active_tasks[task_id]["task_state"] == TaskState.SCHEDULED
    
    # Move to RUNNING
    transition_task_state(task_id, TaskState.RUNNING)
    
    # Invalid: RUNNING -> SUCCESS
    assert transition_task_state(task_id, TaskState.SUCCESS) is False
    
    # Transition to SUCCESS (through valid path)
    transition_task_state(task_id, TaskState.WAITING_APPROVAL)
    transition_task_state(task_id, TaskState.EXECUTING)
    transition_task_state(task_id, TaskState.SUCCESS)
    
    # Invalid: SUCCESS is terminal state, cannot move to any other state
    assert transition_task_state(task_id, TaskState.RUNNING) is False
    assert transition_task_state(task_id, TaskState.CANCELLED) is False

@pytest.mark.asyncio
async def test_non_existent_task():
    assert transition_task_state("non-existent-task", TaskState.RUNNING) is False
