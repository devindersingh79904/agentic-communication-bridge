import asyncio
from app.core import config
from app.core.logger import get_logger

logger = get_logger("tools.research")

async def research_tool() -> dict:
    """
    Simulates vendor research tool.
    """
    logger.info("Research tool execution started")
    await asyncio.sleep(config.AGENT_STEP_DELAY_SECONDS)
    data = {
        "vendors": [
            "Vendor A",
            "Vendor B"
        ]
    }
    logger.info("Research tool execution completed")
    return data
