import json
import logging
import re
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field
from app.services.llm_service import _llm_call
from app.core import config

logger = logging.getLogger("app.tools.pricing_analysis")

class PricingAnalysisInput(BaseModel):
    query: str = Field(..., description="Original user procurement requirements")
    vendors: List[Dict[str, Any]] = Field(..., description="List of vendor dictionaries to compare")

class PricingAnalysisOutput(BaseModel):
    recommended_vendor: Dict[str, Any]
    analysis_summary: str
    reasoning: List[str]
    confidence: float = Field(0.85, description="Confidence score from 0.0 to 1.0")

async def pricing_analysis_tool(query: str, vendors: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Compares vendors on pricing, delivery speed, and rating to select the best vendor.
    Invokes the LLM for structured selection.
    """
    logger.info(f"Executing pricing analysis for query: '{query}' over {len(vendors)} vendors")
    
    # Input verification
    input_data = PricingAnalysisInput(query=query, vendors=vendors)
    
    if not input_data.vendors:
        raise ValueError("No vendors provided for pricing analysis")
        
    system_prompt = (
        "You are an expert procurement analyst. Compare the list of vendors provided and "
        "select the single best vendor based on the user's requirements. "
        "Generate a structured JSON response with the following keys:\n"
        "{\n"
        '  "recommendedVendor": "<name of the selected vendor>",\n'
        '  "confidence": 0.87,\n'
        '  "reasoning": ["<reason 1>", "<reason 2>", "<reason 3>"],\n'
        '  "analysis_summary": "<brief summary comparing the choices>"\n'
        "}\n"
        "Return ONLY the raw JSON object, no markdown blocks, no other text."
    )
    
    user_content = (
        f"User Requirements: {input_data.query}\n\n"
        f"Candidate Vendors:\n{json.dumps(input_data.vendors, indent=2)}\n\n"
        f"Select the best vendor, compare their pricing/items, ratings, and delivery times, and output the JSON structure."
    )
    
    recommended_vendor = input_data.vendors[0]
    analysis_summary = "Analysis completed."
    reasoning = ["Default selection due to parsing error"]
    confidence = 0.85
    
    try:
        raw_output = await _llm_call("pricing_analysis", system_prompt, user_content, max_tokens=600)
        
        # Regex sanitization to extract JSON
        json_match = re.search(r'\{.*\}', raw_output, re.DOTALL)
        if json_match:
            raw_output = json_match.group(0)
            
        data = json.loads(raw_output)
        selected_name = data.get("recommendedVendor", "").strip().lower()
        analysis_summary = data.get("analysis_summary", "Analysis completed.")
        reasoning = data.get("reasoning", [])
        confidence = float(data.get("confidence", 0.85))
        
        # Find matching vendor by name (case-insensitive fuzzy match)
        for v in input_data.vendors:
            v_name = (v.get("vendor_name") or v.get("name", "")).strip().lower()
            if selected_name in v_name or v_name in selected_name:
                recommended_vendor = v
                break
    except Exception as e:
        logger.error(f"Pricing analysis LLM call failed or failed to parse: {e}")
        # Default: select vendor with highest rating
        input_data.vendors.sort(key=lambda x: x.get("rating", 0.0), reverse=True)
        recommended_vendor = input_data.vendors[0]
        analysis_summary = f"Selected {recommended_vendor.get('vendor_name') or recommended_vendor.get('name')} based on highest vendor rating."
        reasoning = ["Highest vendor rating", f"Delivery within {recommended_vendor.get('delivery_days', 3)} days"]
        confidence = 0.70
        
    output = PricingAnalysisOutput(
        recommended_vendor=recommended_vendor,
        analysis_summary=analysis_summary,
        reasoning=reasoning,
        confidence=confidence
    )
    return output.model_dump()
