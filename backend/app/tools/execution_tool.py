from typing import Callable, Any, Optional
from app.core.logger import get_logger
from app.models.workflow_state import WorkflowState
from app.services.llm_service import generate_execution_result
from app.tools.base_tool import BaseTool
from app.models.workflow_models import WorkflowSession, ToolResult

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

class ExecuteOutreachTool(BaseTool):
    """
    Class-based tool to simulate sending the email outreach.
    """
    @property
    def name(self) -> str:
        return "execute_outreach"

    @property
    def description(self) -> str:
        return "Simulates execution of final outreach to the selected vendor."

    async def execute(self, session: WorkflowSession, progress_callback: Optional[Callable[[str], Any]] = None) -> ToolResult:
        logger.info(f"Executing ExecuteOutreachTool via runtime for task {session.task_id}")
        state = WorkflowState.from_json(session.workflow_state_json)

        if progress_callback:
            await progress_callback("Executing final procurement outreach...")

        await execution_tool(state)
        session.workflow_state_json = state.to_json()

        return ToolResult(
            status="success",
            confidence=1.0,
            artifacts={"execution_result": state.execution_result},
            metadata={}
        )
