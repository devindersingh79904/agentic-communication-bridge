import logging
from typing import Callable, Any, Optional
from app.core.logger import get_logger, task_id_ctx
from app.core.enums import TaskState
from app.models.workflow_state import WorkflowState
from app.services.agent_planner import planner

logger = get_logger("tools.research")

async def research_tool(
    state: WorkflowState, 
    progress_callback: Optional[Callable[[str, Optional[TaskState]], Any]] = None
) -> None:
    """
    Upgraded Research Tool.
    Delegates task execution to the agent planner, enabling semantic search and
    dynamic fallback to web search.
    """
    logger.info("Research tool execution started for prompt: %.100s", state.prompt)
    task_id = task_id_ctx.get()
    
    # Define transition callback for task state modifications during execution
    async def transition_callback(new_state: TaskState):
        if progress_callback:
            await progress_callback(
                f"Search status updated: {new_state.value}", 
                new_state
            )
                
    await planner.run_research(state, task_id, transition_callback)
    logger.info("Research tool execution completed")

