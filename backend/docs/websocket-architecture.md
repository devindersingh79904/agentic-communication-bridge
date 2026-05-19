# Trybo Agentic Bridge - WebSocket Architecture

This document describes the design and contract of the real-time WebSocket communication layer between the frontend and the backend.

## Endpoint
- **URL**: `/v1/agent/connect`
- **Protocol**: WS / WSS (WebSocket)

---

## 1. Design & Communication Model

### Event-Driven Communication
The system utilizes a structured, bidirectional event-driven paradigm where:
1. **Outbound (Server-to-Client)**: The backend streams granular task states, agent logs, errors, and human-in-the-loop interactive requests.
2. **Inbound (Client-to-Server)**: The frontend sends control directives (e.g. Approve/Stop inputs) back to influence execution.

### Why WebSockets Instead of Polling?
- **Real-Time Responsiveness**: Agent execution steps (such as drafting outreach and analyzing vendor responses) need to stream instantly to the user interface.
- **Low Latency & Overhead**: Reusing a single TCP connection avoids the continuous overhead of repeatedly establishing HTTP handshakes required by polling.
- **Bi-directional Stream**: Simplifies the orchestration flow where the backend can request approval and receive a client response on the exact same channel without coordinating polling status checks.

---

## 2. Request Tracing & Correlation

To maintain clear async-safe execution traces, every connection is associated with a `correlation_id` and a `task_id`:
- During connection, the server checks the client's handshake headers for `X-Correlation-ID`.
- If missing, the server generates a new `UUID4` correlation ID.
- The server also generates a unique `UUID4` `task_id` for the session.
- These IDs are propagated using Python `contextvars` for all operations in the active WebSocket session and are automatically injected into all centralized log statements.
- Context tokens are captured upon propagation and reset in `finally` blocks in both the connection handler and background tasks to prevent context leakage across asynchronous tasks.

---

## 3. Communication Flow & State Machine

```mermaid
stateDiagram-v2
    [*] --> SCHEDULED
    SCHEDULED --> RUNNING : start background task
    state RUNNING {
        [*] --> SEARCHING_VENDORS
        SEARCHING_VENDORS --> ANALYZING_PRICING
        ANALYZING_PRICING --> DRAFTING_OUTREACH
        DRAFTING_OUTREACH --> SELF_REFLECTION
    }
    RUNNING --> WAITING_APPROVAL : pause at gate
    WAITING_APPROVAL --> SUCCESS : APPROVED received
    WAITING_APPROVAL --> CANCELLED : STOP received / timeout
    RUNNING --> CANCELLED : STOP received
    RUNNING --> FAILED : unexpected error
    SUCCESS --> [*]
    CANCELLED --> [*]
    FAILED --> [*]
```

```mermaid
sequenceDiagram
    participant Client as Frontend Client
    participant Server as Agentic Bridge Backend
    
    Client->>Server: Connect to /v1/agent/connect (Optionally with X-Correlation-ID)
    Server-->>Client: Accept Connection
    Note over Server: Generates task_id & sets contextvars
    
    rect rgb(240, 248, 255)
        Note over Server, Client: Standard Message Loop (RUNNING)
        Server->>Client: Send StatusUpdateEvent (STATUS_UPDATE: SEARCHING_VENDORS)
        Server->>Client: Send StatusUpdateEvent (STATUS_UPDATE: ANALYZING_PRICING)
        Server->>Client: Send StatusUpdateEvent (STATUS_UPDATE: DRAFTING_OUTREACH)
        Server->>Client: Send StatusUpdateEvent (STATUS_UPDATE: SELF_REFLECTION)
    end

    rect rgb(255, 240, 245)
        Note over Server, Client: Human-in-the-loop Interaction (WAITING_APPROVAL)
        Server->>Client: Send ApprovalRequiredEvent (APPROVAL_REQUIRED)
        Note over Server: Wait with APPROVAL_TIMEOUT_SECONDS timeout
        alt APPROVED message received
            Client->>Server: Send ApproveEvent (APPROVED)
            Server->>Client: Send TaskCompletedEvent (TASK_COMPLETED: SUCCESS)
        else STOP message received
            Client->>Server: Send StopEvent (STOP)
            Server->>Client: Send TaskCancelledEvent (TASK_CANCELLED: CANCELLED)
        else Timeout exceeded
            Note over Server: Timeout triggered
            Server->>Client: Send TaskCancelledEvent (TASK_CANCELLED: CANCELLED)
        end
    end
```

---

## 4. Orchestration & Coordination Strategies

### Active Task Registry
To coordinate async execution without databases or persistent message brokers, the backend maintains an in-memory dictionary registry `active_tasks` mapping `task_id -> task_metadata`:
```python
active_tasks[task_id] = {
    "websocket": websocket,
    "task": asyncio_task_reference,
    "approval_event": asyncio.Event(),
    "task_state": TaskState,
    "cancelled": bool
}
```
This is cleaned up immediately upon completion, cancellation, or connection drops to prevent memory leaks.

### Human-in-the-Loop Approval Gate
The orchestration workflow uses Python's `asyncio.Event` (`approval_event`) to pause workflow execution:
- When entering `WAITING_APPROVAL` state, the orchestrator calls `await approval_event.wait()`.
- If an `APPROVED` payload arrives in the websocket loop, `approval_event.set()` is called, resuming execution.

### Approval Timeout Strategy
To prevent hanging tasks, the orchestrator wraps approval waiting in `asyncio.wait_for(...)` using a configurable timeout defined by the `APPROVAL_TIMEOUT_SECONDS` environment variable (default: `10` seconds):
- If no response is received in this window, `asyncio.TimeoutError` is raised.
- The backend catches it, logs it at `WARNING` level, transitions task state to `CANCELLED`, issues a `TaskCancelledEvent` to the client, and cleans up task resources.

### STOP Interruption Flow
If a `STOP` event is received (or a client disconnect is caught), the orchestrator triggers immediate task interruption:
- Marks the task state as `CANCELLED` and `cancelled = True`.
- Invokes `.cancel()` on the task's `asyncio.Task` reference.
- This raises `asyncio.CancelledError` inside the background runner coroutine, allowing the task to gracefully perform final event emission and connection cleanup without orphan routines.
- Checks `websocket.client_state == WebSocketState.CONNECTED` to avoid sending to a closed websocket.

---

## 5. Sample Payloads

All WebSocket event payloads derive from `BaseWebSocketEvent` containing the trace context (`event_type`, `correlation_id`, and `task_id`).

### Outbound Events (Server-to-Client)

#### Status Update
```json
{
  "event_type": "STATUS_UPDATE",
  "correlation_id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
  "task_id": "8fa16de3-d144-482d-83b9-a29bc0192d29",
  "task_state": "RUNNING",
  "agent_step": "SEARCHING_VENDORS",
  "message": "Searching for vendors..."
}
```

#### Approval Required
```json
{
  "event_type": "APPROVAL_REQUIRED",
  "correlation_id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
  "task_id": "8fa16de3-d144-482d-83b9-a29bc0192d29",
  "task_state": "WAITING_APPROVAL",
  "draft_message": "Hello vendor, we would like to discuss pricing...",
  "message": "Draft generated. Awaiting user approval."
}
```

#### Task Completed
```json
{
  "event_type": "TASK_COMPLETED",
  "correlation_id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
  "task_id": "8fa16de3-d144-482d-83b9-a29bc0192d29",
  "task_state": "SUCCESS",
  "message": "Task successfully executed. Outreach finalized."
}
```

#### Task Cancelled
```json
{
  "event_type": "TASK_CANCELLED",
  "correlation_id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
  "task_id": "8fa16de3-d144-482d-83b9-a29bc0192d29",
  "task_state": "CANCELLED",
  "message": "Orchestration cancelled by client."
}
```

### Inbound Events (Client-to-Server)

#### Approve
```json
{
  "event_type": "APPROVED",
  "correlation_id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
  "task_id": "8fa16de3-d144-482d-83b9-a29bc0192d29"
}
```

#### Stop
```json
{
  "event_type": "STOP",
  "correlation_id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
  "task_id": "8fa16de3-d144-482d-83b9-a29bc0192d29"
}
```

---

## 6. LLM Integration Flow

The backend incorporates real-time OpenAI Chat Completion to generate and improve vendor outreach drafts dynamically within the orchestration loop.

### Steps
1. **DRAFTING_OUTREACH**: Calls `generate_outreach_draft()` targeting the OpenAI API.
   - **System Prompt**: `"You are a professional procurement assistant. Generate extremely concise vendor outreach messages."`
   - **User Prompt**: `"Generate a concise professional outreach message requesting vendor pricing discussion."`
2. **SELF_REFLECTION**: Calls `self_reflect_draft(draft)` targeting the OpenAI API.
   - **System Prompt**: `"You are reviewing an outreach message for professionalism, tone, and clarity. Keep it concise."`
   - **User Prompt**: `"Improve this outreach draft while keeping it concise and professional:\n\n{draft}"`
3. **WAITING_APPROVAL**: Sends the refined draft message to the client via `ApprovalRequiredEvent`.

### Error Boundaries & Handling
- If OpenAI API raises an exception (e.g. invalid API key, network timeout), the orchestrator catches it, logs the error, sends a WebSocket `ErrorEvent` with `error_code="LLM_GENERATION_FAILED"`, and gracefully aborts execution.
- No retry mechanisms are implemented to maintain simple, predictable execution timing.

### Token Optimization
- Requests specify `max_tokens=150` to restrict generation length, maintain low latency, and optimize token usage.

### Configuration
- `OPENAI_API_KEY`: Fetched from the environment. Validated at startup; the server fails fast if this variable is missing or empty.
- `OPENAI_MODEL`: Configurable via the environment (defaults to `gpt-4.1-mini`).
- `OPENAI_TEMPERATURE`: Configurable parameter (defaults to `0.3`) allowing runtime tuning of LLM response creativity/determinism. Lower values ensure consistent, professional draft messages and minimize randomness in demonstration flows.

---

## 7. Lightweight Tool Architecture

Instead of adopting heavy orchestration frameworks (like LangGraph or LangChain), the orchestrator steps are refactored into isolated, modular async functions under `app/tools/`:
- **`research_tool`**: Simulates vendor discovery and returns mock structured data.
- **`analysis_tool`**: Simulates pricing and vendor analysis.
- **`draft_tool`**: Integrates OpenAI's Chat Completion to generate the initial outreach draft.
- **`reflection_tool`**: Performs self-reflection on the draft using the LLM.
- **`execution_tool`**: Executes the outreach after the approval gate is satisfied.

### Characteristics:
- **Modular Async Flow**: Every tool is written as a lightweight, independent async function that isolates its logic and handles its own execution tracing logs.
- **Configurable Delays**: Simulates latency using `await asyncio.sleep(config.AGENT_STEP_DELAY_SECONDS)`, which is controlled globally via the `AGENT_STEP_DELAY_SECONDS` environment variable (defaults to `2` seconds).
- **Tracability**: Centralized log events are triggered at the start and completion of each tool, propagating `correlation_id` and `task_id` dynamically.


