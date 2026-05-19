from dataclasses import dataclass, field
from typing import Optional


@dataclass
class WorkflowState:
    """
    Lightweight shared workflow state container inspired by graph-based
    orchestration systems. Acts as the centralized mutable runtime context
    shared between the orchestrator, tools, and LLM integration layers.
    """
    prompt: str

    research_data: Optional[dict] = None
    analysis_summary: Optional[str] = None

    draft: Optional[str] = None
    improved_draft: Optional[str] = None

    execution_result: Optional[str] = None
