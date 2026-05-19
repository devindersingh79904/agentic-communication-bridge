import asyncio
from app.core import config
from app.core.logger import get_logger
from app.models.workflow_state import WorkflowState

logger = get_logger("tools.execution")

async def execution_tool(state: WorkflowState) -> None:
    """
    Simulates final outreach delivery. Writes state.execution_result.
    """
    logger.info("Execution tool execution started")
    await asyncio.sleep(config.AGENT_STEP_DELAY_SECONDS)
    state.execution_result = "Approved outreach executed successfully"
    logger.info("Execution tool execution completed")
