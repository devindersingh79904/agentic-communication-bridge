import logging
import httpx
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field
from app.core import config

logger = logging.getLogger("app.tools.external_vendor_search")

class ExternalSearchInput(BaseModel):
    query: str = Field(..., description="Query for external search")
    category: str = Field(..., description="Detected category (computer, transport, food, stationery)")

class ExternalSearchOutput(BaseModel):
    vendors: List[Dict[str, Any]]
    confidence: float

# Predefined high-quality external vendors for mock search fallbacks
EXTERNAL_MOCK_DATA = {
    "computer": [
        {
            "vendor_name": "GlobalTech Solutions",
            "category": "computer",
            "items": [{"name": "Developer Laptop Core Ultra", "price": 92000}, {"name": "4K UltraWide Screen", "price": 28000}],
            "rating": 4.7,
            "delivery_days": 3,
            "location": "Electronic City",
            "source_url": "https://globaltechsolutions.co.in",
            "metadata": {"description": "Enterprise-wide hardware solutions, import distributor of premium systems."}
        },
        {
            "vendor_name": "Silicon Valley Hardware",
            "category": "computer",
            "items": [{"name": "Custom Workstation Ryzen 9", "price": 140000}, {"name": "Mechanical Keyboard", "price": 4500}],
            "rating": 4.4,
            "delivery_days": 2,
            "location": "Koramangala",
            "source_url": "https://siliconvalleyhardware.in",
            "metadata": {"description": "Specialized developer desktops, high-speed RAM and component upgrades."}
        }
    ],
    "transport": [
        {
            "vendor_name": "Rapid Cargo Express",
            "category": "transport",
            "items": [{"name": "Intercity Container Service", "price": 12000}, {"name": "Lorry Rental (24h)", "price": 9000}],
            "rating": 4.6,
            "delivery_days": 1,
            "location": "Yeshwanthpur",
            "source_url": "https://rapidcargoexpress.com",
            "metadata": {"description": "Fast logistics, container shipping, express intra-state delivery."}
        },
        {
            "vendor_name": "Metro Logistics Hub",
            "category": "transport",
            "items": [{"name": "E-Bike Fleet Delivery Pack", "price": 1200}, {"name": "Mini-Truck Hire", "price": 2800}],
            "rating": 4.5,
            "delivery_days": 1,
            "location": "Whitefield",
            "source_url": "https://metrologisticshub.com",
            "metadata": {"description": "Micro-logistics and electric bike fleets for high-density tech park deliveries."}
        }
    ],
    "food": [
        {
            "vendor_name": "Green Gourmet Catering",
            "category": "food",
            "items": [{"name": "Eco Salad Bowl Box", "price": 220}, {"name": "Healthy Snacks Platter", "price": 1100}],
            "rating": 4.8,
            "delivery_days": 1,
            "location": "Indiranagar",
            "source_url": "https://greengourmet.in",
            "metadata": {"description": "Organic organic products and healthy corporate salads, plastic-free delivery."}
        },
        {
            "vendor_name": "Royal Banquet Foods",
            "category": "food",
            "items": [{"name": "Three-Course Buffet Catering", "price": 450}, {"name": "Hi-Tea Coffee Station Set", "price": 1500}],
            "rating": 4.6,
            "delivery_days": 1,
            "location": "Malleshwaram",
            "source_url": "https://royalbanquetfoods.co.in",
            "metadata": {"description": "Premium multi-cuisine buffet spreads, experienced office catering."}
        }
    ],
    "stationery": [
        {
            "vendor_name": "Paper World Wholesale",
            "category": "stationery",
            "items": [{"name": "Premium Copier Paper Box (5 Reams)", "price": 1150}, {"name": "Executive Planner Binder", "price": 450}],
            "rating": 4.4,
            "delivery_days": 2,
            "location": "Chickpet",
            "source_url": "https://paperworldwholesale.com",
            "metadata": {"description": "Bulk paper products, planners, whiteboards, and shipping labels."}
        },
        {
            "vendor_name": "Mega Office Supplies",
            "category": "stationery",
            "items": [{"name": "Whiteboard Easel Stand", "price": 3200}, {"name": "Marker & Accessories kit", "price": 600}],
            "rating": 4.5,
            "delivery_days": 1,
            "location": "HSR Layout",
            "source_url": "https://megaofficesupplies.co.in",
            "metadata": {"description": "Office hardware supplies, whiteboard setups, presentation tools."}
        }
    ]
}

async def external_vendor_search_tool(
    query: str, 
    category: str,
    city: str = "Bangalore",
    locality: str = "Marathahalli",
    pincode: str = "560037"
) -> Dict[str, Any]:
    """
    Optional web-search-based vendor discovery.
    Integrates with Tavily if API key is provided, otherwise falls back to a realistic mock search.
    """
    logger.info(f"Executing external vendor search for query: '{query}' in locality={locality}, city={city}, pincode={pincode}, category={category}")
    
    # Input Schema validation
    input_data = ExternalSearchInput(query=query, category=category)
    cat_normalized = input_data.category.lower()
    if cat_normalized not in EXTERNAL_MOCK_DATA:
        cat_normalized = "computer"
        
    vendors = []
    
    # Check Tavily Search Integration
    if config.TAVILY_API_KEY:
        logger.info("Tavily API key found. Querying Tavily...")
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    "https://api.tavily.com/search",
                    json={
                        "api_key": config.TAVILY_API_KEY,
                        "query": f"{query} vendor in {locality} {city} {pincode} {category}",
                        "search_depth": "basic",
                        "include_answer": False
                    }
                )
                if response.status_code == 200:
                    data = response.json()
                    results = data.get("results", [])
                    # Parse Tavily results into vendor formats
                    for i, r in enumerate(results[:2]):
                        vendors.append({
                            "vendor_name": r.get("title", f"Web Vendor {i+1}"),
                            "category": cat_normalized,
                            "items": [{"name": "Standard Services", "price": 0}],
                            "rating": 4.5,
                            "delivery_days": 3,
                            "location": "Online Search Result",
                            "source_url": r.get("url", "https://tavily.com"),
                            "metadata": {"description": r.get("content", "Found via web search.")}
                        })
        except Exception as e:
            logger.error(f"Tavily search request failed: {e}. Falling back to mock search.")
            
    # Mock fallback if Tavily not used or failed
    if not vendors:
        logger.info("Using high-quality mock external search results.")
        vendors = EXTERNAL_MOCK_DATA.get(cat_normalized, EXTERNAL_MOCK_DATA["computer"])
        
    output = ExternalSearchOutput(vendors=vendors, confidence=0.85)
    return output.model_dump()
