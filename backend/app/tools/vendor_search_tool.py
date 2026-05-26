import logging
from typing import List, Dict, Any, Optional, Callable
from pydantic import BaseModel, Field

from app.rag.vendor_retriever import retrieve_vendors
from app.tools.base_tool import BaseTool
from app.models.workflow_models import WorkflowSession, ToolResult
from app.models.workflow_state import WorkflowState

logger = logging.getLogger("app.tools.vendor_search")

class VendorSearchInput(BaseModel):
    query: str = Field(..., description="The user's procurement or search query")
    category: Optional[str] = Field(None, description="Optional vendor category (computer, transport, food, stationery)")
    top_k: int = Field(3, description="Number of top results to return")

class VendorSearchOutput(BaseModel):
    vendors: List[Dict[str, Any]]
    confidence: float

async def vendor_search_tool(query: str, category: Optional[str] = None, top_k: int = 3) -> Dict[str, Any]:
    """
    Independently callable vendor search tool.
    Semantically searches the internal vendor database.
    """
    logger.info(f"Executing internal vendor search for: '{query}' (category: {category})")
    input_data = VendorSearchInput(query=query, category=category, top_k=top_k)
    matched = await retrieve_vendors(input_data.query, category=input_data.category, top_k=input_data.top_k)
    agg_confidence = max([v.get("confidence", 0.0) for v in matched]) if matched else 0.0
    output = VendorSearchOutput(vendors=matched, confidence=agg_confidence)
    return output.model_dump()

class VendorSearchTool(BaseTool):
    """
    Class-based tool implementation for the Agentic Runtime connection.
    """
    @property
    def name(self) -> str:
        return "vendor_search"

    @property
    def description(self) -> str:
        return "Searches internal RAG vector store and external Tavily web APIs for supplier options."

    async def execute(self, session: WorkflowSession, progress_callback: Optional[Callable[[str], Any]] = None) -> ToolResult:
        logger.info(f"Executing VendorSearchTool via runtime for task {session.task_id}")
        state = WorkflowState.from_json(session.workflow_state_json)

        if progress_callback:
            await progress_callback("Searching database and online catalogs...")

        async def transition_callback(new_state):
            state.current_step = new_state
            session.workflow_state_json = state.to_json()
            if progress_callback:
                await progress_callback(f"Search status updated: {new_state.value}")

        # Lazy local import to resolve circular import dependency
        from app.services.agent_planner import planner
        await planner.run_research(state, session.task_id, transition_callback)
        session.workflow_state_json = state.to_json()

        vendors = state.research_data.get("vendors", []) if state.research_data else []
        confidence = state.research_data.get("internal_confidence", 0.0) if state.research_data else 0.0

        return ToolResult(
            status="success",
            confidence=confidence,
            artifacts={"vendors": vendors},
            metadata={
                "category": state.research_data.get("category") if state.research_data else "computer",
                "external_search_triggered": state.research_data.get("external_search_triggered", False) if state.research_data else False
            }
        )
