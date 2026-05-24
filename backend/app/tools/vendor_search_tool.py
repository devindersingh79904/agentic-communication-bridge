import logging
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field
from app.rag.vendor_retriever import retrieve_vendors

logger = logging.getLogger("app.tools.vendor_search")

class VendorSearchInput(BaseModel):
    query: str = Field(..., description="The user's procurement or search query")
    category: Optional[str] = Field(None, description="Optional vendor category (computer, transport, food, stationery)")
    top_k: int = Field(3, description="Number of top results to return")

class VendorSearchOutput(BaseModel):
    vendors: List[Dict[str, Any]]
    confidence: float

async def vendor_search_tool(query: str, category: Optional[str] = None, top_k: int = 3) -> Dict[str, Any]:
    """
    Independently callable vendor search tool.
    Semantically searches the internal vendor database.
    """
    logger.info(f"Executing internal vendor search for: '{query}' (category: {category})")
    
    # Input Validation
    input_data = VendorSearchInput(query=query, category=category, top_k=top_k)
    
    # Semantic Retrieval
    matched = await retrieve_vendors(input_data.query, category=input_data.category, top_k=input_data.top_k)
    
    # Calculate aggregate confidence as the highest similarity score, defaulting to 0.0 if empty
    agg_confidence = max([v.get("confidence", 0.0) for v in matched]) if matched else 0.0
    
    output = VendorSearchOutput(vendors=matched, confidence=agg_confidence)
    return output.model_dump()
