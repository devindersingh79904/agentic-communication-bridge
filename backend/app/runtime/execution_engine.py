import asyncio
import time
import logging
from typing import Callable, Any, Optional, Dict, List

from app.core import config
from app.core.logger import get_logger
from app.models.workflow_models import WorkflowSession, PlanStep, ToolResult, RuntimeWorkflowState
from app.models.workflow_state import WorkflowState
from app.core.tool_registry import tool_registry
from app.tools.base_tool import BaseTool
from app.storage.workflow_repository import workflow_repo

logger = get_logger("runtime.execution_engine")

class ExecutionEngine:
    async def execute_plan(self, session: WorkflowSession, progress_emitter: Callable[[str, str], Any]) -> None:
        """
        Runs the execution engine loop over the session plan, resolving DAG dependencies,
        handling retries, and logging observability metrics.
        """
        task_id = session.task_id
        session.status = RuntimeWorkflowState.EXECUTING
        workflow_repo.save_session(session)
        
        # Performance Tracking
        workflow_start_time = time.time()
        session.metrics["workflow_start_time"] = workflow_start_time
        session.metrics["tool_latencies"] = session.metrics.get("tool_latencies", {})
        session.metrics["retry_counts"] = session.metrics.get("retry_counts", {})
        
        logger.info(f"ExecutionEngine starting plan for task {task_id}")
        
        # Main execution loop - iterates until all steps are completed, failed, or cancelled
        while True:
            # Refresh session
            session = workflow_repo.get_session(task_id)
            if not session:
                logger.error(f"Task session {task_id} not found in repository.")
                break
                
            if session.status in (RuntimeWorkflowState.CANCELLED, RuntimeWorkflowState.FAILED, RuntimeWorkflowState.COMPLETED):
                logger.info(f"Task {task_id} reached terminal state: {session.status.value}")
                break

            # 1. Dependency Resolution (DAG Check)
            next_step = self._resolve_next_step(session)
            if not next_step:
                all_done = all(step.status == "completed" for step in session.execution_plan.plan)
                any_failed = any(step.status == "failed" for step in session.execution_plan.plan)
                
                if all_done:
                    session.status = RuntimeWorkflowState.COMPLETED
                    workflow_repo.save_session(session)
                    logger.info(f"All steps completed successfully for task {task_id}")
                elif any_failed:
                    session.status = RuntimeWorkflowState.FAILED
                    workflow_repo.save_session(session)
                    logger.error(f"Task {task_id} failed due to step failure.")
                else:
                    logger.info(f"No execution step currently ready for task {task_id}.")
                break
            
            # 2. Execute Step
            step_completed = await self._execute_step(session, next_step, progress_emitter)
            if not step_completed:
                break
                
            await asyncio.sleep(0.1)

        # Log total session execution metrics
        total_duration = round(time.time() - workflow_start_time, 3)
        session.metrics["total_duration_seconds"] = total_duration
        workflow_repo.save_session(session)
        
        logger.info(
            f"[METRIC] Task session={task_id} workflow_duration={total_duration}s "
            f"total_retries={session.retries_count} tool_latencies={session.metrics['tool_latencies']}"
        )

    def _resolve_next_step(self, session: WorkflowSession) -> Optional[PlanStep]:
        """
        Scans steps to find the next step whose depends_on steps are all "completed"
        (or not present in the current plan).
        """
        plan_step_ids = {s.step_id for s in session.execution_plan.plan}
        completed_step_ids = {s.step_id for s in session.execution_plan.plan if s.status == "completed"}
        
        for step in session.execution_plan.plan:
            if step.status == "pending":
                deps_satisfied = all(
                    (dep in completed_step_ids) or (dep not in plan_step_ids)
                    for dep in step.depends_on
                )
                if deps_satisfied:
                    return step
        return None

    async def _execute_step(self, session: WorkflowSession, step: PlanStep, progress_emitter: Callable[[str, str], Any]) -> bool:
        """
        Executes a single step using its tool name. Handles retries with exponential backoff.
        Supports both class-based BaseTool implementations and legacy callable tool functions (and test mocks).
        """
        task_id = session.task_id
        tool_name = step.tool
        
        # Fetch tool from core registry to support mock overrides in conftest
        if not tool_registry.has(tool_name):
            logger.error(f"Tool {tool_name} not found in registry.")
            step.status = "failed"
            workflow_repo.save_session(session)
            return False

        tool = tool_registry.get(tool_name)
        step.status = "running"
        workflow_repo.save_session(session)

        # Notify client of active tool execution
        await progress_emitter(tool_name, f"Executing tool: {tool_name}")
        
        # Defensive metrics initialization to prevent KeyError
        if "tool_latencies" not in session.metrics:
            session.metrics["tool_latencies"] = {}
        if "retry_counts" not in session.metrics:
            session.metrics["retry_counts"] = {}
            
        attempts = 0
        max_attempts = getattr(config, "MAX_RETRY_ATTEMPTS", 3)
        initial_delay = getattr(config, "RETRY_INITIAL_DELAY", 1.0)
        backoff_factor = getattr(config, "RETRY_BACKOFF_FACTOR", 2.0)
        
        tool_start_time = time.time()
        
        while attempts < max_attempts:
            try:
                attempts += 1
                logger.info(f"Running tool {tool_name} for task {task_id} (Attempt {attempts}/{max_attempts})")
                
                # Check if it is a BaseTool instance or a raw callable function (legacy mock)
                if isinstance(tool, BaseTool):
                    async def tool_progress_logger(msg: str):
                        await progress_emitter(tool_name, msg)
                    result: ToolResult = await tool.execute(session, tool_progress_logger)
                else:
                    # Legacy or Mocked tool function call compatibility
                    legacy_state = WorkflowState.from_json(session.workflow_state_json)
                    if asyncio.iscoroutinefunction(tool):
                        await tool(legacy_state)
                    else:
                        tool(legacy_state)
                    # Update session state back
                    session.workflow_state_json = legacy_state.to_json()
                    workflow_repo.save_session(session)
                    result = ToolResult(status="success", confidence=0.85)

                if result.status == "success":
                    step.status = "completed"
                    latency = round(time.time() - tool_start_time, 3)
                    session.metrics["tool_latencies"][tool_name] = latency
                    session.metrics["retry_counts"][tool_name] = attempts - 1
                    
                    logger.info(f"[METRIC] Tool={tool_name} task={task_id} latency={latency}s attempts={attempts}")
                    workflow_repo.save_session(session)
                    return True
                else:
                    raise ValueError(result.error_message or "Tool reported failure status")
                    
            except Exception as e:
                logger.warning(f"Tool {tool_name} failed on attempt {attempts}/{max_attempts}: {e}")
                session.retries_count += 1
                
                if attempts < max_attempts:
                    session.status = RuntimeWorkflowState.PLANNING
                    workflow_repo.save_session(session)
                    
                    await progress_emitter(tool_name, f"Tool '{tool_name}' failed. Retrying... (Attempt {attempts + 1})")
                    
                    delay = initial_delay * (backoff_factor ** (attempts - 1))
                    await asyncio.sleep(delay)
                    
                    session.status = RuntimeWorkflowState.EXECUTING
                    workflow_repo.save_session(session)

        # Failed
        step.status = "failed"
        session.status = RuntimeWorkflowState.FAILED
        workflow_repo.save_session(session)
        logger.error(f"Tool {tool_name} execution failed after {max_attempts} attempts.")
        return False

# Singleton instance
execution_engine = ExecutionEngine()
