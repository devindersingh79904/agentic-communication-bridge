from app.core.logger import get_logger
from app.models.workflow_state import WorkflowState
from app.services.llm_service import self_reflect_draft

logger = get_logger("tools.reflection")

async def reflection_tool(state: WorkflowState) -> None:
    """
    Delegates self-reflection to LLM service. Reads state.draft, writes state.improved_draft.
    """
    logger.info("Reflection tool execution started")
    state.improved_draft = await self_reflect_draft(
        draft=state.draft or "",
        prompt=state.prompt,
        rejection_feedback=state.rejection_feedback
    )
    logger.info("Reflection tool execution completed")
