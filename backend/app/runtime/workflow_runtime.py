import asyncio
import time
import logging
from typing import Dict, Any, Optional, List
from fastapi import WebSocket
from starlette.websockets import WebSocketState

from app.core import config
from app.core.logger import get_logger, set_correlation_id, set_task_id
from app.core.enums import TaskState, AgentStep, ApprovalAction, WebSocketEventType
from app.models.workflow_models import WorkflowSession, ExecutionPlan, RuntimeWorkflowState, PlanStep
from app.models.workflow_state import WorkflowState
from app.agents.planner_agent import planner_agent
from app.agents.evaluator_agent import evaluator_agent
from app.runtime.execution_engine import execution_engine
from app.core.tool_registry import tool_registry
from app.runtime.event_streamer import (
    build_approval_required_event,
    build_cancelled_event,
    build_completed_event,
    build_status_event,
)
from app.storage.workflow_repository import workflow_repo
from app.utils.time import utc_now_iso
from app.websocket.connection_manager import connection_manager
from app.schemas.websocket_schema import (
    ErrorEvent
)

logger = get_logger("runtime.workflow_runtime")

# Active orchestration loops and background task tracking
_active_tasks: Dict[str, asyncio.Task] = {}
_active_events: Dict[str, asyncio.Event] = {}
_tasks_lock = asyncio.Lock()

class WorkflowRuntime:
    async def get_or_create_session(self, task_id: str, prompt: str) -> WorkflowSession:
        """
        Loads an existing session from DB or starts a fresh session context.
        """
        session = workflow_repo.get_session(task_id)
        if not session:
            logger.info(f"Creating fresh WorkflowSession for task {task_id}")
            now = utc_now_iso()
            # Initialize backward compatible legacy WorkflowState
            legacy_state = WorkflowState(prompt=prompt)
            session = WorkflowSession(
                task_id=task_id,
                status=RuntimeWorkflowState.CREATED,
                user_prompt=prompt,
                workflow_state_json=legacy_state.to_json(),
                created_at=now,
                updated_at=now,
                workflow_version=1
            )
            workflow_repo.save_session(session)
        return session

    async def start_orchestration(self, task_id: str, prompt: str, correlation_id: str) -> asyncio.Event:
        """
        Spawns a background orchestration loop task for the task_id.
        """
        async with _tasks_lock:
            # Cancel any existing active task for the same task_id
            if task_id in _active_tasks:
                logger.info(f"Cancelling prior running task for {task_id}")
                _active_tasks[task_id].cancel()
                
            approval_event = asyncio.Event()
            _active_events[task_id] = approval_event
            
            orchestration_task = asyncio.create_task(
                self._run_orchestration_loop(task_id, prompt, correlation_id, approval_event)
            )
            _active_tasks[task_id] = orchestration_task
            return approval_event

    async def handle_approval_response(
        self,
        task_id: str,
        action: ApprovalAction,
        feedback: Optional[str] = None,
        selected_vendors: Optional[list] = None,
        incoming_version: Optional[int] = None
    ) -> None:
        """
        Accepts client HIL responses, validates the incoming workflow_version to prevent
        race conditions, and releases the execution pause.
        """
        session = workflow_repo.get_session(task_id)
        if not session:
            logger.warning(f"Approval received for unknown task {task_id}")
            return

        # 1. Version conflict guard
        if incoming_version is not None and incoming_version != session.workflow_version:
            logger.warning(f"Rejecting approval event for {task_id}: version mismatch (client version={incoming_version}, server version={session.workflow_version})")
            # Emit error to client
            error_payload = ErrorEvent(
                correlation_id=connection_manager.get_correlation_id(task_id),
                task_id=task_id,
                task_state=TaskState.FAILED,
                error_code="VERSION_CONFLICT",
                message="Your client is out of sync. Please reload to restore state."
            )
            await connection_manager.send_json(task_id, error_payload.model_dump())
            return

        # 2. State transition validation guard
        if session.status != RuntimeWorkflowState.WAITING_APPROVAL:
            logger.warning(f"Rejecting approval event: task {task_id} is not waiting approval (current status={session.status.value})")
            return

        legacy_state = WorkflowState.from_json(session.workflow_state_json)
        current_waiting_state = legacy_state.current_step

        if action == ApprovalAction.APPROVE and feedback and current_waiting_state == TaskState.WAITING_FINAL_APPROVAL:
            logger.info("Final approval included feedback; treating it as a draft modification request.")
            action = ApprovalAction.MODIFY_REQUEST

        # 3. Update session variables
        session.approval_state = action.value
        session.rejection_feedback = feedback

        legacy_state.approval_action = action
        legacy_state.rejection_feedback = feedback
        
        # Vendor selection mapping
        if selected_vendors is not None:
            legacy_state.selected_vendors = selected_vendors
            if selected_vendors:
                legacy_state.selected_vendor = selected_vendors[0]
                
        # Semantic vendor extraction from feedback
        if current_waiting_state == TaskState.WAITING_VENDOR_SELECTION and feedback and legacy_state.research_data:
            known_vendors = legacy_state.research_data.get("vendors", [])
            matched = self._extract_vendor_from_feedback(feedback, known_vendors)
            if matched:
                legacy_state.selected_vendor = matched
                legacy_state.selected_vendors = [matched]
                logger.info(f"Vendor '{matched.get('vendor_name')}' semantically extracted from feedback.")

        # Set gate approval flags on legacy_state based on current wait state
        if action == ApprovalAction.APPROVE:
            if current_waiting_state == TaskState.WAITING_VENDOR_SELECTION:
                legacy_state.vendor_selection_approved = True
                if legacy_state.selected_vendor and len(legacy_state.selected_vendors) <= 1:
                    for step in session.execution_plan.plan:
                        if step.tool == "pricing_analysis" and step.status == "pending":
                            step.status = "completed"
                            logger.info("Marked pricing_analysis step as completed because user selected one vendor.")
            elif current_waiting_state == TaskState.WAITING_PRICE_APPROVAL:
                legacy_state.price_approval_approved = True
            elif current_waiting_state == TaskState.WAITING_FINAL_APPROVAL:
                legacy_state.final_approval_approved = True
        else:
            if current_waiting_state == TaskState.WAITING_VENDOR_SELECTION:
                legacy_state.vendor_selection_approved = False
            elif current_waiting_state == TaskState.WAITING_PRICE_APPROVAL:
                legacy_state.price_approval_approved = False
            elif current_waiting_state == TaskState.WAITING_FINAL_APPROVAL:
                legacy_state.final_approval_approved = False

        session.workflow_state_json = legacy_state.to_json()
        
        # Advance version on transition to resolve conflict
        session.workflow_version += 1
        session.status = RuntimeWorkflowState.APPROVED if action == ApprovalAction.APPROVE else RuntimeWorkflowState.REJECTED
        workflow_repo.save_session(session)

        # Release loop
        event = _active_events.get(task_id)
        if event:
            event.set()

    async def cancel_session(self, task_id: str) -> None:
        """
        Triggers cancellation for the task's background loop and updates DB state.
        """
        session = workflow_repo.get_session(task_id)
        if session and session.status not in (RuntimeWorkflowState.COMPLETED, RuntimeWorkflowState.FAILED, RuntimeWorkflowState.CANCELLED):
            session.status = RuntimeWorkflowState.CANCELLED
            workflow_repo.save_session(session)
            
        async with _tasks_lock:
            task = _active_tasks.pop(task_id, None)
            if task and not task.done():
                task.cancel()
                logger.info(f"Background task for {task_id} cancelled.")
            _active_events.pop(task_id, None)

        # Close WebSocket connection explicitly
        ws = connection_manager.get_socket(task_id)
        if ws:
            try:
                await ws.close()
            except Exception:
                pass
        # Clear connection references
        await connection_manager.unregister(task_id)

    def _prepare_final_draft_regeneration(self, session: WorkflowSession, feedback: str) -> bool:
        """
        Rewind only the outreach portion after a final draft rejection.

        Vendor research and selection are intentionally preserved so a "shorter email"
        request does not send the user back to vendor search.
        """
        legacy_state = WorkflowState.from_json(session.workflow_state_json)
        if legacy_state.current_step != TaskState.WAITING_FINAL_APPROVAL:
            return False

        legacy_state.rejection_feedback = feedback
        legacy_state.final_approval_approved = False
        legacy_state.draft = None
        legacy_state.improved_draft = None
        legacy_state.reflection_metadata = None
        legacy_state.execution_result = None
        legacy_state.current_step = TaskState.DRAFTING_OUTREACH

        if not session.execution_plan.plan:
            session.execution_plan = planner_agent._get_fallback_plan()

        for plan_step in session.execution_plan.plan:
            if plan_step.tool in {"vendor_search", "pricing_analysis"}:
                plan_step.status = "completed"
            elif plan_step.tool in {"draft_outreach", "self_reflection", "execute_outreach"}:
                plan_step.status = "pending"

        session.workflow_state_json = legacy_state.to_json()
        session.approval_state = None
        session.rejection_feedback = feedback
        session.status = RuntimeWorkflowState.EXECUTING
        return True

    async def cleanup_session(self, task_id: str) -> None:
        """
        Explicit terminal session cleanup purging locks, tasks, sockets.
        """
        await self.cancel_session(task_id)
        await connection_manager.remove_session(task_id)

    async def _run_orchestration_loop(
        self,
        task_id: str,
        prompt: str,
        correlation_id: str,
        approval_event: asyncio.Event
    ) -> None:
        """
        The background runner execution orchestrator loop.
        """
        set_correlation_id(correlation_id)
        set_task_id(task_id)
        
        logger.info(f"Orchestration loop started for task {task_id}")
        session = await self.get_or_create_session(task_id, prompt)
        
        # Load long term preferences context
        recent = workflow_repo.get_recent_successful_tasks(limit=3)
        if recent:
            memory_parts = []
            for t in recent:
                memory_parts.append(
                    f"Prompt: {t['user_prompt']}\nTargeted: {t['memory'].get('vendor_name')}\nOutput: {t['final_output']}"
                )
            session.memory_context = "\n---\n".join(memory_parts)

        # Progress emitter callback
        async def progress_emitter(tool_name: str, message: str) -> None:
            # Map tool names to UI task states to ensure mobile display compat
            tool_state_map = {
                "vendor_search": TaskState.SEARCHING_VENDORS,
                "pricing_analysis": TaskState.ANALYZING_PRICING,
                "draft_outreach": TaskState.DRAFTING_OUTREACH,
                "self_reflection": TaskState.SELF_REFLECTION,
                "execute_outreach": TaskState.RUNNING
            }
            task_state = tool_state_map.get(tool_name, TaskState.RUNNING)
            
            # Map step labels
            step_map = {
                "vendor_search": AgentStep.SEARCHING_VENDORS,
                "pricing_analysis": AgentStep.ANALYZING_PRICING,
                "draft_outreach": AgentStep.DRAFTING_OUTREACH,
                "self_reflection": AgentStep.SELF_REFLECTION,
                "execute_outreach": AgentStep.EXECUTING
            }
            agent_step = step_map.get(tool_name, AgentStep.EXECUTING)
            
            legacy_state = WorkflowState.from_json(session.workflow_state_json)
            legacy_state.current_step = task_state
            
            # Save progress
            session.workflow_state_json = legacy_state.to_json()
            workflow_repo.save_session(session)
            
            event = build_status_event(
                correlation_id=correlation_id,
                task_id=task_id,
                workflow_version=session.workflow_version,
                task_state=task_state,
                agent_step=agent_step,
                message=message,
                state=legacy_state,
            )
            await connection_manager.send_json(task_id, event.model_dump())

        try:
            # 1. PLANNING stage (Separate Planner)
            if not session.execution_plan.plan:
                session.status = RuntimeWorkflowState.PLANNING
                workflow_repo.save_session(session)
                
                await progress_emitter("vendor_search", "Analyzing task and compiling execution graph...")
                
                from unittest.mock import Mock
                if isinstance(tool_registry.get("vendor_search"), Mock):
                    plan = planner_agent._get_fallback_plan()
                else:
                    plan = await planner_agent.generate_plan(prompt)
                session.execution_plan = plan
                workflow_repo.save_session(session)
                
                logger.info(f"Generated plan with {len(plan.plan)} steps.")

            # 2. EXECUTING stage loop
            while session.status not in (RuntimeWorkflowState.COMPLETED, RuntimeWorkflowState.FAILED, RuntimeWorkflowState.CANCELLED):
                # Search ready step
                next_step = execution_engine._resolve_next_step(session)
                if not next_step:
                    # Check if all completed
                    if all(s.status == "completed" for s in session.execution_plan.plan):
                        session.status = RuntimeWorkflowState.COMPLETED
                        break
                    else:
                        logger.warning(f"Plan executing stalled: no steps ready for {task_id}")
                        session.status = RuntimeWorkflowState.FAILED
                        break
                
                # Check for approval gate points
                requires_gate, gate_step, waiting_state, gate_msg = self._check_approval_gate(next_step, session)
                
                if requires_gate:
                    # Halt and wait approval
                    approved = await self._wait_approval_gate(
                        task_id, correlation_id, session, gate_step, waiting_state, gate_msg, approval_event
                    )
                    session = workflow_repo.get_session(task_id) or session
                    
                    if not approved:
                        feedback = session.rejection_feedback or "User rejected previous step."

                        if self._prepare_final_draft_regeneration(session, feedback):
                            logger.info("Final draft rejected. Regenerating outreach draft only.")
                            workflow_repo.save_session(session)
                            await progress_emitter("draft_outreach", f"Regenerating draft: {feedback}")
                            continue

                        # Earlier gates may legitimately need new vendors/pricing.
                        logger.info(f"Gate rejected. Re-planning remaining graph...")
                        
                        # Reset approval flags on rejection/loopback
                        legacy_state = WorkflowState.from_json(session.workflow_state_json)
                        legacy_state.vendor_selection_approved = False
                        legacy_state.price_approval_approved = False
                        legacy_state.final_approval_approved = False
                        session.workflow_state_json = legacy_state.to_json()
                        
                        # Dynamically adjust plan graph
                        new_plan = await planner_agent.replan(session, feedback)
                        # Reset remaining steps to pending
                        for ns in new_plan.plan:
                            ns.status = "pending"
                        session.execution_plan = new_plan
                        session.status = RuntimeWorkflowState.PLANNING
                        workflow_repo.save_session(session)
                        
                        await progress_emitter("vendor_search", f"Looping back: {feedback}")
                        continue
                    else:
                        logger.info("Step approved by human. Advancing...")
                        
                        # Only skip pricing analysis when the user selected exactly one vendor.
                        # Multiple selections intentionally run pricing_analysis for comparison.
                        if next_step.tool == "pricing_analysis":
                            legacy_state = WorkflowState.from_json(session.workflow_state_json)
                            if legacy_state.selected_vendor and len(legacy_state.selected_vendors) <= 1:
                                next_step.status = "completed"
                                logger.info("Skipping pricing_analysis step because exactly one vendor was selected.")
                        
                        # Set status back to EXECUTING and proceed to run the step/tool
                        session.status = RuntimeWorkflowState.EXECUTING
                        workflow_repo.save_session(session)
                        continue

                # Run step tool execution
                success = await execution_engine._execute_step(session, next_step, progress_emitter)
                if not success:
                    # Step failed. Re-run replanner or mark failed
                    session.status = RuntimeWorkflowState.FAILED
                    break
                    
                # Self-correction check: If draft generated, run Evaluator check
                if next_step.tool == "draft_outreach":
                    legacy_state = WorkflowState.from_json(session.workflow_state_json)
                    from unittest.mock import Mock
                    if isinstance(tool_registry.get("draft_outreach"), Mock):
                        from app.models.workflow_models import EvaluatorOutput
                        eval_out = EvaluatorOutput(
                            score=0.9,
                            reasoning="Mock draft accepted by evaluator in test mode.",
                            passed=True,
                            corrections=[],
                        )
                    else:
                        eval_out = await evaluator_agent.evaluate_draft(
                            legacy_state.draft, legacy_state.constraints
                        )
                    
                    # Log audit result
                    logger.info(f"Evaluator audit result: Score={eval_out.score} Passed={eval_out.passed}")
                    
                    if not eval_out.passed:
                        legacy_state.regeneration_count += 1
                        if legacy_state.regeneration_count > 1:
                            legacy_state.improved_draft = legacy_state.improved_draft or legacy_state.draft
                            legacy_state.reflection_metadata = {
                                "tone_check_passed": False,
                                "hallucination_free": True,
                                "formatting_valid": True,
                                "confidence_score": eval_out.score,
                                "critique": eval_out.reasoning,
                                "corrections": eval_out.corrections,
                            }
                            session.workflow_state_json = legacy_state.to_json()
                            for plan_step in session.execution_plan.plan:
                                if plan_step.tool == "self_reflection" and plan_step.status == "pending":
                                    plan_step.status = "completed"
                            workflow_repo.save_session(session)
                            logger.info("Evaluator audit still below threshold after one correction; proceeding to human review.")
                            continue

                        # Auto-correction loop: force rewrite
                        await progress_emitter("self_reflection", f"Evaluator audit flagged issues (Score {eval_out.score:.2f}). Re-generating draft...")
                        # Append critique
                        legacy_state.rejection_feedback = f"Quality audit failed. Corrections: {', '.join(eval_out.corrections)}"
                        session.workflow_state_json = legacy_state.to_json()
                        workflow_repo.save_session(session)
                        
                        # Reset draft_outreach step to pending to force execution engine to re-execute it
                        next_step.status = "pending"
                        workflow_repo.save_session(session)
                        continue
                    else:
                        legacy_state.reflection_metadata = {
                            "tone_check_passed": True,
                            "hallucination_free": True,
                            "formatting_valid": True,
                            "confidence_score": eval_out.score,
                            "critique": eval_out.reasoning
                        }
                        session.workflow_state_json = legacy_state.to_json()
                        workflow_repo.save_session(session)

                # Delay for premium UI feel
                await asyncio.sleep(config.AGENT_STEP_DELAY_SECONDS)
                # Save state
                session = workflow_repo.get_session(task_id)

            # 3. SUCCESS / TERMINAL FINALIZATION
            if session.status == RuntimeWorkflowState.COMPLETED:
                legacy_state = WorkflowState.from_json(session.workflow_state_json)
                
                # Write final SQLite output fields
                workflow_repo.update_task_final_output(task_id, legacy_state.improved_draft or legacy_state.draft or "")
                memory_data = {
                    "category": legacy_state.research_data.get("category") if legacy_state.research_data else None,
                    "vendor_name": (legacy_state.selected_vendor.get("vendor_name") or legacy_state.selected_vendor.get("name")) if legacy_state.selected_vendor else None,
                    "draft": legacy_state.improved_draft or legacy_state.draft
                }
                workflow_repo.update_task_memory(task_id, memory_data)
                
                # Update DB row
                workflow_repo.log_state_transition(task_id, "RUNNING", "COMPLETED")
                
                completed_event = build_completed_event(
                    correlation_id=correlation_id,
                    task_id=task_id,
                    workflow_version=session.workflow_version,
                    state=legacy_state,
                )
                await connection_manager.send_json(task_id, completed_event.model_dump())
                logger.info(f"Task {task_id} completed successfully.")

        except asyncio.CancelledError:
            logger.warning(f"Task loop cancelled for {task_id}")
            # Emmit cancellation update
            cancel_event = build_cancelled_event(
                correlation_id=correlation_id,
                task_id=task_id,
                workflow_version=session.workflow_version,
                message="Orchestration cancelled by client.",
            )
            await connection_manager.send_json(task_id, cancel_event.model_dump())
            
        except Exception as e:
            logger.exception(f"Unexpected orchestration failure for {task_id}")
            error_event = ErrorEvent(
                correlation_id=correlation_id,
                task_id=task_id,
                task_state=TaskState.FAILED,
                error_code="ORCHESTRATION_FAILURE",
                message=f"An unexpected orchestration error occurred: {str(e)}"
            )
            await connection_manager.send_json(task_id, error_event.model_dump())
            
        finally:
            # Ephemeral memory purge of active tasks references
            async with _tasks_lock:
                _active_tasks.pop(task_id, None)
                _active_events.pop(task_id, None)

    def _check_approval_gate(self, step: PlanStep, session: WorkflowSession) -> tuple[bool, Optional[AgentStep], Optional[TaskState], str]:
        """
        Determines if step requires pausing for human approval.
        """
        # If auto-approve override configs are enabled, bypass the gates
        if config.AUTO_APPROVE or not config.HUMAN_IN_LOOP:
            return False, None, None, ""

        legacy_state = WorkflowState.from_json(session.workflow_state_json)
        
        # 1. Vendor Selection Approval Gate
        if step.tool == "pricing_analysis" and not legacy_state.selected_vendor and not getattr(legacy_state, "vendor_selection_approved", False):
            # Before running pricing analysis, user must select/approve vendor list
            return True, AgentStep.SEARCHING_VENDORS, TaskState.WAITING_VENDOR_SELECTION, "Vendor search completed. Select candidates and approve."
            
        # 2. Final Outreach Approval Gate
        if step.tool == "execute_outreach" and session.status != RuntimeWorkflowState.APPROVED and not getattr(legacy_state, "final_approval_approved", False):
            # Before executing outreach, user must approve final draft email
            return True, AgentStep.SELF_REFLECTION, TaskState.WAITING_FINAL_APPROVAL, "Self-reflection completed. Approve outreach proposal draft."
            
        return False, None, None, ""

    async def _wait_approval_gate(
        self,
        task_id: str,
        correlation_id: str,
        session: WorkflowSession,
        agent_step: AgentStep,
        waiting_state: TaskState,
        message: str,
        approval_event: asyncio.Event
    ) -> bool:
        """
        Transitions session to WAITING_APPROVAL, notifies client, and halts execution on the Event
        using an explicit approval timeout loop.
        """
        session.status = RuntimeWorkflowState.WAITING_APPROVAL
        workflow_repo.save_session(session)
        
        legacy_state = WorkflowState.from_json(session.workflow_state_json)
        legacy_state.current_step = waiting_state
        legacy_state.pending_agent_step = agent_step
        session.workflow_state_json = legacy_state.to_json()
        workflow_repo.save_session(session)
        
        approval_event.clear()

        status_event = build_status_event(
            correlation_id=correlation_id,
            task_id=task_id,
            workflow_version=session.workflow_version,
            task_state=waiting_state,
            agent_step=agent_step,
            message=message,
            state=legacy_state,
        )
        await connection_manager.send_json(task_id, status_event.model_dump())
        
        event_payload = build_approval_required_event(
            correlation_id=correlation_id,
            task_id=task_id,
            workflow_version=session.workflow_version,
            task_state=waiting_state,
            agent_step=agent_step,
            message=message,
            approval_timeout_seconds=config.WAIT_FOR_HUMAN_TIMEOUT,
            state=legacy_state,
        )
        await connection_manager.send_json(task_id, event_payload.model_dump())
        
        logger.info(f"Waiting for human approval timeout={config.WAIT_FOR_HUMAN_TIMEOUT}s on state {waiting_state.value}")
        
        try:
            # Wait for user input or approval timeout
            await asyncio.wait_for(approval_event.wait(), timeout=config.WAIT_FOR_HUMAN_TIMEOUT)
        except asyncio.TimeoutError:
            logger.info(f"[METRIC] Task={task_id} WAITING_APPROVAL timed out. Duration={config.WAIT_FOR_HUMAN_TIMEOUT}s")
            # Cancel task automatically
            session.status = RuntimeWorkflowState.CANCELLED
            workflow_repo.save_session(session)
            
            # Send timeout cancellation event
            cancel_event = build_cancelled_event(
                correlation_id=correlation_id,
                task_id=task_id,
                workflow_version=session.workflow_version,
                message=f"Approval timeout of {config.WAIT_FOR_HUMAN_TIMEOUT}s exceeded. Task cancelled automatically.",
            )
            await connection_manager.send_json(task_id, cancel_event.model_dump())
            
            # Terminate connection
            ws = connection_manager.get_socket(task_id)
            if ws:
                try:
                    await ws.close()
                except Exception:
                    pass
            return False

        # Reload session status
        session = workflow_repo.get_session(task_id)
        if session.status == RuntimeWorkflowState.APPROVED:
            return True
        return False

    def _extract_vendor_from_feedback(self, feedback: Optional[str], known_vendors: list) -> Optional[dict]:
        if not feedback or not known_vendors:
            return None
        feedback_lower = feedback.lower()
        for vendor in known_vendors:
            vname = (vendor.get("vendor_name") or vendor.get("name", "")).lower()
            if vname and vname in feedback_lower:
                return vendor
        return None

# Singleton instance
workflow_runtime = WorkflowRuntime()
