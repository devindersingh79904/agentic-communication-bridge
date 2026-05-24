from dataclasses import dataclass, field
from typing import Optional, Any
from app.core.enums import ApprovalAction, AgentStep

@dataclass
class WorkflowState:
    """
    Lightweight shared workflow state container inspired by graph-based
    orchestration systems (e.g., LangGraph's shared state nodes).

    Acts as the centralized mutable runtime context shared between the
    orchestrator, tools, and LLM integration layers. Each field is
    written by exactly one tool and read downstream, eliminating
    explicit parameter chaining across the orchestration pipeline.

    This state is intentionally ephemeral — scoped to a single WebSocket
    session lifecycle. No persistence layer is used because orchestration
    runs are short-lived, single-connection flows that are cleaned up
    immediately on completion, cancellation, or disconnect.
    """
    prompt: str

    research_data: Optional[dict] = None
    analysis_summary: Optional[str] = None
    selected_vendor: Optional[dict] = None

    draft: Optional[str] = None
    improved_draft: Optional[str] = None

    execution_result: Optional[str] = None

    regeneration_count: int = 0
    rejection_feedback: Optional[str] = None
    approval_action: Optional[ApprovalAction] = None

    # Multi-step approval tracking: which step the orchestrator is currently
    # paused on awaiting approval and the data produced by that step.
    pending_agent_step: Optional[AgentStep] = None
    pending_step_data: Optional[str] = None