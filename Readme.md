# Trybo Agentic Bridge

A premium, production-ready human-in-the-loop AI agentic system. It features a React Native (Expo) mobile interface communicating in real time over WebSockets with a FastAPI backend to coordinate a multi-stage procurement research agent.

## How it Works

The application operates as a real-time state machine:

1. **Client Triggers Agent**: The client connects to the WebSocket endpoint `/v1/agent/connect`.
2. **State Machine Execution**: The backend executes the workflow:
   - `SEARCHING_VENDORS` (Vendor Discovery)
   - `ANALYZING_PRICING` (Price/Offer Analysis)
   - `DRAFTING_OUTREACH` (OpenAI generates the initial outreach message)
   - `SELF_REFLECTION` (OpenAI reviews and improves the generated draft)
3. **Approval Gate (`WAITING_APPROVAL`)**: The backend pauses execution and waits for client consent. A configurable countdown timer runs (e.g., 10 seconds).
4. **Final States**:
   - If the user clicks **Approve & Finalize**, it completes the task (`SUCCESS`).
   - If the user clicks **Stop Run**, it immediately cancels (`CANCELLED`).
   - If the countdown runs out, it automatically cancels (`CANCELLED`).

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
You can execute the automated test client to verify success, cancellation, timeouts, and LLM error bounds:
```bash
cd backend
PYTHONPATH=. uv run python scratch/websocket_test.py
```