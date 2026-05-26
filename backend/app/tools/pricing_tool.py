import logging
from typing import Callable, Any, Optional
from app.tools.base_tool import BaseTool
from app.models.workflow_models import WorkflowSession, ToolResult
from app.models.workflow_state import WorkflowState

logger = logging.getLogger("app.tools.pricing")

class PricingTool(BaseTool):
    """
    Class-based pricing comparison tool.
    """
    @property
    def name(self) -> str:
        return "pricing_analysis"

    @property
    def description(self) -> str:
        return "Compares prices, delivery speed, and ratings of discovered vendors to select the best option."

    async def execute(self, session: WorkflowSession, progress_callback: Optional[Callable[[str], Any]] = None) -> ToolResult:
        logger.info(f"Executing PricingTool via runtime for task {session.task_id}")
        state = WorkflowState.from_json(session.workflow_state_json)
        
        if progress_callback:
            await progress_callback("Analyzing catalogs and pricing...")
            
        # Lazy local import to resolve circular dependencies
        from app.services.agent_planner import planner
        await planner.run_analysis(state)
        session.workflow_state_json = state.to_json()
        
        recommended = state.selected_vendor
        confidence = 0.85
        if recommended:
            confidence = recommended.get("confidence", 0.85)
            
        return ToolResult(
            status="success",
            confidence=confidence,
            artifacts={
                "recommended_vendor": recommended,
                "analysis_summary": state.analysis_summary
            },
            metadata={}
        )
