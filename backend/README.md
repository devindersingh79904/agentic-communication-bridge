# Trybo Agentic Bridge Backend

FastAPI backend for the human-in-the-loop procurement agent. It exposes a WebSocket bridge for real-time agent progress, durable workflow state stored in SQLite, and a recovery API for restoring the mobile client after reconnects.

## What This Backend Provides

- `GET /`: health check.
- `WS /v1/agent/connect`: starts, resumes, approves, stops, and streams an agent task.
- `GET /v1/workflow/{task_id}`: returns persisted workflow state for mobile recovery.
- `GET /v1/metadata/*`: helper metadata endpoints for enums and websocket contracts.

## Runtime Architecture

The active orchestration path is:

```text
websocket/agent_websocket.py
  -> runtime/workflow_runtime.py
  -> agents/planner_agent.py
  -> runtime/execution_engine.py
  -> tools/*
  -> storage/workflow_repository.py
```

The durable session status enum is `RuntimeWorkflowState` in `app/models/workflow_models.py`.

`TaskState` in `app/core/enums.py` is the UI-facing state streamed to the mobile client.

`WorkflowState` in `app/models/workflow_state.py` is the serialized tool context kept for compatibility with existing tool implementations.

`app/services/agent_orchestrator_service.py` is intentionally a small compatibility facade. Older tests and tools still import that module, while the large transitional implementation is isolated in `app/services/legacy_orchestrator_compat.py`.

## Human-In-The-Loop Flow

1. Client connects to `WS /v1/agent/connect`.
2. Client sends `START_TASK` with a `prompt`.
3. Runtime creates a `WorkflowSession` in SQLite and plans a DAG of tools.
4. Backend streams `STATUS_UPDATE` events as tools run.
5. Runtime pauses at active approval gates:
   - `WAITING_VENDOR_SELECTION`
   - `WAITING_FINAL_APPROVAL`
6. Pricing analysis is retained as a runtime/tool state but auto-proceeds once a vendor is selected.
7. Client sends `APPROVAL_RESPONSE`.
8. Runtime validates state/version, resumes, or rewrites only the relevant downstream steps on rejection.
9. Client can send `STOP` at any time to cancel the workflow.
10. Backend emits `TASK_COMPLETED`, `TASK_CANCELLED`, or `ERROR`.

Approval waits use `WAIT_FOR_HUMAN_TIMEOUT`. If the timeout expires, the task is cancelled and a cancellation event is streamed.

## Environment

Create `backend/.env`:

```bash
AGENT_PROVIDER=openai
OPENAI_API_KEY=your_key
OPENAI_MODEL=gpt-4.1-mini

DB_PATH=agent_procurement.db
CHROMA_PERSIST_PATH=./chroma_db

HUMAN_IN_LOOP=true
AUTO_APPROVE=false
WAIT_FOR_HUMAN_TIMEOUT=300
AGENT_STEP_DELAY_SECONDS=2

HEARTBEAT_INTERVAL_SECONDS=15
HEARTBEAT_TIMEOUT_SECONDS=30
MAX_RETRY_ATTEMPTS=3
RETRY_INITIAL_DELAY=1.0
RETRY_BACKOFF_FACTOR=2.0
```

For local Ollama:

```bash
AGENT_PROVIDER=ollama
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=qwen2.5-coder:7b
```

## Run

```bash
uv sync
uv run uvicorn app.main:app --reload
```

The backend starts at `http://127.0.0.1:8000`.

## Test

```bash
uv run pytest
```

Current backend verification:

```text
21 passed
```

## Generated Data

`chroma_db/`, SQLite databases, logs, caches, and virtual environments are ignored by `.gitignore`. Chroma data is generated locally from `app/rag/sample_vendors.json` and should not be committed.

## Documentation Map

- `docs/websocket-architecture.md`: WebSocket event contract and interruption behavior.
- `docs/data-flow-deep-dive.md`: Current runtime data flow and persistence lifecycle.
- `docs/rag-architecture.md`: ChromaDB vendor retrieval design.
- `docs/review-fix-log.md`: Senior-review cleanup notes and verification history.
