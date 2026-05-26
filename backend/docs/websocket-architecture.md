# WebSocket Architecture

The backend exposes a single bidirectional WebSocket endpoint for the mobile app:

```text
WS /v1/agent/connect
```

The socket is used for task start/resume, live status streaming, human approvals, cancellation, and heartbeat messages.

## Current Implementation

Primary files:

- `app/websocket/agent_websocket.py`: accepts sockets, validates inbound events, handles reconnect/start/approval/stop.
- `app/websocket/connection_manager.py`: maps `task_id` to live socket connections.
- `app/runtime/workflow_runtime.py`: runs the durable workflow loop and approval gates.
- `app/runtime/event_streamer.py`: builds structured outbound event payloads.
- `app/schemas/websocket_schema.py`: Pydantic models for inbound and outbound event contracts.

Legacy compatibility:

- `app/services/agent_orchestrator_service.py` is a small facade for older imports.
- `app/services/legacy_orchestrator_compat.py` contains the transitional compatibility runner used by legacy tests/tools.

## Connection Lifecycle

1. Client opens `WS /v1/agent/connect`.
2. Server accepts and creates a `correlation_id` from `X-Correlation-ID` or a generated UUID.
3. Server creates a temporary `task_id` and registers the socket.
4. Client sends `START_TASK`.
5. For a new task, the runtime creates a persisted `WorkflowSession`.
6. For a resume request, the server rebinds the socket to an existing session and sends a restore `STATUS_UPDATE`.
7. Runtime streams progress and approval events until completion, cancellation, or disconnect.

## Inbound Events

Inbound messages are validated with `IncomingWebSocketEvent`.

### START_TASK

Start a new task:

```json
{
  "event_type": "START_TASK",
  "prompt": "Find laptop vendors near Bangalore"
}
```

Resume an existing task:

```json
{
  "event_type": "START_TASK",
  "task_id": "existing-task-id"
}
```

### APPROVAL_RESPONSE

Approve the current gate:

```json
{
  "event_type": "APPROVAL_RESPONSE",
  "action": "APPROVE",
  "workflow_version": 2
}
```

Vendor selection approval can include selected vendors:

```json
{
  "event_type": "APPROVAL_RESPONSE",
  "action": "APPROVE",
  "selected_vendors": [
    {
      "vendor_name": "Example Vendor",
      "rating": 4.7
    }
  ],
  "workflow_version": 2
}
```

Reject or request changes:

```json
{
  "event_type": "APPROVAL_RESPONSE",
  "action": "REJECT",
  "feedback": "Show vendors with faster delivery.",
  "workflow_version": 2
}
```

`workflow_version` protects against stale approvals. If older clients omit it, the backend accepts the event for backward compatibility.

### STOP

```json
{
  "event_type": "STOP",
  "workflow_version": 2
}
```

The backend cancels the persisted session, cancels the active runtime task, emits `TASK_CANCELLED` when possible, and removes active task references.

### PING / PONG

The server sends `PING` at `HEARTBEAT_INTERVAL_SECONDS`. The client can respond with `PONG`. Client-initiated `PING` receives a `PONG` response.

## Outbound Events

All outbound events include:

```json
{
  "event_type": "STATUS_UPDATE",
  "correlation_id": "trace-id",
  "task_id": "task-id",
  "timestamp": "2026-05-26T00:00:00Z"
}
```

### STATUS_UPDATE

```json
{
  "event_type": "STATUS_UPDATE",
  "correlation_id": "trace-id",
  "task_id": "task-id",
  "task_state": "SEARCHING_VENDORS",
  "agent_step": "SEARCHING_VENDORS",
  "message": "Executing tool: vendor_search",
  "vendors": []
}
```

### APPROVAL_REQUIRED

```json
{
  "event_type": "APPROVAL_REQUIRED",
  "correlation_id": "trace-id",
  "task_id": "task-id",
  "task_state": "WAITING_FINAL_APPROVAL",
  "agent_step": "SELF_REFLECTION",
  "message": "Self-reflection completed. Approve outreach proposal draft.",
  "draft_message": "Dear vendor team...",
  "approval_timeout_seconds": 300,
  "reflection_metadata": {
    "tone_check_passed": true,
    "confidence_score": 0.85
  }
}
```

### TASK_COMPLETED

```json
{
  "event_type": "TASK_COMPLETED",
  "correlation_id": "trace-id",
  "task_id": "task-id",
  "task_state": "COMPLETED",
  "message": "Procurement outreach simulation succeeded.",
  "final_response": "Dear vendor team..."
}
```

### TASK_CANCELLED

```json
{
  "event_type": "TASK_CANCELLED",
  "correlation_id": "trace-id",
  "task_id": "task-id",
  "task_state": "CANCELLED",
  "message": "Orchestration cancelled by client."
}
```

### ERROR

```json
{
  "event_type": "ERROR",
  "correlation_id": "trace-id",
  "task_id": "task-id",
  "task_state": "FAILED",
  "error_code": "VERSION_CONFLICT",
  "message": "Your client is out of sync. Please reload to restore state."
}
```

## Approval Gates

The active runtime pauses at two human-in-the-loop gates:

```text
WAITING_VENDOR_SELECTION
WAITING_FINAL_APPROVAL
```

`WAITING_PRICE_APPROVAL` remains in the enum for legacy compatibility, but pricing currently auto-proceeds after vendor selection in the active workflow.

Each gate persists the current `WorkflowState`, streams `APPROVAL_REQUIRED`, clears the runtime approval event, and waits up to `WAIT_FOR_HUMAN_TIMEOUT` seconds.

On approval, the runtime persists the approval flag and increments `workflow_version`.

On rejection or modification request, the runtime stores feedback and loops back through the relevant steps. Final draft feedback regenerates only `draft_outreach`, `self_reflection`, and `execute_outreach`; it does not re-run vendor search.

On timeout, the runtime marks the session `CANCELLED`, emits `TASK_CANCELLED`, and closes the socket if still connected.

## Race-Condition Handling

- `workflow_version` rejects stale approvals and stale stop requests when supplied.
- Active tasks are guarded by an async lock in `workflow_runtime.py`.
- Terminal states are treated as final: `COMPLETED`, `FAILED`, and `CANCELLED`.
- `ConnectionManager.send_json()` checks that a socket is still connected before sending.
- Reconnects rebind the current task to a new socket without starting a duplicate runtime.

## Recovery Contract

If the mobile app backgrounds or disconnects, it can restore with:

```text
GET /v1/workflow/{task_id}
```

Then it can reconnect to `WS /v1/agent/connect` and send:

```json
{
  "event_type": "START_TASK",
  "task_id": "existing-task-id"
}
```

The backend sends a restore `STATUS_UPDATE` containing current vendors, selected vendor, pricing analysis, draft message, and reflection metadata when available.
