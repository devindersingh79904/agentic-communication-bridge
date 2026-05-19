import asyncio
from app.core import config
from app.core.logger import get_logger
from app.models.workflow_state import WorkflowState

logger = get_logger("tools.analysis")

async def analysis_tool(state: WorkflowState) -> None:
    """
    Simulates vendor pricing analysis. Reads state.research_data, writes state.analysis_summary.
    """
    logger.info("Analysis tool execution started")
    await asyncio.sleep(config.AGENT_STEP_DELAY_SECONDS)
    vendors = state.research_data.get("vendors", []) if state.research_data else []
    state.analysis_summary = f"Analyzed pricing for vendors: {', '.join(vendors)}. Found competitive options."
    logger.info("Analysis tool execution completed")
