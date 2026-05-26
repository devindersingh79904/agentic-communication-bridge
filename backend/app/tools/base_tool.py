from abc import ABC, abstractmethod
from typing import Callable, Any, Optional
from app.models.workflow_models import WorkflowSession, ToolResult

class BaseTool(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        """The identifier name of the tool, matching what the planner returns."""
        pass

    @property
    @abstractmethod
    def description(self) -> str:
        """A brief description of what the tool does."""
        pass

    @abstractmethod
    async def execute(self, session: WorkflowSession, progress_callback: Optional[Callable[[str], Any]] = None) -> ToolResult:
        """
        Executes the tool with the current session state and returns a strongly-typed ToolResult.
        Can optionally trigger progress_callback with execution logs to stream status.
        """
        pass
