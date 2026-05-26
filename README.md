# Trybo Agentic Bridge

A production-style human-in-the-loop AI agentic system. It features a React Native (Expo) mobile interface communicating in real time over WebSockets with a FastAPI backend to coordinate a multi-stage procurement research agent, powered by a custom lightweight durable agentic runtime.

---

## Project Structure

```
├── backend/             # FastAPI backend application, orchestration state machine, and mock test suite
│   ├── app/             # Application source code
│   │   ├── agents/      # Planner and Evaluator agents
│   │   ├── runtime/     # Execution engine and session state loop
│   │   ├── storage/     # SQLite WorkflowRepository session persistence
│   │   ├── tools/       # BaseTool and specialized tool implementations
│   │   ├── models/      # Pydantic schemas for state machine & runtime contracts
│   │   └── websocket/   # WebSocket ConnectionManager and endpoint handlers
│   ├── docs/            # Architecture notes, data-flow guide, websocket contract, review fix log
│   ├── README.md        # Backend-specific runbook
│   └── tests/           # Targeted pytest suite (state transitions, LLM two-pass, orchestrator, websockets)
├── mobile/              # React Native (Expo) mobile application codebase
│   ├── src/             # Mobile screens, WebSocket/API services, and client components
│   └── App.tsx          # Application entry point
└── README.md            # Root documentation and instructions
```

---

## Workflow State Machine Diagram

```
      [START_TASK]
           │
           ▼
     ┌───────────┐
     │  CREATED  │
     └─────┬─────┘
           │
           ▼
     ┌───────────┐
     │ PLANNING  │◄───────────────────────────┐
     └─────┬─────┘                            │
           │                                  │
           ▼                                  │ (User Feedback /
     ┌───────────┐                            │  Evaluator Critique)
     │ EXECUTING │                            │
     └─────┬─────┘                            │
           │                                  │
           ▼                                  │
┌────────────────────────┐                    │
│    WAITING_APPROVAL    ├────────────────────┘
└──────────┬─────────────┘ (Auto-Cancel on Timeout)
           │
      [APPROVE]
           │
           ▼
     ┌───────────┐
     │ APPROVED  │
     └─────┬─────┘
           │
           ▼
     ┌───────────┐
     │ COMPLETED │
     └───────────┘
```

---

## Key Frontend UX Features

The mobile interface is designed as an interactive, conversational copilot:
- **Conversational Chat Feed**: Removes complex tabs. All updates, retrieved supplier cards, comparisons, and audits render inline in a single chat stream.
- **Inline Vendor Selection**: Checking boxes, entering feedback, and submitting selection choices are done directly on the vendor results card in the chat log.
- **Dynamic Progress Checklist**: A lightweight indicator at the top showing the current stage: `Search` -> `Selection` -> `Comparison` -> `Outreach` -> `Approval`.
- **Reasoning Trace Panel**: A collapsible component below the stepper displays high-level planner agent decision reasoning traces in real time.
- **Seamless Session Recovery**: If connection drops, the mobile client queries a REST API endpoint (`GET /v1/workflow/{task_id}`), instantly restores the Zustand store, re-establishes the WebSocket, and resumes the streaming workflow view.

---

## Backend Runtime Summary

The backend uses a durable runtime rather than the older direct orchestrator path:

```text
websocket/agent_websocket.py
  -> runtime/workflow_runtime.py
  -> agents/planner_agent.py
  -> runtime/execution_engine.py
  -> tools/*
  -> storage/workflow_repository.py
```

Runtime status is stored as `RuntimeWorkflowState`. UI-facing progress is streamed as `TaskState`. Tool context is serialized through `WorkflowState`.

The module `backend/app/services/agent_orchestrator_service.py` is intentionally a small compatibility facade for older imports and tests. Transitional logic is isolated in `backend/app/services/legacy_orchestrator_compat.py`; new execution work should target `backend/app/runtime/workflow_runtime.py`.

---

## Architectural Choices & Rationale (Why Not...)

When designing a production-grade agentic runtime, there are several off-the-shelf distributed frameworks available. For the scope of this procurement engineering challenge, a custom lightweight runtime was chosen:

1. **Why Not LangGraph?**
   - *Complexity & Latency*: LangGraph introduces complex graph structures, state replication channels, and compilation overhead. A lightweight State Machine with a Pydantic-based `PlannerAgent` achieves identical DAG routing behavior without the package bloat and training-data drift of complex framework bindings.
2. **Why Not Temporal?**
   - *Dependency Footprint*: Temporal provides excellent durable execution, but requires running external Temporal worker binaries, database schemas, and local server processes. We achieved durable state recovery using SQLite persistence and asyncio session re-binding inside FastAPI.
3. **Why Not Kafka / RabbitMQ?**
   - *Overengineering*: Message queues are ideal for multi-service distributed microservices. Since our agent runtime executes in a unified FastAPI backend process, an in-memory asyncio connection manager mapped to persistent SQLite rows provides sufficient concurrency controls and race-condition safety.
4. **Why Not CrewAI / LangChain?**
   - *Lack of Control*: Frameworks like CrewAI are heavily opinionated, make unnecessary sequential assumptions, and result in highly verbose prompts and token wastage. Direct AsyncOpenAI calls wrapping structured Pydantic schemas allow fine-tuned, prompt-level controls.

---

## Concurrency & Idempotency Controls

To ensure safety and correctness during concurrent operations or network retries, the system implements:
- **Workflow Version Guard**: Every state change increments a `workflow_version` in the database. Client operations must submit the matching version; stale actions are rejected.
- **Action Idempotency**: Modifying requests (`APPROVAL_RESPONSE`, `STOP`) contain a unique `action_id`.
  - *Prototype Note*: For simplicity, `action_id` idempotency is tracked using an in-memory registry. A production-ready implementation should persist this in the database (e.g., using a Redis cache or relational database table with unique constraints).

---

## Environment Configuration

### Backend Configuration

Create a `.env` file in the `backend/` directory:

```bash
# =========================
# System Configuration
# =========================
PORT=8000
HOST=127.0.0.1
DB_PATH=agent_procurement.db

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
# Options: "openai" or "ollama"
AGENT_PROVIDER=openai

# =========================
# OpenAI Configuration
# =========================
OPENAI_API_KEY=your_openai_api_key_here
OPENAI_MODEL=gpt-4.1-mini
OPENAI_TEMPERATURE=0.3
REFLECTION_TEMPERATURE=0.6

# =========================
# Ollama Configuration
# =========================
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=qwen2.5-coder:7b

# =========================
# Agent Workflow & HITL Configuration
# =========================
AGENT_STEP_DELAY_SECONDS=2
HUMAN_IN_LOOP=true
AUTO_APPROVE=false
WAIT_FOR_HUMAN_TIMEOUT=300
ENABLE_SELF_REFLECTION=true
ENABLE_EXTERNAL_VENDOR_SEARCH=true
MAX_REGENERATION_ATTEMPTS=3

# =========================
# External Tools Integration
# =========================
TAVILY_API_KEY=your_tavily_key
CHROMA_PERSIST_PATH=./chroma_db

# =========================
# Concurrency & Retry Settings
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
EXPO_PUBLIC_WS_BASE_URL=ws://localhost:8000
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
# Synchronize environment and install dependencies
uv sync
```

### 2. Mobile Client Setup
```bash
cd mobile
npm install
```

---

## Execution Instructions

### 1. Start the Backend Server
```bash
cd backend
uv run uvicorn app.main:app --reload
```
The server will start at `http://127.0.0.1:8000`. The restoration API will be served at `http://127.0.0.1:8000/v1/workflow/{task_id}`.

### 2. Start the Mobile Client
```bash
cd mobile
npm run web
```
The client bundler will start and serve the application on `http://localhost:8081`.

---

## Verification & Testing

To run the automated tests verifying state machine transitions, retry backoffs, and DAG plan resolution:
```bash
cd backend
uv run pytest
```

Current backend verification:

```text
27 passed
```

---

## Documentation Map

- `backend/README.md`: Backend-specific setup, runtime flow, environment, and testing runbook.
- `backend/docs/websocket-architecture.md`: WebSocket event contract, approval gates, stop handling, and reconnect flow.
- `backend/docs/data-flow-deep-dive.md`: Durable runtime execution path and persistence lifecycle.
- `backend/docs/rag-architecture.md`: ChromaDB-backed vendor retrieval design.
- `backend/docs/review-fix-log.md`: Senior-review cleanup history and verification notes.

---

## Future Improvements

To scale this prototype into an enterprise orchestration platform, the following extensibility paths are planned:
- **Selective Parallel Execution**: Upgrading the execution engine's scheduler to run independent graph step branches concurrently (e.g., executing multiple search API calls in parallel).
- **Distributed Queue Backend**: Moving the background task scheduler out of FastAPI's local event loop and into a worker manager like Celery or Dramatiq backed by Redis.
- **Semantic Vector Memory**: Replacing the raw SQL text history query with a local ChromaDB collection querying task history semantic similarity embeddings.
- **Temporal Integration**: Replacing the custom asyncio-based durable loops with Temporal Workflows for state machine event tracking.
- **Advanced Planner Heuristics**: Implementing reasoning feedback trees (Tree of Thoughts) to select optimal tools.
