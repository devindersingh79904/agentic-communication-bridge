import logging
from app.core.logger import get_logger
from app.models.workflow_state import WorkflowState
from app.services.agent_planner import planner

logger = get_logger("tools.draft")

async def draft_tool(state: WorkflowState, progress_callback = None) -> None:
    """
    Upgraded Draft Tool.
    Delegates to the agent planner to generate outreach text targeting the selected vendor.
    """
    logger.info("Draft tool execution started")
    await planner.run_draft(state)
    logger.info("Draft tool execution completed")

