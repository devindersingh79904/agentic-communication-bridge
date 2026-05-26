from app.tools.base_tool import BaseTool
from app.tools.vendor_search_tool import vendor_search_tool, VendorSearchTool
from app.tools.pricing_tool import PricingTool
from app.tools.draft_writer_tool import DraftWriterTool
from app.tools.reflection_tool import reflection_tool, ReflectionTool
from app.tools.execution_tool import execution_tool, ExecuteOutreachTool

# Predefined tool mapping for registry routing
TOOL_REGISTRY = {
    "vendor_search": VendorSearchTool(),
    "pricing_analysis": PricingTool(),
    "draft_outreach": DraftWriterTool(),
    "self_reflection": ReflectionTool(),
    "execute_outreach": ExecuteOutreachTool()
}
