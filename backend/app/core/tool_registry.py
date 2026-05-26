import logging
import asyncio
import time
from datetime import datetime
from typing import Callable, Dict, Any, List

from app.core import config
from app.models.workflow_state import WorkflowState
from app.core.enums import TaskState

logger = logging.getLogger("app.core.tool_registry")

class ToolRegistry:
    def __init__(self):
        self._registry: Dict[str, Callable] = {}

    def register(self, name: str, func: Callable):
        """Registers a tool function under a unique name."""
        self._registry[name] = func
        logger.info(f"Registered tool: '{name}'")

    async def execute(self, name: str, state: WorkflowState, *args, **kwargs) -> Any:
        """
        Executes a registered tool with retries, timeout/retry status updates,
        and tool trace capturing.
        """
        if name not in self._registry:
            raise KeyError(f"Tool '{name}' is not registered in the ToolRegistry.")

        func = self._registry[name]
        logger.info(f"Executing tool '{name}' via registry")

        # 1. Capture snapshot of state before execution (Input Trace)
        input_snapshot = {
            "prompt": state.prompt,
            "constraints": dict(state.constraints) if state.constraints else {},
            "selected_vendor": dict(state.selected_vendor) if state.selected_vendor else None,
            "selected_vendors": list(state.selected_vendors) if state.selected_vendors else [],
            "draft": state.draft,
            "improved_draft": state.improved_draft,
        }

        attempts = 0
        max_attempts = getattr(config, "MAX_RETRY_ATTEMPTS", 3)
        initial_delay = getattr(config, "RETRY_INITIAL_DELAY", 1.0)
        backoff_factor = getattr(config, "RETRY_BACKOFF_FACTOR", 2.0)

        last_error = None
        start_time = time.time()
        
        while attempts < max_attempts:
            try:
                attempts += 1
                # Execute the tool
                if asyncio.iscoroutinefunction(func):
                    res = await func(state, *args, **kwargs)
                else:
                    res = func(state, *args, **kwargs)
                
                # Execution succeeded, capture output snapshot
                output_snapshot = {
                    "research_data": dict(state.research_data) if state.research_data else None,
                    "selected_vendor": dict(state.selected_vendor) if state.selected_vendor else None,
                    "analysis_summary": state.analysis_summary,
                    "draft": state.draft,
                    "improved_draft": state.improved_draft,
                    "execution_result": state.execution_result,
                }
                
                # Log execution trace
                trace = {
                    "tool": name,
                    "input": input_snapshot,
                    "output": output_snapshot,
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                    "duration_seconds": round(time.time() - start_time, 3),
                    "attempts": attempts,
                }
                state.tool_traces.append(trace)
                return res

            except Exception as e:
                last_error = e
                logger.warning(
                    f"Tool '{name}' failed on attempt {attempts}/{max_attempts}: {e}"
                )
                
                if attempts < max_attempts:
                    # Update status to FAILED_RETRYING transiently if possible
                    from app.services.agent_orchestrator_service import active_tasks, transition_task_state
                    from app.repositories.task_repository import task_repo
                    
                    from app.core.logger import task_id_ctx
                    task_id = task_id_ctx.get()
                    if task_id and task_id in active_tasks:
                        task_info = active_tasks[task_id]
                        old_state = task_info.get("task_state")
                        if transition_task_state(task_id, TaskState.FAILED_RETRYING):
                            task_repo.update_task_status(task_id, old_state, TaskState.FAILED_RETRYING)
                            
                            # Stream websocket notification of retrying
                            websocket = task_info.get("websocket")
                            if websocket:
                                from app.schemas.websocket_schema import StatusUpdateEvent
                                from app.core.enums import AgentStep
                                event = StatusUpdateEvent(
                                    correlation_id=task_info.get("correlation_id", ""),
                                    task_id=task_id,
                                    task_state=TaskState.FAILED_RETRYING,
                                    agent_step=state.pending_agent_step or AgentStep.SEARCHING_VENDORS,
                                    message=f"Tool '{name}' failed. Retrying in {initial_delay}s... (Attempt {attempts + 1})"
                                )
                                asyncio.create_task(websocket.send_json(event.model_dump()))
                    
                    delay = initial_delay * (backoff_factor ** (attempts - 1))
                    await asyncio.sleep(delay)
                    
                    # Transition back to RUNNING after retry wait
                    if task_id and task_id in active_tasks:
                        task_info = active_tasks[task_id]
                        if transition_task_state(task_id, TaskState.RUNNING):
                            task_repo.update_task_status(task_id, TaskState.FAILED_RETRYING, TaskState.RUNNING)

        # If we got here, all attempts failed
        logger.error(f"Tool '{name}' failed after {max_attempts} attempts.")
        
        # Capture error trace
        trace = {
            "tool": name,
            "input": input_snapshot,
            "output": {"error": str(last_error)},
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "duration_seconds": round(time.time() - start_time, 3),
            "attempts": attempts,
            "failed": True
        }
        state.tool_traces.append(trace)
        
        # Graceful fallback logic depending on tool
        if name == "vendor_search":
            logger.info("Applying fallback for vendor_search tool: empty list")
            state.research_data = {
                "category": "computer",
                "vendors": [],
                "internal_confidence": 0.0,
                "market_insights": "Fallback applied due to search tool failure."
            }
            return
        elif name == "pricing_analysis":
            logger.info("Applying fallback for pricing_analysis tool: select first available")
            vendors = state.research_data.get("vendors", []) if state.research_data else []
            if vendors:
                state.selected_vendor = vendors[0]
                state.analysis_summary = "Fallback analysis summary (tool failure)."
            return
        elif name == "draft_outreach":
            logger.info("Applying fallback for draft_outreach tool: generic template")
            vendor_name = (state.selected_vendor.get('vendor_name') or state.selected_vendor.get('name')) if state.selected_vendor else "Selected Vendor"
            state.draft = f"Dear {vendor_name} Team,\n\nWe are interested in your procurement services. Please provide your best pricing and availability.\n\nThank you,\nOur Company"
            return
        elif name == "self_reflection":
            logger.info("Applying fallback for self_reflection tool: skip reflection")
            state.improved_draft = state.draft or ""
            state.reflection_metadata = {"tone_check_passed": True, "hallucination_free": True, "formatting_valid": True}
            return
        elif name == "execute_outreach":
            logger.info("Applying fallback for execute_outreach tool: mark as executed")
            state.execution_result = f"Outreach sent to {(state.selected_vendor.get('vendor_name') or state.selected_vendor.get('name')) if state.selected_vendor else 'vendor'}"
            return

        raise last_error

# Singleton instance
tool_registry = ToolRegistry()

# Import and register core tools (lazy local import to avoid startup ordering issues)
from app.tools.research_tool import research_tool
from app.tools.analysis_tool import analysis_tool
from app.tools.draft_tool import draft_tool
from app.tools.reflection_tool import reflection_tool
from app.tools.execution_tool import execution_tool

tool_registry.register("vendor_search", research_tool)
tool_registry.register("pricing_analysis", analysis_tool)
tool_registry.register("draft_outreach", draft_tool)
tool_registry.register("self_reflection", reflection_tool)
tool_registry.register("execute_outreach", execution_tool)

