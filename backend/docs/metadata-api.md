# Metadata Enums API

The `Trybo Agentic Bridge` backend exposes its internal enumerations dynamically via the Metadata API. 

## Purpose
By exposing these enumerations centrally:
- The frontend dynamically renders dropdowns, filters, and state maps.
- We **avoid hardcoding magic strings** in the frontend client.
- When backend enums are expanded in the future, the frontend immediately inherits the changes without requiring client-side updates.

## Endpoint

**`GET /v1/metadata/enums`**

### Usage Recommendation
The frontend client should make a request to this endpoint once on application startup or hydration, and store the resulting lists in its global state (e.g., Redux, Context API, or a Singleton). 

### Sample Response
```json
{
  "success": true,
  "message": "Enum metadata fetched successfully",
  "correlation_id": "8b26a7d1-f2c8-4a89-97a5-37f0cd9e533e",
  "timestamp": "2026-05-19T14:37:23.415954Z",
  "data": {
    "task_states": [
      "SCHEDULED",
      "RUNNING",
      "SEARCHING_VENDORS",
      "EXTERNAL_SEARCHING",
      "ANALYZING_PRICING",
      "DRAFTING_OUTREACH",
      "SELF_REFLECTION",
      "WAITING_FINAL_APPROVAL",
      "COMPLETED",
      "FAILED",
      "CANCELLED",
      "FAILED_RETRYING"
    ],
    "websocket_event_types": [
      "STATUS_UPDATE",
      "APPROVAL_REQUIRED",
      "START_TASK",
      "APPROVAL_RESPONSE",
      "STOP",
      "TASK_COMPLETED",
      "TASK_CANCELLED",
      "ERROR",
      "PING",
      "PONG"
    ],
    "approval_actions": [
      "APPROVE",
      "REJECT"
    ],
    "agent_steps": [
      "SEARCHING_VENDORS",
      "ANALYZING_PRICING",
      "DRAFTING_OUTREACH",
      "SELF_REFLECTION",
      "EXECUTING"
    ]
  }
}
```
