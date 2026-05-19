from typing import Optional
from pydantic import BaseModel
from app.core.enums import WebSocketEventType, TaskState, AgentStep

class BaseWebSocketEvent(BaseModel):
    """
    Base websocket event carrying tracing and tracking context.
    """
    event_type: WebSocketEventType
    correlation_id: str
    task_id: Optional[str] = None

class StatusUpdateEvent(BaseWebSocketEvent):
    """
    Event sent when the agent updates its execution status.
    """
    event_type: WebSocketEventType = WebSocketEventType.STATUS_UPDATE
    task_state: TaskState
    agent_step: AgentStep
    message: str

class ApprovalRequiredEvent(BaseWebSocketEvent):
    """
    Event sent when the agent requires human intervention/approval.
    """
    event_type: WebSocketEventType = WebSocketEventType.APPROVAL_REQUIRED
    task_state: TaskState
    draft_message: str
    message: str
    approval_timeout_seconds: Optional[int] = None

class TaskCompletedEvent(BaseWebSocketEvent):
    """
    Event sent when the agent task completes successfully.
    """
    event_type: WebSocketEventType = WebSocketEventType.TASK_COMPLETED
    task_state: TaskState
    message: str

class TaskCancelledEvent(BaseWebSocketEvent):
    """
    Event sent when the agent task has been cancelled.
    """
    event_type: WebSocketEventType = WebSocketEventType.TASK_CANCELLED
    task_state: TaskState
    message: str

class ErrorEvent(BaseWebSocketEvent):
    """
    Event sent when a failure or error occurs during processing.
    """
    event_type: WebSocketEventType = WebSocketEventType.ERROR
    task_state: TaskState
    error_code: str
    message: str

class ApproveEvent(BaseWebSocketEvent):
    """
    Event received from client approving the current task block.
    """
    event_type: WebSocketEventType = WebSocketEventType.APPROVED

class StopEvent(BaseWebSocketEvent):
    """
    Event received from client to cancel/stop the execution.
    """
    event_type: WebSocketEventType = WebSocketEventType.STOP

class StartTaskEvent(BaseModel):
    """
    Event received from client to start orchestration with a user prompt.
    """
    event_type: str
    prompt: str

