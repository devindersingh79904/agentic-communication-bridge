import logging
from typing import Callable, Dict, Any

logger = logging.getLogger("app.core.tool_registry")

class ToolRegistry:
    def __init__(self):
        self._registry: Dict[str, Callable] = {}

    def register(self, name: str, func: Callable):
        """Registers a tool function under a unique name."""
        self._registry[name] = func
        logger.info(f"Registered tool: '{name}'")

    def get(self, name: str) -> Callable:
        """Returns a registered tool by name."""
        if name not in self._registry:
            raise KeyError(f"Tool '{name}' is not registered in the ToolRegistry.")
        return self._registry[name]

    def has(self, name: str) -> bool:
        """Checks whether a tool is registered."""
        return name in self._registry

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

