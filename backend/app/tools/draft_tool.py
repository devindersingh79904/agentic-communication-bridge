import asyncio
from app.core import config
from app.core.logger import get_logger
from app.services.llm_service import generate_outreach_draft

logger = get_logger("tools.draft")

async def draft_tool(summary: str) -> str:
    """
    Delegates initial draft generation to LLM service wrapper.
    """
    logger.info("Draft tool execution started")
    await asyncio.sleep(config.AGENT_STEP_DELAY_SECONDS)
    draft = await generate_outreach_draft()
    logger.info("Draft tool execution completed")
    return draft
