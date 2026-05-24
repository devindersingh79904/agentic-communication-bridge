from app.core.logger import get_logger
from app.models.workflow_state import WorkflowState
from app.services.llm_service import generate_analysis

logger = get_logger("tools.analysis")

async def analysis_tool(state: WorkflowState) -> None:
    """
    Uses LLM to analyze vendor research data and recommend the best vendor.
    Reads state.research_data, writes state.analysis_summary and state.selected_vendor.
    """
    logger.info("Analysis tool execution started")
    research_data = state.research_data or {}
    analysis_summary, selected_vendor = await generate_analysis(research_data, state.prompt)
    state.analysis_summary = analysis_summary
    state.selected_vendor = selected_vendor
    logger.info("Analysis tool execution completed")
