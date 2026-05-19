import asyncio
from app.core import config
from app.core.logger import get_logger
from app.models.workflow_state import WorkflowState

logger = get_logger("tools.research")

async def research_tool(state: WorkflowState) -> None:
    """
    Simulates vendor research tool. Writes results to state.research_data.
    """
    logger.info("Research tool execution started for prompt: %.100s", state.prompt)
    await asyncio.sleep(config.AGENT_STEP_DELAY_SECONDS)
    state.research_data = {
        "prompt": state.prompt,
        "vendors": [
            {"name": "TechNova Systems", "location": "Marathahalli"},
            {"name": "ByteEdge Computers", "location": "Whitefield"},
            {"name": "NextGen PC Hub", "location": "Bellandur"}
        ]
    }
    logger.info("Research tool execution completed")
