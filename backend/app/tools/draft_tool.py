import asyncio
from app.core import config
from app.core.logger import get_logger
from app.models.workflow_state import WorkflowState
from app.services.llm_service import generate_outreach_draft

logger = get_logger("tools.draft")

async def draft_tool(state: WorkflowState) -> None:
    """
    Delegates initial draft generation to LLM service. Writes state.draft.
    """
    logger.info("Draft tool execution started")
    await asyncio.sleep(config.AGENT_STEP_DELAY_SECONDS)
    state.draft = await generate_outreach_draft(
        prompt=state.prompt,
        analysis_summary=state.analysis_summary or ""
    )
    logger.info("Draft tool execution completed")
