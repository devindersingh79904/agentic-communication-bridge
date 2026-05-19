from pydantic import BaseModel, Field

class EnumMetadataResponse(BaseModel):
    """
    Schema representing the structure of the centralized enum metadata response.
    Exposes valid orchestration values for frontend consumption.
    """
    task_states: list[str] = Field(
        ..., description="List of valid orchestration states (e.g., SCHEDULED, RUNNING)"
    )
    websocket_event_types: list[str] = Field(
        ..., description="List of valid WebSocket communication event types"
    )
    approval_actions: list[str] = Field(
        ..., description="List of valid human-in-the-loop approval actions"
    )
    agent_steps: list[str] = Field(
        ..., description="List of valid agent background progress steps"
    )
