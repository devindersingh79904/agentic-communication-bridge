from app.core.logger import get_logger
from app.models.workflow_state import WorkflowState
from app.services.llm_service import generate_research_data

logger = get_logger("tools.research")

async def research_tool(state: WorkflowState) -> None:
    """
    Uses LLM to perform deep research on the user's topic.
    Passes rejection feedback to the LLM for refinement on re-runs.
    Writes results to state.research_data.
    """
    logger.info("Research tool execution started for prompt: %.100s", state.prompt)
    feedback = state.rejection_feedback
    state.research_data = await generate_research_data(state.prompt, feedback=feedback)
    # Clear feedback after use so it doesn't leak to subsequent steps
    state.rejection_feedback = None
    logger.info("Research tool execution completed")
