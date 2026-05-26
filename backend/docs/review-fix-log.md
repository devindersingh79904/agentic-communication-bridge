# Senior Review Fix Log

## 2026-05-26

### Runtime structure
- Moved transitional orchestrator compatibility code into `app.services.legacy_orchestrator_compat`.
- Kept `app.services.agent_orchestrator_service` as a small facade so older tests/tools still import the same names.
- Extracted websocket event payload construction into `app.runtime.event_streamer`.

### Naming clarity
- Introduced `RuntimeWorkflowState` for durable runtime session status.
- Kept `CoreWorkflowState` as a backward-compatible alias only.
- Runtime, websocket, storage, and compatibility modules now use `RuntimeWorkflowState`.

### WebSocket contract
- Added `IncomingWebSocketEvent` for typed client-to-server payload validation.
- Preserved backward compatibility for clients that omit `workflow_version`.
- Centralized pricing/status/approval/completed/cancelled payload builders.

### HITL correctness
- Persisted approval gate flags in `WorkflowState`.
- Reloaded the persisted session after approval gates to avoid stale in-memory overwrites.
- Limited evaluator auto-correction to one retry before human review, matching the assignment.

### Repository hygiene
- Added `chroma_db/` to `backend/.gitignore` so generated vector-store data stays local.
- Added `app.utils.time.utc_now_iso()` and replaced `datetime.utcnow()` calls that produced test warnings.

### Verification
- `python -m compileall backend/app`
- `uv run pytest`
- Result: 17 backend tests passed.

### Documentation alignment
- Added a backend-specific README covering endpoints, runtime flow, environment, tests, and generated data.
- Rewrote the data-flow deep dive around the current durable runtime path.
- Rewrote the WebSocket architecture document around typed inbound events, three approval gates, stop handling, reconnects, and workflow versions.
- Updated root README with the backend runtime summary and documentation map.
