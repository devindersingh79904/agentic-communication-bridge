# Trybo Agentic Bridge

A production-style human-in-the-loop AI agentic system. It features a React Native (Expo) mobile interface communicating in real time over WebSockets with a FastAPI backend to coordinate a multi-stage procurement research agent.

---

## Project Structure

```
├── backend/             # FastAPI backend application, orchestration state machine, and mock test suite
│   ├── app/             # Application source code (models, websocket endpoints, and agent tools)
│   └── tests/           # Targeted pytest suite (state transitions, LLM two-pass, orchestrator, websockets)
├── mobile/              # React Native (Expo) mobile application codebase
│   ├── src/             # Mobile screens, WebSocket/API services, and client components
│   └── App.tsx          # Application entry point
└── README.md            # Root documentation and instructions
```

---

## Key Frontend UX Features

The mobile interface is designed to maximize human-in-the-loop (HITL) visibility and responsiveness:
- **Approval & Rejection Timeline**: A visual progression showing real-time agent status updates alongside any historical feedback and regeneration attempts.
- **Regeneration Visibility**: Visual feedback indicating when the agent is revising drafts based on user input, tracking regeneration limits.
- **Optimistic Cancellation**: Instant feedback when the user stops the task, preventing extra network spam.
- **Approval Timeout Countdown**: A clear visual timer counting down the consent gate window, making it obvious when the agent will automatically cancel.

---

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
APPROVAL_TIMEOUT_SECONDS=60

# =========================
# Localized Procurement Context
# =========================
DEFAULT_CITY=Bangalore
DEFAULT_LOCALITY=Marathahalli
DEFAULT_PINCODE=560037

# =========================
# User Identity Configuration
# =========================
DEFAULT_USER_NAME=Devinder Singh
DEFAULT_COMPANY_NAME=DSP Technologies

# =========================
# Agent Provider Selection
# =========================
# Set to "openai" to use OpenAI, or "ollama" to use a local Ollama instance
AGENT_PROVIDER=openai

# =========================
# OpenAI Configuration
# =========================
# Required API Key
OPENAI_API_KEY=your_openai_api_key_here

# Configurable OpenAI model options
OPENAI_MODEL=gpt-4.1-mini

# Configurable temperature parameter for draft determinism
OPENAI_TEMPERATURE=0.3

# Temperature parameter for self-reflection refinement
REFLECTION_TEMPERATURE=0.6

# =========================
# Ollama Configuration
# =========================
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=qwen2.5-coder:7b

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

# =========================
# Database & RAG Configuration
# =========================
DB_PATH=agent_procurement.db
CHROMA_PERSIST_PATH=./chroma_db
TAVILY_API_KEY=your_tavily_key
SERPAPI_API_KEY=your_serpapi_key

# =========================
# Heartbeat & Retry Policies
# =========================
HEARTBEAT_INTERVAL_SECONDS=15
HEARTBEAT_TIMEOUT_SECONDS=30
MAX_RETRY_ATTEMPTS=3
RETRY_INITIAL_DELAY=1.0
RETRY_BACKOFF_FACTOR=2.0
```

### Mobile Configuration

Create a `.env` file in the `mobile/` directory:

```bash
# WebSocket base URL for real-time agent communication
EXPO_PUBLIC_WS_BASE_URL=ws://localhost:8000

# HTTP base URL for REST API calls (metadata enums, etc.)
EXPO_PUBLIC_API_BASE_URL=http://localhost:8000
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

The project includes a targeted, production-style test suite built on `pytest` and `pytest-asyncio` with network-independent OpenAI mocks, allowing anyone to run tests without network dependencies, API keys, or token costs.

### Running Tests
To run the automated test suite, execute:
```bash
cd backend
uv run pytest
```

### Covered Test Categories
1. **Orchestrator Workflows (`tests/test_orchestrator.py`)**:
   - **Full Approval Flow**: Connects, receives updates, approves draft, completes successfully.
   - **Rejection & Regeneration Loop**: Rejects draft, regenerates outreach, approves next draft, completes.
   - **Max Regeneration Attempts**: Rejects repeatedly to verify hard-limit error code (`MAX_REGENERATION_EXCEEDED`).
   - **Approval Timeout Cancellation**: Pauses and automatically cancels after timeout expires.
   - **Stop / Task Interrupt Flow**: Cancels mid-execution.
   - **STOP Spam / Idempotency**: Simulates multiple concurrent STOP events to verify only a single cancellation is executed.
2. **WebSocket Connection & Lifecycle (`tests/test_websocket.py`)**:
   - **Real WebSocket Integration**: Verifies websocket endpoint interaction via FastAPI `TestClient`.
   - **Unexpected Disconnect Cleanup**: Validates that when a client websocket disconnects unexpectedly, the orchestration task is cancelled and cleanly removed from the active tasks registry, preventing orphan memory leaks.
3. **State Transitions (`tests/test_state_transitions.py`)**:
   - Validates all allowed state machine transitions (e.g. `SCHEDULED` -> `RUNNING` -> `WAITING_APPROVAL` -> `EXECUTING` -> `SUCCESS`).
   - Verifies blocking/prevention of invalid transitions (e.g. terminal state changes).
4. **LLM Integration & Refinement (`tests/test_llm_service.py`)**:
   - **Two-Pass LLM Refinement**: Validates the evaluation-then-rewrite pipeline.
   - **Sanitization & Regex**: Verifies regex-based removal of LLM critiques and markdown block artifacts.
   - **Length Truncation**: Ensures output is bounded to `MAX_OUTPUT_LENGTH`.

---

## How Interrupt/Stop Logic Works

The system is designed to handle `STOP` signals safely at any point in the orchestration lifecycle, including during active LLM calls, mid-approval-timeout, and at the exact moment of completion.

### Architecture

The interrupt mechanism is built on three layers:

1. **Client Layer** — The mobile client sends a `STOP` WebSocket event and immediately closes the socket. Optimistic state is set to `CANCELLED` to prevent repeated STOP spam.

2. **WebSocket Handler Layer** (`app/websocket/agent_websocket.py`) — Receives the `STOP` event and delegates to `cancel_task(task_id)`. Additionally, the `finally` block in the handler guarantees cleanup even on unexpected disconnects.

3. **Orchestrator Layer** (`app/services/agent_orchestrator_service.py`) — `cancel_task()` performs:
   - State transition guard: `transition_task_state(task_id, TaskState.CANCELLED)` validates the transition against the `VALID_TRANSITIONS` state machine. If the task is already in a terminal state (`SUCCESS`, `FAILED`, `CANCELLED`), the transition is rejected and no further action is taken.
   - Sets the `cancelled` flag on the task registry entry.
   - Cancels the `asyncio.Task` via `task.cancel()`, which raises `asyncio.CancelledError` inside the running orchestration coroutine.
   - Awaits `asyncio.gather(task, return_exceptions=True)` to ensure the coroutine has fully exited before cleanup proceeds.

### Race Condition Handling

| Scenario | Behavior |
|---|---|
| STOP during `SEARCHING_VENDORS` / `ANALYZING_PRICING` | `asyncio.CancelledError` propagates through `asyncio.sleep()` in the tool. Caught in orchestrator, `TASK_CANCELLED` emitted. |
| STOP during `DRAFTING_OUTREACH` (active LLM call) | `asyncio.CancelledError` interrupts the `await client.chat.completions.create()` call. Caught and cleaned up. |
| STOP during `WAITING_APPROVAL` (approval timeout) | `asyncio.CancelledError` interrupts `asyncio.wait_for(approval_event.wait())`. Caught and cleaned up. |
| STOP at exact moment of `SUCCESS` | `transition_task_state()` rejects `SUCCESS → CANCELLED` because `SUCCESS` is a terminal state with no outgoing transitions. The STOP is safely ignored. |
| STOP after timeout cancellation | `transition_task_state()` rejects `CANCELLED → CANCELLED`. Idempotent — no duplicate events emitted. |

### Terminal Event Deduplication

`send_terminal_event()` uses a `terminal_emitted` flag to ensure that `SUCCESS`, `FAILED`, or `CANCELLED` events are emitted **exactly once**, even under concurrent cancellation and completion races. After emitting, the WebSocket connection is closed server-side.

---

## Libraries & Technology Choices

### Backend

| Library | Purpose |
|---|---|
| **FastAPI** | Async-first web framework with native WebSocket support, automatic OpenAPI docs, and Pydantic integration |
| **Pydantic** | Schema validation and serialization for all WebSocket event payloads and API responses |
| **OpenAI SDK** (`openai`) | Async client for GPT-4.1-mini integration — used for draft generation and self-reflection |
| **python-dotenv** | Loads `.env` files for local development configuration without modifying system environment |
| **uvicorn** | ASGI server for running the FastAPI application with hot-reload during development |
| **websockets** | Used in the integration test script for programmatic WebSocket client testing |

### Mobile Client

| Library | Purpose |
|---|---|
| **React Native (Expo)** | Cross-platform mobile framework — enables web, iOS, and Android from a single codebase |
| **Zustand** | Lightweight, hook-based state management with no boilerplate — chosen over Redux for simplicity and direct WebSocket integration |
| **TypeScript** | End-to-end type safety matching the backend Pydantic schemas |