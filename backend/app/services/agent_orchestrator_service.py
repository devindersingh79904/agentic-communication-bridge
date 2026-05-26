"""
Backward-compatible facade for the pre-runtime orchestrator API.

The durable implementation now lives in app.runtime.workflow_runtime.  A few
tests and legacy tools still import these names directly, so this module keeps
that surface stable while isolating transitional compatibility code in one
clearly named place.
"""

from app.services.legacy_orchestrator_compat import (  # noqa: F401
    active_tasks,
    cancel_task,
    cleanup_task,
    handle_approval_response,
    is_websocket_active,
    register_task,
    run_orchestration,
    set_task_reference,
    task_repo,
    transition_task_state,
)
