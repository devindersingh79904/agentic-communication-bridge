import os
# Ensure dummy key is present before importing app modules
if "OPENAI_API_KEY" not in os.environ or not os.environ["OPENAI_API_KEY"]:
    os.environ["OPENAI_API_KEY"] = "mock-api-key"

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient
from app.main import app
from app.runtime.workflow_runtime import _active_tasks, _active_events
from openai import AsyncOpenAI


@pytest.fixture(autouse=True)
async def clean_registry():
    """Cleans up active tasks registry and database tables before and after each test."""
    _active_tasks.clear()
    _active_events.clear()
    from app.storage.workflow_repository import workflow_repo
    try:
        with workflow_repo._get_connection() as conn:
            conn.execute("DELETE FROM tasks")
            conn.execute("DELETE FROM task_transitions")
            conn.commit()
    except Exception:
        pass
    yield
    _active_tasks.clear()
    _active_events.clear()

    try:
        with workflow_repo._get_connection() as conn:
            conn.execute("DELETE FROM tasks")
            conn.execute("DELETE FROM task_transitions")
            conn.commit()
    except Exception:
        pass


@pytest.fixture
def test_client():
    """FastAPI TestClient fixture."""
    return TestClient(app)


@pytest.fixture
def mock_openai():
    """
    Mock the LLM client at the `get_client()` level.

    Creates a fake AsyncOpenAI instance whose
    `chat.completions.create` attribute is an AsyncMock.
    """
    mock_client = MagicMock(spec=AsyncOpenAI)
    mock_create = AsyncMock()
    mock_client.chat.completions.create = mock_create

    with patch("app.services.llm_service.get_client", return_value=mock_client):
        yield mock_create


@pytest.fixture(autouse=True)
def mock_planner_agent():
    """Mock PlannerAgent's LLM methods to return fallback plan immediately."""
    from app.agents.planner_agent import planner_agent
    
    original_generate = planner_agent.generate_plan
    original_replan = planner_agent.replan
    
    async def mock_generate(prompt):
        return planner_agent._get_fallback_plan()
        
    async def mock_replan(session, failure_reason):
        return planner_agent._get_fallback_plan()
        
    planner_agent.generate_plan = mock_generate
    planner_agent.replan = mock_replan
    yield
    planner_agent.generate_plan = original_generate
    planner_agent.replan = original_replan