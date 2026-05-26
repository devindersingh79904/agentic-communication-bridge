import os
# Ensure dummy key is present before importing app modules
if "OPENAI_API_KEY" not in os.environ or not os.environ["OPENAI_API_KEY"]:
    os.environ["OPENAI_API_KEY"] = "mock-api-key"

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient
from app.main import app
from app.services.agent_orchestrator_service import active_tasks
from openai import AsyncOpenAI


@pytest.fixture(autouse=True)
async def clean_registry():
    """Cleans up active tasks registry and database tables before and after each test."""
    active_tasks.clear()
    from app.storage.workflow_repository import workflow_repo
    try:
        with workflow_repo._get_connection() as conn:
            conn.execute("DELETE FROM tasks")
            conn.execute("DELETE FROM task_transitions")
            conn.commit()
    except Exception:
        pass
    yield
    active_tasks.clear()
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