from typing import Optional, Dict, Any, List
from datetime import datetime
from pydantic import BaseModel, Field
from app.core.enums import WebSocketEventType, TaskState, AgentStep, ApprovalAction

class BaseWebSocketEvent(BaseModel):
    """
    Base websocket event carrying tracing and tracking context.
    """
    event_type: WebSocketEventType
    correlation_id: str
    task_id: Optional[str] = None
    timestamp: str = Field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")

class StatusUpdateEvent(BaseWebSocketEvent):
    """
    Event sent when the agent updates its execution status.
    """
    event_type: WebSocketEventType = WebSocketEventType.STATUS_UPDATE
    task_state: TaskState
    agent_step: AgentStep
    message: str
    vendors: Optional[List[Dict[str, Any]]] = None
    selected_vendor: Optional[Dict[str, Any]] = None
    pricing_analysis: Optional[Dict[str, Any]] = None

class ApprovalRequiredEvent(BaseWebSocketEvent):
    """
    Event sent when the agent requires human intervention/approval for a specific step.
    """
    event_type: WebSocketEventType = WebSocketEventType.APPROVAL_REQUIRED
    task_state: TaskState
    agent_step: AgentStep
    draft_message: str
    step_data: Optional[str] = None
    message: str
    approval_timeout_seconds: Optional[int] = None
    reflection_metadata: Optional[Dict[str, Any]] = None
    vendors: Optional[List[Dict[str, Any]]] = None
    selected_vendor: Optional[Dict[str, Any]] = None
    pricing_analysis: Optional[Dict[str, Any]] = None

class TaskCompletedEvent(BaseWebSocketEvent):
    """
    Event sent when the agent task completes successfully.
    """
    event_type: WebSocketEventType = WebSocketEventType.TASK_COMPLETED
    task_state: TaskState
    message: str
    final_response: Optional[str] = None
    vendors: Optional[List[Dict[str, Any]]] = None
    selected_vendor: Optional[Dict[str, Any]] = None
    pricing_analysis: Optional[Dict[str, Any]] = None

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

class ApprovalResponseEvent(BaseWebSocketEvent):
    """
    Event received from client with an approval response (APPROVE/REJECT).
    """
    event_type: WebSocketEventType = WebSocketEventType.APPROVAL_RESPONSE
    action: ApprovalAction
    feedback: Optional[str] = None

class StopEvent(BaseWebSocketEvent):
    """
    Event received from client to cancel/stop the execution.
    """
    event_type: WebSocketEventType = WebSocketEventType.STOP

class StartTaskEvent(BaseWebSocketEvent):
    """
    Event received from client to start orchestration with a user prompt.
    """
    event_type: WebSocketEventType = WebSocketEventType.START_TASK
    prompt: str

