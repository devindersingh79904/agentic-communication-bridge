# Trybo Agentic Bridge

A premium, production-ready human-in-the-loop AI agentic system. It features a React Native (Expo) mobile interface communicating in real time over WebSockets with a FastAPI backend to coordinate a multi-stage procurement research agent.

## How it Works

The application operates as a real-time state machine where orchestration steps are isolated into lightweight, modular async tools (`app/tools/`), sharing a centralized `WorkflowState` object inspired by graph-based orchestration:

1. **Client Connects**: The client opens a WebSocket to `/v1/agent/connect`. The task is registered in `SCHEDULED` state.
2. **Client Sends `START_TASK`**: The client sends a `START_TASK` event with the user's prompt. The backend creates a `WorkflowState(prompt=...)` and begins orchestration (`SCHEDULED → RUNNING`).
3. **State Machine Execution**: The backend executes the workflow sequentially:
   - `SEARCHING_VENDORS` → Runs `research_tool` (Vendor Discovery)
   - `ANALYZING_PRICING` → Runs `analysis_tool` (Price/Offer Analysis)
   - `DRAFTING_OUTREACH` → Runs `draft_tool` (OpenAI generates outreach using the user's prompt)
   - `SELF_REFLECTION` → Runs `reflection_tool` (OpenAI reviews and improves the draft)
4. **Approval Gate & Iterative Regeneration (`WAITING_APPROVAL`)**: The backend pauses and waits for client consent. 
   - **Approve**: Transitions to `EXECUTING` → `SUCCESS`.
   - **Reject**: Receives rejection feedback, increments the regeneration counter, and loops back to `DRAFTING_OUTREACH` / `SELF_REFLECTION` for refinement.
   - **Stop**: Explicitly cancels execution at any phase (`CANCELLED`).
   - The loop is bounded by `MAX_REGENERATION_ATTEMPTS` to prevent infinite loops.
5. **Execution & Final States** (`WAITING_APPROVAL → EXECUTING → SUCCESS`):
   - If approved, transitions to `EXECUTING`, calls `execution_tool`, then transitions to `SUCCESS`, streaming the final AI response payload.
   - If the user clicks **Stop** or the timeout expires, it cancels (`CANCELLED`).

Each tool reads/writes shared `WorkflowState` fields and pauses for a globally configurable delay (`AGENT_STEP_DELAY_SECONDS`).

---

## Environment Configuration

Create a `.env` file in the `backend/` directory using the following variables:

```bash
# =========================
# System Configuration
# =========================
# Port and server options
PORT=8000
HOST=127.0.0.1

# Human-in-the-loop approval gate timeout window in seconds
APPROVAL_TIMEOUT_SECONDS=10

# =========================
# OpenAI Configuration
# =========================
# Required API Key
OPENAI_API_KEY=your_openai_api_key_here

# Configurable OpenAI model options
OPENAI_MODEL=gpt-4.1-mini

# Configurable temperature parameter for draft determinism
OPENAI_TEMPERATURE=0.3

# =========================
# Agent Workflow Configuration
# =========================
# Configurable delay in seconds for every workflow step
AGENT_STEP_DELAY_SECONDS=2

# =========================
# Regeneration Configuration
# =========================
# Bounded limit for iterative LLM regeneration attempts
MAX_REGENERATION_ATTEMPTS=3
```

---

## Setup Instructions

### Prerequisites
- Node.js (v18+)
- Python (3.12+)
- [uv](https://github.com/astral-sh/uv) (recommended Python package manager)

### 1. Backend Setup
```bash
cd backend
# Synchronize environment & install dependencies
uv sync
```

### 2. Mobile Client Setup
```bash
cd mobile
# Install expo and web rendering support dependencies
npm install
```

---

## Execution Instructions

### 1. Start the Backend Server
```bash
cd backend
uv run uvicorn app.main:app --reload
```
The server will start at `http://127.0.0.1:8000` and the WebSocket endpoint will be accessible at `ws://127.0.0.1:8000/v1/agent/connect`.

### 2. Start the Mobile Client
```bash
cd mobile
npm run web
```
The client bundler will start and serve the application on `http://localhost:8081`. You can view it in your browser or run it on a mobile emulator/physical device using the Expo Go application.

---

## Testing & Verification
You can execute the automated WebSocket integration test to verify the full orchestration flow (connect → `START_TASK` → steps → approval → `EXECUTING` → success):
```bash
cd backend
PYTHONPATH=. uv run python scratch/websocket_test.py
```
Expected output:
```
[1] Connected to WebSocket
[2] Sent START_TASK
  [STATUS_UPDATE] state=RUNNING step=SEARCHING_VENDORS ...
  [STATUS_UPDATE] state=RUNNING step=ANALYZING_PRICING ...
  [STATUS_UPDATE] state=RUNNING step=DRAFTING_OUTREACH ...
  [STATUS_UPDATE] state=RUNNING step=SELF_REFLECTION ...
  [APPROVAL_REQUIRED] state=WAITING_APPROVAL ...
[3] Sent APPROVED
  [TASK_COMPLETED] state=SUCCESS ...

✅ SUCCESS - Full workflow completed!
```