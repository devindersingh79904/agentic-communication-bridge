from app.core.logger import get_logger
from app.models.workflow_state import WorkflowState
from app.services.llm_service import generate_execution_result

logger = get_logger("tools.execution")

async def execution_tool(state: WorkflowState) -> None:
    """
    Uses LLM to generate a realistic execution result after approval.
    Writes state.execution_result.
    """
    logger.info("Execution tool execution started")
    state.execution_result = await generate_execution_result(
        prompt=state.prompt,
        improved_draft=state.improved_draft,
        draft=state.draft,
    )
    logger.info("Execution tool execution completed")
