import logging
from app.core.logger import get_logger, task_id_ctx
from app.core.enums import TaskState
from app.models.workflow_state import WorkflowState
from app.services.agent_planner import planner

logger = get_logger("tools.research")

async def research_tool(state: WorkflowState) -> None:
    """
    Upgraded Research Tool.
    Delegates task execution to the agent planner, enabling semantic search and
    dynamic fallback to web search.
    """
    logger.info("Research tool execution started for prompt: %.100s", state.prompt)
    task_id = task_id_ctx.get()
    
    # Define transition callback for task state modifications during execution
    async def transition_callback(new_state: TaskState):
        from app.services.agent_orchestrator_service import transition_task_state, active_tasks
        from app.repositories.task_repository import task_repo
        
        task_info = active_tasks.get(task_id)
        if task_info:
            old_state = task_info.get("task_state")
            if transition_task_state(task_id, new_state):
                task_repo.update_task_status(task_id, old_state, new_state)
                
    await planner.run_research(state, task_id, transition_callback)
    logger.info("Research tool execution completed")
