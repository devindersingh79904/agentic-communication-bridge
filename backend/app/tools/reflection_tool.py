import asyncio
from app.core import config
from app.core.logger import get_logger
from app.services.llm_service import self_reflect_draft

logger = get_logger("tools.reflection")

async def reflection_tool(draft: str) -> str:
    """
    Delegates self-reflection to LLM service wrapper.
    """
    logger.info("Reflection tool execution started")
    await asyncio.sleep(config.AGENT_STEP_DELAY_SECONDS)
    improved_draft = await self_reflect_draft(draft)
    logger.info("Reflection tool execution completed")
    return improved_draft
