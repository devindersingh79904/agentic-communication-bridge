import json
from dataclasses import dataclass, field
from typing import Optional, Any, List, Dict
from app.core.enums import ApprovalAction, AgentStep, TaskState

@dataclass
class WorkflowState:
    """
    Lightweight shared workflow state container inspired by graph-based
    orchestration systems.
    
    Acts as the centralized mutable runtime context shared between the
    orchestrator, tools, and LLM integration layers.
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

    # Multi-step approval tracking
    pending_agent_step: Optional[AgentStep] = None
    pending_step_data: Optional[str] = None

    # Production-grade agent extensions
    reflection_metadata: Optional[dict] = None
    internal_rag_confidence: float = 0.0
    memory_context: Optional[str] = None

    # HITL/Replanning Redesign extensions
    selected_vendors: List[dict] = field(default_factory=list)
    rejected_vendors: List[str] = field(default_factory=list)
    feedback_history: List[str] = field(default_factory=list)
    constraints: Dict[str, Any] = field(default_factory=dict)
    tool_traces: List[dict] = field(default_factory=list)
    current_step: TaskState = TaskState.SEARCHING_VENDORS

    def to_dict(self) -> dict:
        return {
            "prompt": self.prompt,
            "research_data": self.research_data,
            "analysis_summary": self.analysis_summary,
            "selected_vendor": self.selected_vendor,
            "draft": self.draft,
            "improved_draft": self.improved_draft,
            "execution_result": self.execution_result,
            "regeneration_count": self.regeneration_count,
            "rejection_feedback": self.rejection_feedback,
            "approval_action": self.approval_action.value if self.approval_action else None,
            "pending_agent_step": self.pending_agent_step.value if self.pending_agent_step else None,
            "pending_step_data": self.pending_step_data,
            "reflection_metadata": self.reflection_metadata,
            "internal_rag_confidence": self.internal_rag_confidence,
            "memory_context": self.memory_context,
            "selected_vendors": self.selected_vendors,
            "rejected_vendors": self.rejected_vendors,
            "feedback_history": self.feedback_history,
            "constraints": self.constraints,
            "tool_traces": self.tool_traces,
            "current_step": self.current_step.value if self.current_step else None,
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'WorkflowState':
        state = cls(prompt=data.get("prompt", ""))
        state.research_data = data.get("research_data")
        state.analysis_summary = data.get("analysis_summary")
        state.selected_vendor = data.get("selected_vendor")
        state.draft = data.get("draft")
        state.improved_draft = data.get("improved_draft")
        state.execution_result = data.get("execution_result")
        state.regeneration_count = data.get("regeneration_count", 0)
        state.rejection_feedback = data.get("rejection_feedback")
        
        aa = data.get("approval_action")
        state.approval_action = ApprovalAction(aa) if aa else None
        
        pas = data.get("pending_agent_step")
        state.pending_agent_step = AgentStep(pas) if pas else None
        
        state.pending_step_data = data.get("pending_step_data")
        state.reflection_metadata = data.get("reflection_metadata")
        state.internal_rag_confidence = data.get("internal_rag_confidence", 0.0)
        state.memory_context = data.get("memory_context")
        
        state.selected_vendors = data.get("selected_vendors", [])
        state.rejected_vendors = data.get("rejected_vendors", [])
        state.feedback_history = data.get("feedback_history", [])
        state.constraints = data.get("constraints", {})
        state.tool_traces = data.get("tool_traces", [])
        
        cs = data.get("current_step")
        state.current_step = TaskState(cs) if cs else TaskState.SEARCHING_VENDORS
        
        return state

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

    @classmethod
    def from_json(cls, json_str: str) -> 'WorkflowState':
        return cls.from_dict(json.loads(json_str))