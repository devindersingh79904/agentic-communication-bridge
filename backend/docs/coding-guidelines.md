# Trybo Agentic Bridge - Coding Guidelines

These strict, scalable guidelines define the engineering standards for the Trybo Agentic Bridge backend. The architecture is optimized for collaborative engineering and AI-assisted development, specifically tailored for an async human-in-the-loop agent orchestration system.

## 1. Project Philosophy
- **Clean Architecture:** Keep dependencies pointing inward. Frameworks and external interfaces must not dictate business rules.
- **Scalability:** Design components that gracefully handle concurrent connections, AI API latency, and orchestration load.
- **Maintainability:** Write code for the next engineer. Prioritize readability and predictable structures.
- **Async-First Design:** Maximize concurrency and throughput by designing I/O operations asynchronously from the ground up.
- **Simplicity Over Overengineering:** Avoid premature abstractions. Build what is needed now with clear paths for future extension.

## 2. Layer Responsibilities
Strictly enforce boundaries between architectural layers:
- **API (`app/api/`)**: Controllers must remain thin. Strictly handles request parsing, routing, and response formatting.
- **WebSocket (`app/websocket/`)**: Handles communication, event broadcasting, and connections only. Handlers must not contain business logic.
- **Config (`app/config/`)**: Environment loading, settings, and application startup configuration (alongside `main.py`).
- **Services (`app/services/`)**: The core of the application. **All business logic belongs exclusively here.** Avoid "fat services"—split orchestration helpers/services when a service grows too large (e.g., beyond a 1500-line `agent_service.py`).
- **Repositories (`app/repositories/`)**: Abstract data access and persistence (SQLite). **Must never contain business logic.**
- **Models (`app/models/`)**: Database entities and ORM mappings representing the data layer.
- **Schemas (`app/schemas/`)**: Pydantic DTOs for data validation and serialization across layer boundaries.
- **Utils (`app/utils/`)**: Reusable helper functions (e.g., parsers, generic formatters).
- **Core (`app/core/`)**: Shared infrastructure, task registry, enums, state machine helpers, constants, and shared app-wide components.

## 3. Coding Principles
- **DRY (Don't Repeat Yourself):** Extract repeated logic into reusable services or utils.
- **SOLID Principles:** Adhere to structural best practices to ensure loosely coupled components.
- **Single Responsibility Principle (SRP):** Each function, class, and module should have exactly one reason to change.
- **Separation of Concerns:** Keep routing, business rules, and data access decoupled at all times.
- **Readable/Self-Documenting Code:** Choose clear, descriptive names. Code should explain *what* it does without relying heavily on inline comments.
- **Avoid Premature Optimization:** Optimize only when bottlenecks are identified via data and profiling.

## 4. Async & Concurrency Rules
- **Async-First Codebase:** Default to `async def` for any I/O-bound operations (DB, API calls).
- **Never Block the Event Loop:** Do not use blocking operations (e.g., `time.sleep`, synchronous HTTP requests) inside async functions.
- **Avoid Sync I/O in Async Functions:** Offload unavoidable synchronous tasks to thread pools (e.g., `asyncio.to_thread` or `run_in_threadpool`).
- **Proper asyncio Task Cleanup:** Always manage the lifecycle of spawned tasks. Prevent orphaned routines.
- **Handle `asyncio.CancelledError` Correctly:** Ensure proper cleanup logic executes if a task is cancelled.
- **Background Tasks Must Be Tracked:** Background orchestration tasks must be tracked to prevent silent failures, zombie tasks, or memory leaks.
- **Concurrency-Safe State Transitions:** Protect shared state and transitions (e.g., utilizing `asyncio.Lock` for agent states) to prevent race conditions.
- **State Transition Validation:** All state transitions must be validated through allowed transition rules. This is critical for avoiding race conditions (e.g., STOP vs SUCCESS conflicts).

## 5. WebSocket Rules
- **Structured WebSocket Event Payloads:** All messages must follow a strict, predefined JSON schema.
- **Enums/Constants for Message Types:** Define all event types in central enums; no raw strings.
- **Graceful Disconnect Handling:** Ensure connections are cleanly closed, and related resources are immediately released upon client disconnect.
- **No Business Logic in Handlers:** Route incoming payload events directly to the appropriate Service layer functions.
- **Proper Cancellation Handling:** Cleanly cancel active agent orchestration loops if a dependent WebSocket connection drops.

## 6. Constants & Enums
- **Prefer Enums Over Literals:** Always use Enums instead of raw strings, especially in WebSocket and event systems where magic strings quickly become messy and error-prone.
- **Avoid Duplicated Magic Strings:** Centralize repeated literal values to prevent silent bugs.
- **Use Enums/Constants for:**
  - Task and agent states
  - WebSocket event types
  - Standard error codes
  - Configuration keys

## 7. Error Handling
- **Centralized Exception Handling:** Handle expected errors via FastAPI global exception handlers in the `core` layer.
- **Structured Error Responses:** Always return errors in a consistent JSON format.
- **No Silent Exception Swallowing:** Never use `except Exception: pass`. Always log or handle the exception explicitly.
- **Meaningful Logs:** Include contextual data (IDs, current state) in exception logs for actionable debugging.

## 8. Logging Rules
- **Mandatory Tracing IDs:** `correlation_id`, `task_id`, or `session_id` are mandatory in orchestration logs. This is critical for async tracing.
- **Request/Response Logging:** Log incoming API requests and outgoing responses (excluding sensitive payloads).
- **WebSocket Lifecycle Logging:** Log connection establishment, unexpected drops, and structured core events.
- **Task State Transition Logging:** Explicitly log when orchestration states change (e.g., `PENDING` -> `RUNNING`).
- **Cancellation Logging:** Log when a task or connection is actively cancelled.
- **Avoid Logging Secrets/API Keys:** Actively scrub logs for OpenAI keys, tokens, or PII.

## 9. Type Safety
- **Mandatory Type Hints:** All functions, arguments, and return types must be fully annotated.
- **Use Pydantic Schemas:** Validate all incoming data and serialize outgoing data using Pydantic DTOs.
- **Clear DTO Naming:** Distinguish schemas explicitly (e.g., `UserCreateRequest`, `UserResponse`).

## 10. Environment & Security Rules
- **No Hardcoded Secrets:** Never commit API keys, passwords, or tokens to the codebase.
- **All Config from Environment Variables:** Manage configurations centrally using Pydantic settings.
- **Use `.env` Files Locally:** Manage local development variables safely in an environment file.
- **Keep `.env` Out of Git:** Ensure `.env` is always explicitly ignored in `.gitignore`.

## 11. File & Naming Conventions
- **snake_case:** Use for files, directories, variables, and function names (e.g., `agent_service.py`).
- **PascalCase:** Use for classes, TypeVars, and Pydantic models (e.g., `WebSocketManager`).
- **Lowercase Folder Naming:** All module directories must be strictly lowercase.
- **Hyphen-Separated Markdown Docs:** Use kebab-case for documentation files (e.g., `coding-guidelines.md`).

## 12. Documentation Rules
- **Keep Docs Small and Focused:** Address specific topics per document to improve searchability.
- **Avoid Giant Architecture Documents:** Break complex systems down into digestible, modular guides.
- **Location:** Keep all documentation inside the `/backend/docs` directory.
- **Optimize for AI Retrieval/Context Efficiency:** Use clear headers, bullet points, and concise language to maximize token efficiency for LLMs.

## 13. Development Guidelines
- **Commit Frequently:** Keep version control history granular and logical.
- **Keep PR-Sized Changes:** Make small, easily reviewable pull requests.
- **Test Incrementally:** Write and run tests iteratively alongside feature development.
- **Build Orchestration Before UI Polish:** Focus strictly on robust backend mechanics (agents, state management, websockets) before polishing frontend clients or views.

## 14. Import & Code Cleanup Rules

1. **No Unused Imports**
   - Every file must contain only actively used imports.
   - Remove unused imports immediately during development.
   - Unused imports are strictly prohibited in committed code.
   - Avoid bloated import sections.

2. **Import Organization**
   - Group imports in this order:
     - Python standard library
     - Third-party libraries
     - Local application imports
   - Separate groups with a single blank line.
   - Avoid wildcard imports (`from x import *`).

3. **Import Clarity**
   - Prefer explicit imports over ambiguous imports.
   - Import only what is needed.
   - Avoid deeply nested inline imports unless solving circular dependencies or performance concerns.

4. **Circular Dependency Prevention**
   - Maintain clean architecture boundaries to avoid circular imports.
   - Shared logic should move into `app/utils`, `app/core`, or dedicated services instead of creating bidirectional dependencies.

5. **End-of-Implementation Cleanup**
   - Before every commit or PR:
     - Remove unused imports
     - Remove dead code
     - Remove commented-out code
     - Remove debug print statements
     - Remove temporary logs
     - Ensure formatting consistency

6. **Logging Cleanup Rules**
   - Do not leave temporary debugging logs in production code.
   - Keep only meaningful operational logs.

7. **AI-Assisted Development Hygiene**
   - When generating code with AI:
     - Always verify imports.
     - Remove hallucinated imports.
     - Remove unused helper functions.
     - Ensure generated dependencies are actually required.

8. **Maintainability Standards**
   - Keep files clean and minimal.
   - Avoid "import dumping".
   - Keep dependencies intentional and traceable.
