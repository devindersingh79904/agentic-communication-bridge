Trybo Engineering Challenge: The Agentic Communication Bridge
The Goal
Build a functional prototype of a human-in-the-loop agentic system. You will create a mobile interface that triggers a backend "Agent" to perform a research task, stream its progress, and wait for your approval before "executing" the final action.

Time Limit: 24 Hours. Focus: Backend/Frontend connectivity, Real-time state, and Error handling.

1. Technical Requirements
A. Backend (Python/FastAPI)
WebSocket Orchestrator: Create a WebSocket endpoint /v1/agent/connect.
The Streaming Agent: Simulate a "Research Agent" that sends a sequence of status updates to the mobile app (e.g., "Searching for vendors...", "Analyzing pricing...", "Drafting outreach...").
The Consent Gate: The backend must pause after drafting and wait for an APPROVED message from the mobile client over the socket before sending a "Task Success" final state.
Interrupt Handling: If the user sends a STOP signal via the app, the backend must immediately terminate the process and return a "Cancelled" status.
B. Mobile Client (React Native)
State Management: Use Zustand or TanStack Query to track the agent’s progress and current status.
Streaming UI: Build a chat-style interface that displays the agent's progress updates in real-time.
Optimistic UI: When the task is triggered, the UI should immediately show a "Scheduled" state in a sidebar or task list, reflecting a pending status in the database schema.
Lifecycle Management: Ensure that if the user backgrounds the app or navigates away, the socket is properly managed or cleaned up to prevent leaks.
C. AI Integration
The Draft Tool: Use any LLM (OpenAI, Gemini, or a mock wrapper) to generate the final message text based on the task description.
Self-Reflection: Have the agent perform one "self-correction" step (e.g., the LLM checks its own draft for tone) before it asks for user approval.
2. Evaluation Metrics
We aren't looking for a perfect UI; we are looking for technical depth:

Backend "Figure-it-out" Ability: How well did you handle the state machine on the FastAPI server (handling pauses and approvals)?

Attention to Detail: Did you handle race conditions? What happens if the user hits "Stop" at the exact moment the agent finishes?

Independence: Can you prioritize building the core "Bridge" over superficial styling?

AI Leverage: How effectively did you use LLM tools to build or enhance the feature?

3. Submission Instructions
Repository: Provide a link to a private GitHub repo.
Structure: * /backend: FastAPI code.
/mobile: React Native (Expo) code.
Documentation: A brief README.md explaining:
How to run both services.
How you handled the "Interrupt" logic.
Any libraries you chose to speed up development (e.g., react-native-gifted-chat or shadcn/ui equivalents).
Good luck, Asher. We are looking for engineers who can build for the future of agentic workflows.