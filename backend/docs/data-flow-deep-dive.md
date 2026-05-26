# Backend Data Flow Deep Dive

This document describes the current durable runtime path for a procurement workflow. The older `agent_orchestrator_service.py` API is now a compatibility facade; active WebSocket execution runs through `app.runtime.workflow_runtime`.

## Runtime Path

```text
mobile websocket client
  -> app/websocket/agent_websocket.py
  -> app/runtime/workflow_runtime.py
  -> app/agents/planner_agent.py
  -> app/runtime/execution_engine.py
  -> app/tools/*
  -> app/storage/workflow_repository.py
```

## State Models

`RuntimeWorkflowState`
: Durable server-side workflow status stored in SQLite. Values include `CREATED`, `PLANNING`, `EXECUTING`, `WAITING_APPROVAL`, `APPROVED`, `REJECTED`, `COMPLETED`, `FAILED`, and `CANCELLED`.

`TaskState`
: UI-facing state streamed to the mobile client. Examples include `SEARCHING_VENDORS`, `WAITING_VENDOR_SELECTION`, `ANALYZING_PRICING`, `WAITING_PRICE_APPROVAL`, `DRAFTING_OUTREACH`, `SELF_REFLECTION`, and `WAITING_FINAL_APPROVAL`.

`WAITING_PRICE_APPROVAL` is retained for compatibility with older tests and clients. In the active runtime, pricing analysis auto-proceeds after vendor selection, so the human approval gates are vendor selection and final outreach approval.

`WorkflowState`
: Serialized tool context. It carries vendor results, pricing analysis, selected vendor, draft text, reflection metadata, approval flags, feedback, and tool traces.

`WorkflowSession`
: Persisted runtime session. It combines the durable status, execution plan, serialized `WorkflowState`, workflow version, retry metrics, event history, and timestamps.

## Chronological Flow

### 1. WebSocket Connection

File: `app/websocket/agent_websocket.py`

The client connects to `WS /v1/agent/connect`. The server accepts the socket, creates a `correlation_id`, creates a temporary `task_id`, registers the socket in `ConnectionManager`, and starts a heartbeat task.

Inbound payloads are validated with `IncomingWebSocketEvent` before dispatch.

### 2. Start Task

Inbound event:

```json
{
  "event_type": "START_TASK",
  "prompt": "Find laptop vendors near Bangalore"
}
```

The endpoint calls:

```python
session = await workflow_runtime.get_or_create_session(task_id, prompt)
approval_event = await workflow_runtime.start_orchestration(task_id, prompt, correlation_id)
```

The repository creates a `tasks` row with `RuntimeWorkflowState.CREATED` and stores the initial serialized `WorkflowState`.

### 3. Planning

File: `app/agents/planner_agent.py`

The runtime classifies the prompt category and creates an `ExecutionPlan`. The fallback DAG is:

```text
vendor_search -> pricing_analysis -> draft_outreach -> self_reflection -> execute_outreach
```

Each `PlanStep` has a `step_id`, `tool`, `reason`, `depends_on`, and `status`.

### 4. Tool Execution

File: `app/runtime/execution_engine.py`

The engine resolves the next runnable step by dependency order, fetches the tool through `tool_registry.get(tool_name)`, and executes it with retry/backoff controls:

```text
MAX_RETRY_ATTEMPTS
RETRY_INITIAL_DELAY
RETRY_BACKOFF_FACTOR
```

Progress is streamed as `STATUS_UPDATE` events through `event_streamer.py`.

### 5. Approval Gate 1: Vendor Selection

Before `pricing_analysis`, the runtime pauses if no vendor has been selected.

Server event:

```json
{
  "event_type": "APPROVAL_REQUIRED",
  "task_state": "WAITING_VENDOR_SELECTION",
  "agent_step": "SEARCHING_VENDORS",
  "vendors": []
}
```

Client response:

```json
{
  "event_type": "APPROVAL_RESPONSE",
  "action": "APPROVE",
  "selected_vendors": [{ "vendor_name": "Example Vendor" }]
}
```

The runtime persists `vendor_selection_approved`, selected vendor state, increments `workflow_version`, and releases the approval event.

### 6. Approval Gate 2: Price Approval

Before `draft_outreach`, the runtime pauses for pricing approval. The approval event includes the pricing summary and recommended vendor. On approval, `price_approval_approved` is persisted and execution resumes.

### 7. Draft And Self-Reflection

The `draft_outreach` step generates the draft message. The evaluator then checks the draft for quality, tone, formatting, and hallucination risk.

The runtime performs at most one automatic correction loop before moving to human review. This matches the assignment requirement for one self-correction step while avoiding infinite evaluator loops.

### 8. Approval Gate 3: Final Outreach Approval

Before `execute_outreach`, the runtime pauses at `WAITING_FINAL_APPROVAL` and sends the final draft plus reflection metadata.

On approval, `final_approval_approved` is persisted and the simulated execution step runs.

### 9. Completion

On success, the runtime:

- Marks the session `COMPLETED`.
- Stores `final_output`.
- Stores lightweight task memory for future planning context.
- Emits `TASK_COMPLETED`.
- Removes active task/event references.

## Cancellation And Timeout

Client `STOP` events are handled in `agent_websocket.py` and routed to `workflow_runtime.cleanup_session(task_id)`.

Approval waits use `WAIT_FOR_HUMAN_TIMEOUT`. On timeout, the runtime marks the session `CANCELLED`, emits `TASK_CANCELLED`, and closes the connection.

Version-aware approval and stop handling use `workflow_version` when the client provides it. Missing versions are accepted for backward compatibility with older clients.

## Persistence

SQLite database path is controlled by `DB_PATH`.

Primary table:

```sql
CREATE TABLE IF NOT EXISTS tasks (
    task_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    user_prompt TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    approval_state TEXT,
    rejection_feedback TEXT,
    final_output TEXT,
    memory TEXT,
    workflow_state_json TEXT,
    workflow_version INTEGER DEFAULT 1,
    execution_plan TEXT,
    tool_outputs TEXT,
    websocket_session_metadata TEXT,
    event_history TEXT,
    retries_count INTEGER DEFAULT 0,
    metrics TEXT
);
```

Transition table:

```sql
CREATE TABLE IF NOT EXISTS task_transitions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    old_state TEXT NOT NULL,
    new_state TEXT NOT NULL,
    transitioned_at TEXT NOT NULL,
    FOREIGN KEY (task_id) REFERENCES tasks (task_id) ON DELETE CASCADE
);
```

## Recovery

The mobile client can call:

```text
GET /v1/workflow/{task_id}
```

The response includes current state, completed steps, pending approval data, vendors, pricing analysis, draft message, reflection metadata, and `workflow_version`.

The client can then reconnect to `WS /v1/agent/connect` and send `START_TASK` with the existing `task_id` to restore the live view.

## Generated Data

ChromaDB data lives under `CHROMA_PERSIST_PATH`, defaulting to `./chroma_db`. This directory is generated locally and ignored by git. Vendor seed data comes from `app/rag/sample_vendors.json`.

## Compatibility Layer

`app/services/agent_orchestrator_service.py` intentionally remains as a small facade for older tests and legacy imports. Its implementation is isolated in `app/services/legacy_orchestrator_compat.py`. New runtime work should target `app/runtime/workflow_runtime.py`.
