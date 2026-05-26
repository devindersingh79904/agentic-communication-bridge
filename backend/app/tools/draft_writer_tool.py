import logging
from typing import Callable, Any, Optional
from app.tools.base_tool import BaseTool
from app.models.workflow_models import WorkflowSession, ToolResult
from app.models.workflow_state import WorkflowState

logger = logging.getLogger("app.tools.draft_writer")

class DraftWriterTool(BaseTool):
    """
    Class-based tool for drafting procurement emails.
    """
    @property
    def name(self) -> str:
        return "draft_outreach"

    @property
    def description(self) -> str:
        return "Generates a customized outreach email proposal tailored to the selected vendor."

    async def execute(self, session: WorkflowSession, progress_callback: Optional[Callable[[str], Any]] = None) -> ToolResult:
        logger.info(f"Executing DraftWriterTool via runtime for task {session.task_id}")
        state = WorkflowState.from_json(session.workflow_state_json)
        
        if progress_callback:
            await progress_callback("Drafting outreach communication...")
            
        # Lazy local import to resolve circular dependencies
        from app.services.agent_planner import planner
        await planner.run_draft(state)
        session.workflow_state_json = state.to_json()
        
        return ToolResult(
            status="success",
            confidence=0.90,
            artifacts={"draft": state.draft},
            metadata={}
        )
