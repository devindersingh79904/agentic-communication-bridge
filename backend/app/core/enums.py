from enum import Enum

class TaskState(str, Enum):
    """
    Represents the orchestration lifecycle state of an agent task.
    Used by the state machine to track and validate transitions and logged for tracing.
    """
    SCHEDULED = "SCHEDULED"
    RUNNING = "RUNNING"
    SEARCHING_VENDORS = "SEARCHING_VENDORS"
    EXTERNAL_SEARCHING = "EXTERNAL_SEARCHING"
    ANALYZING_PRICING = "ANALYZING_PRICING"
    DRAFTING_OUTREACH = "DRAFTING_OUTREACH"
    SELF_REFLECTION = "SELF_REFLECTION"
    WAITING_FINAL_APPROVAL = "WAITING_FINAL_APPROVAL"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    FAILED_RETRYING = "FAILED_RETRYING"


class WebSocketEventType(str, Enum):
    """
    Represents WebSocket communication events between frontend and backend.
    Used as the structured contract for real-time payloads to eliminate magic strings.
    """
    STATUS_UPDATE = "STATUS_UPDATE"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    START_TASK = "START_TASK"
    APPROVAL_RESPONSE = "APPROVAL_RESPONSE"
    STOP = "STOP"
    TASK_COMPLETED = "TASK_COMPLETED"
    TASK_CANCELLED = "TASK_CANCELLED"
    ERROR = "ERROR"
    PING = "PING"
    PONG = "PONG"

class ApprovalAction(str, Enum):
    """
    Represents human-in-the-loop approval actions sent from the frontend.
    Determines if an agent workflow proceeds or is immediately halted.
    """
    APPROVE = "APPROVE"
    REJECT = "REJECT"

class AgentStep(str, Enum):
    """
    Represents streaming progress updates from the agent workflow.
    Used to inform the client precisely what the agent is currently doing in the background.
    """
    SEARCHING_VENDORS = "SEARCHING_VENDORS"
    ANALYZING_PRICING = "ANALYZING_PRICING"
    DRAFTING_OUTREACH = "DRAFTING_OUTREACH"
    SELF_REFLECTION = "SELF_REFLECTION"
    EXECUTING = "EXECUTING"
