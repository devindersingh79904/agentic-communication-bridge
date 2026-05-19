import asyncio
import random
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
    if vendors:
        selected_vendor = random.choice(vendors)
        state.selected_vendor = selected_vendor
        vendor_names = [v.get("name") for v in vendors]
        state.analysis_summary = (
            f"Analyzed pricing for vendors: {', '.join(vendor_names)}. "
            f"Selected preferred vendor: {selected_vendor['name']} located in "
            f"{selected_vendor['location']} based on pricing and availability."
        )
    else:
        state.analysis_summary = "No vendors found to analyze."
    logger.info("Analysis tool execution completed")
