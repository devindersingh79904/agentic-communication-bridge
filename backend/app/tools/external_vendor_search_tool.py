import logging
import asyncio
from typing import List, Dict, Any, Optional
from urllib.parse import urlparse
from ddgs import DDGS
from pydantic import BaseModel, Field

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

def web_search(query: str, max_results: int = 5) -> List[Dict]:
    """
    Free DuckDuckGo web search tool for agent.
    No API key needed.
    """
    results = []

    try:
        with DDGS() as ddgs:
            search_results = ddgs.text(
                query=query,
                max_results=max_results
            )

            for item in search_results:
                results.append({
                    "title": item.get("title"),
                    "url": item.get("href"),
                    "snippet": item.get("body")
                })

    except Exception as e:
        return [{
            "title": "Search failed",
            "url": None,
            "snippet": str(e)
        }]

    return results

async def external_vendor_search_tool(
    query: str, 
    category: str,
    city: str = "",
    locality: str = "",
    pincode: str = ""
) -> Dict[str, Any]:
    """
    Optional web-search-based vendor discovery.
    Uses DuckDuckGo search and falls back to realistic mock search results if needed.
    """
    logger.info(f"Executing external vendor search for query: '{query}' in locality={locality}, city={city}, pincode={pincode}, category={category}")
    
    # Input Schema validation
    input_data = ExternalSearchInput(query=query, category=category)
    cat_normalized = input_data.category.lower()
    if cat_normalized not in EXTERNAL_MOCK_DATA:
        cat_normalized = "computer"
        
    vendors = []
    
    # search_query = (
    #     f"{category} suppliers vendors {locality} {city} {pincode}"
    #     f"{query} -wikipedia -dictionary -justdial -sulekha -indiamart"
    # ).strip()


    #use this search_query = f'{category} shops near {locality} {city} {pincode} -justdial -sulekha -indiamart -wikipedia'

    search_query = f'{query} {category} shops in  {locality} {city} {pincode}  -justdial -sulekha -indiamart -wikipedia'
    
    try:
        logger.info("Querying DuckDuckGo for external vendor search: %r", search_query)
        results = await asyncio.to_thread(web_search, search_query, 8)
        if results and results[0].get("title") == "Search failed":
            logger.error("DuckDuckGo search failed: %s", results[0].get("snippet"))
            results = []

        blocked_domains = {"wikipedia.org", "wiktionary.org", "britannica.com"}
        filtered_results = []
        #print the results 
        logger.info("DuckDuckGo search results: %r", results)
        for result in results:
            url = result.get("url") or ""
            domain = urlparse(url).netloc.lower().removeprefix("www.")
            if any(domain.endswith(blocked) for blocked in blocked_domains):
                continue
            filtered_results.append(result)
            if len(filtered_results) == 2:
                break

        for i, result in enumerate(filtered_results):
            title = result.get("title") or f"Web Vendor {i + 1}"
            url = result.get("url") or "https://duckduckgo.com"
            description = result.get("snippet") or "Found via DuckDuckGo search."
            vendors.append({
                "vendor_name": title,
                "category": cat_normalized,
                "items": [{"name": "Standard Services", "price": 0}],
                "rating": 4.5,
                "delivery_days": 3,
                "location": f"{locality}, {city}" if locality and city else "Web Search Result",
                "source_type": "web",
                "source": "duckduckgo",
                "source_url": url,
                "metadata": {"description": description}
            })
    except Exception as e:
        logger.error(f"DuckDuckGo search failed: {e}. Falling back to mock search.")
            
    # Mock fallback if DuckDuckGo fails or returns no usable vendors.
    if not vendors:
        logger.info("Using high-quality mock external search results.")
        vendors = [
            {**vendor, "source_type": "web", "source": "mock_web"}
            for vendor in EXTERNAL_MOCK_DATA.get(cat_normalized, EXTERNAL_MOCK_DATA["computer"])
        ]
        
    output = ExternalSearchOutput(vendors=vendors, confidence=0.85)
    return output.model_dump()
