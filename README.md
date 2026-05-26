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

The mobile interface is designed as an interactive, conversational copilot rather than an enterprise dashboard:
- **Conversational Chat Feed**: Removes complex tabs and logs. All updates, retrieved supplier cards, comparisons, and audits render inline as cards in a single chat stream.
- **Inline Vendor Selection**: Checking boxes, entering feedback, and submitting selection choices are done directly on the vendor results card in the chat log.
- **Dynamic Progress Checklist**: A lightweight indicator at the top showing the current stage: `Search` -> `Selection` -> `Comparison` -> `Outreach` -> `Approval`.
- **Inline final approval**: Outreach drafts, tone checks, hallucination checks, layout audit checks, and critique comments display in a comprehensive verification card with inline Approve/Reject actions.
- **Dynamic Replanning**: Submitting rejection feedback instructs the agent planner to update constraints, avoid previously rejected vendors (filtered out via RAG database query enrichment), and rerun stages dynamically.

---

## How it Works

The application operates as a real-time, event-driven state machine guided by an LLM-based planner/decision engine and executed via a structured `ToolRegistry`. The state machine has the following states (`SCHEDULED`, `RUNNING`, `SEARCHING_VENDORS`, `WAITING_VENDOR_SELECTION`, `ANALYZING_PRICING`, `WAITING_PRICE_APPROVAL`, `DRAFTING_OUTREACH`, `SELF_REFLECTION`, `WAITING_FINAL_APPROVAL`, `COMPLETED`, `FAILED`, `FAILED_RETRYING`, `CANCELLED`):

1. **Client Connects**: The client opens a WebSocket to `/v1/agent/connect`. The task is initialized in `SCHEDULED` state.
2. **Client Sends `START_TASK`**: The client sends a `START_TASK` event containing a user prompt. If the event payload includes a previously saved `task_id`, the system automatically restores the serialized `WorkflowState` from the SQLite database and resumes the task from its last active state.
3. **LLM Decision Loop**: At each step of execution, the orchestrator queries the LLM Planner (`app/services/agent_planner.py`), which inspects the prompt, constraints, history, and feedback, and decides the next action (e.g. run a tool, complete execution, or wait for human input).
4. **Tool Registry & Execution Traces**: All logic steps are executed via the `ToolRegistry` abstraction. It logs detailed input/output JSON traces for every execution in `WorkflowState.tool_traces` and wraps them in retry logic (exponential backoff) with graceful degraded fallbacks.
5. **Three Human-in-the-Loop (HITL) Gates**:
   - **Gate 1 — Vendor Selection (`WAITING_VENDOR_SELECTION`)**: After vendor search completes, the system pauses. The user is presented with retrieved vendor cards.
     - **Approve**: User can approve with optional feedback (e.g., "use ByteEdge Systems"). The backend uses LLM-based semantic extraction to identify vendor preferences from feedback.
     - **Reject with feedback**: If rejected, the planner re-runs vendor search with updated constraints.
   - **Gate 2 — Price Approval (`WAITING_PRICE_APPROVAL`)**: After pricing analysis completes, the system pauses for approval.
     - If approved, the agent proceeds to draft outreach.
     - If rejected, the agent can re-analyze or re-search based on feedback.
   - **Gate 3 — Final Outreach Approval (`WAITING_FINAL_APPROVAL`)**: Pauses after self-reflection critique. Shows the final outreach draft, tone check, hallucination-free verification, formatting validation, and critique comments.
     - If approved, the agent executes outreach and transitions to `COMPLETED`.
     - If rejected with feedback, the planner reroutes the task back to `DRAFTING_OUTREACH` to regenerate.
6. **Execution & Completion**: When final approval is given, the agent executes the outreach, updates the database, sends a `TASK_COMPLETED` payload, and gracefully terminates the session.

Workflow state is persisted directly to SQLite via `workflow_state_json` on every state transition, ensuring zero context loss on connection drops.

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
# Human-in-the-Loop & Autonomy Configuration
# =========================
# Toggle whether the agent pauses for human decisions (default: true)
HUMAN_IN_LOOP=true

# Automatically approve steps without waiting for human feedback (default: false)
AUTO_APPROVE=false

# Human response timeout window in seconds (default: 300)
WAIT_FOR_HUMAN_TIMEOUT=300

# Enable/disable self-reflection check (default: true)
ENABLE_SELF_REFLECTION=true

# Enable/disable Tavily external web search if database confidence is low (default: true)
ENABLE_EXTERNAL_VENDOR_SEARCH=true

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
   - **Full Approval Flow**: Connects, approves all 3 HITL gates (vendor selection, price approval, final approval), completes successfully.
   - **Rejection & Regeneration Loop**: Rejects at vendor selection, re-runs search, then approves all remaining steps.
   - **Max Regeneration Attempts**: Rejects vendor selection repeatedly, then approves and completes to verify bounded retry behavior.
   - **Approval Timeout Cancellation**: Pauses and automatically cancels after timeout expires.
   - **Stop / Task Interrupt Flow**: Cancels mid-execution.
   - **STOP Spam / Idempotency**: Simulates multiple concurrent STOP events to verify only a single cancellation is executed.
2. **WebSocket Connection & Lifecycle (`tests/test_websocket.py`)**:
   - **Real WebSocket Integration**: Verifies full 3-gate approval flow over the real WebSocket endpoint via FastAPI `TestClient`.
   - **Unexpected Disconnect Cleanup**: Validates that when a client websocket disconnects unexpectedly, the orchestration task is cancelled and cleanly removed from the active tasks registry, preventing orphan memory leaks.
3. **State Transitions (`tests/test_state_transitions.py`)**:
   - Validates all allowed state machine transitions (e.g. `SCHEDULED` → `RUNNING` → `WAITING_VENDOR_SELECTION` → `RUNNING` → `WAITING_PRICE_APPROVAL` → `COMPLETED`).
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
   - State transition guard: `transition_task_state(task_id, TaskState.CANCELLED)` validates the transition against the `VALID_TRANSITIONS` state machine. If the task is already in a terminal state (`COMPLETED`, `FAILED`, `CANCELLED`), the transition is rejected and no further action is taken.
   - Sets the `cancelled` flag on the task registry entry.
   - Cancels the `asyncio.Task` via `task.cancel()`, which raises `asyncio.CancelledError` inside the running orchestration coroutine.
   - Awaits `asyncio.gather(task, return_exceptions=True)` to ensure the coroutine has fully exited before cleanup proceeds.

### Race Condition Handling

| Scenario | Behavior |
|---|---|
| STOP during `SEARCHING_VENDORS` / `ANALYZING_PRICING` | `asyncio.CancelledError` propagates through `asyncio.sleep()` in the tool. Caught in orchestrator, `TASK_CANCELLED` emitted. |
| STOP during `DRAFTING_OUTREACH` (active LLM call) | `asyncio.CancelledError` interrupts the `await client.chat.completions.create()` call. Caught and cleaned up. |
| STOP during `WAITING_FINAL_APPROVAL` (approval timeout) | `asyncio.CancelledError` interrupts `asyncio.wait_for(approval_event.wait())`. Caught and cleaned up. |
| STOP at exact moment of `COMPLETED` | `transition_task_state()` rejects `COMPLETED → CANCELLED` because `COMPLETED` is a terminal state with no outgoing transitions. The STOP is safely ignored. |
| STOP after timeout cancellation | `transition_task_state()` rejects `CANCELLED → CANCELLED`. Idempotent — no duplicate events emitted. |

### Terminal Event Deduplication

`send_terminal_event()` uses a `terminal_emitted` flag to ensure that `COMPLETED`, `FAILED`, or `CANCELLED` events are emitted **exactly once**, even under concurrent cancellation and completion races. After emitting, the WebSocket connection is closed server-side.

---

## Libraries & Technology Choices

### Backend

| Library | Purpose |
|---|---|
| **FastAPI** | Async-first web framework with native WebSocket support, automatic OpenAPI docs, and Pydantic integration |
| **Pydantic** | Schema validation and serialization for all WebSocket event payloads and API responses |
| **OpenAI SDK** (`openai`) | Async client for GPT-4.1-mini integration — used for draft generation and self-reflection |
| **chromadb** | Vector database for storing and querying vendor profiles using semantic search embeddings |
| **python-dotenv** | Loads `.env` files for local development configuration without modifying system environment |
| **uvicorn** | ASGI server for running the FastAPI application with hot-reload during development |
| **websockets** | Used in the integration test script for programmatic WebSocket client testing |

### Mobile Client

| Library | Purpose |
|---|---|
| **React Native (Expo)** | Cross-platform mobile framework — enables web, iOS, and Android from a single codebase |
| **Zustand** | Lightweight, hook-based state management with no boilerplate — chosen over Redux for simplicity and direct WebSocket integration |
| **TypeScript** | End-to-end type safety matching the backend Pydantic schemas |

---

## Vector Database Seeding & Mock Data (ChromaDB)

The internal vendor directory is indexed using ChromaDB for semantic search capabilities.

- **Storage Location**: By default, data is persisted locally at the directory specified by `CHROMA_PERSIST_PATH` (defaults to `./chroma_db`).
- **Mock Vendors File**: The source database records are kept in [sample_vendors.json](file:///Users/dsp/development/assignment/backend/app/rag/sample_vendors.json). It contains exactly **40 mock vendors** (10 vendors per category: `computer`, `transport`, `food`, and `stationery`).
- **Seeding Logic**: On first query execution, if the database is either empty or contains the old sample size (12 items), the system resets the database and seeds it with the full list of 40 vendors from `sample_vendors.json`. Each vendor is embedded using their formatted name, location, rating, delivery times, items list, and description.