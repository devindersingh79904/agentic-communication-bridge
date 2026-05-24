import logging
from typing import Dict, Any, Optional
from pydantic import BaseModel, Field
from app.services.llm_service import generate_outreach_draft

logger = logging.getLogger("app.tools.recommendation")

class RecommendationInput(BaseModel):
    query: str = Field(..., description="The user prompt or procurement request")
    selected_vendor: Dict[str, Any] = Field(..., description="The selected vendor dict")
    analysis_summary: str = Field(..., description="The pricing analysis summary")

class RecommendationOutput(BaseModel):
    draft: str

async def recommendation_tool(query: str, selected_vendor: Dict[str, Any], analysis_summary: str) -> Dict[str, Any]:
    """
    Independently callable recommendation tool.
    Generates a professional email outreach draft targeting the selected vendor.
    """
    logger.info(f"Generating outreach recommendation draft for vendor: {selected_vendor.get('vendor_name')}")
    
    # Input validation
    input_data = RecommendationInput(query=query, selected_vendor=selected_vendor, analysis_summary=analysis_summary)
    
    # Call the draft generation service
    draft = await generate_outreach_draft(
        prompt=input_data.query,
        analysis_summary=input_data.analysis_summary,
        selected_vendor=input_data.selected_vendor
    )
    
    output = RecommendationOutput(draft=draft)
    return output.model_dump()
