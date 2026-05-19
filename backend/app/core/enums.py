from enum import Enum

class TaskState(str, Enum):
    """
    Represents the orchestration lifecycle state of an agent task.
    Used by the state machine to track and validate transitions and logged for tracing.
    """
    SCHEDULED = "SCHEDULED"
    RUNNING = "RUNNING"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    EXECUTING = "EXECUTING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"

class WebSocketEventType(str, Enum):
    """
    Represents WebSocket communication events between frontend and backend.
    Used as the structured contract for real-time payloads to eliminate magic strings.
    """
    STATUS_UPDATE = "STATUS_UPDATE"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    START_TASK = "START_TASK"
    APPROVED = "APPROVED"
    STOP = "STOP"
    TASK_COMPLETED = "TASK_COMPLETED"
    TASK_CANCELLED = "TASK_CANCELLED"
    ERROR = "ERROR"

class ApprovalAction(str, Enum):
    """
    Represents human-in-the-loop approval actions sent from the frontend.
    Determines if an agent workflow proceeds or is immediately halted.
    """
    APPROVE = "APPROVE"
    STOP = "STOP"

class AgentStep(str, Enum):
    """
    Represents streaming progress updates from the agent workflow.
    Used to inform the client precisely what the agent is currently doing in the background.
    """
    SEARCHING_VENDORS = "SEARCHING_VENDORS"
    ANALYZING_PRICING = "ANALYZING_PRICING"
    DRAFTING_OUTREACH = "DRAFTING_OUTREACH"
    SELF_REFLECTION = "SELF_REFLECTION"
