import logging
from app.core.logger import get_logger
from app.models.workflow_state import WorkflowState
from app.services.agent_planner import planner

logger = get_logger("tools.analysis")

async def analysis_tool(state: WorkflowState, progress_callback = None) -> None:
    """
    Upgraded Analysis Tool.
    Delegates execution to the agent planner to compare prices, ratings, and locations.
    """
    logger.info("Analysis tool execution started")
    await planner.run_analysis(state)
    logger.info("Analysis tool execution completed")

