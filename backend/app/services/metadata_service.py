from app.core.enums import TaskState, WebSocketEventType, ApprovalAction, AgentStep
from app.schemas.metadata_schema import EnumMetadataResponse

def get_all_enums_metadata() -> EnumMetadataResponse:
    """
    Aggregates all backend enums into a structured response.
    Converts enum values to frontend-safe string lists.
    """
    return EnumMetadataResponse(
        task_states=[state.value for state in TaskState],
        websocket_event_types=[event.value for event in WebSocketEventType],
        approval_actions=[action.value for action in ApprovalAction],
        agent_steps=[step.value for step in AgentStep]
    )
