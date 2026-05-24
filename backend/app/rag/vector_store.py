import os
import json
import logging
from typing import List, Dict, Any, Optional
from app.core import config
from app.rag.embedding_service import get_embedding

logger = logging.getLogger("app.rag.vector_store")

# Define the 12 sample vendors (3 for each category: computer, transport, food, stationery)
SAMPLE_VENDORS = [
    # Category: computer
    {
        "vendor_name": "TechWorld Computers",
        "category": "computer",
        "items": [
            {"name": "MacBook Air M4", "price": 98000},
            {"name": "Dell XPS 15", "price": 120000},
            {"name": "Lenovo ThinkPad X1", "price": 110000}
        ],
        "rating": 4.6,
        "delivery_days": 2,
        "location": "Bangalore",
        "metadata": {"description": "Premium laptops, high-performance office workstations, and developer systems."}
    },
    {
        "vendor_name": "ByteEdge Systems",
        "category": "computer",
        "items": [
            {"name": "Gaming Laptop RTX 4070", "price": 95000},
            {"name": "Desktop PC Intel i7", "price": 85000},
            {"name": "Monitor 4K", "price": 30000}
        ],
        "rating": 4.3,
        "delivery_days": 3,
        "location": "Whitefield",
        "metadata": {"description": "Custom builds, gaming setups, and high-resolution creative monitors."}
    },
    {
        "vendor_name": "NextGen PC Hub",
        "category": "computer",
        "items": [
            {"name": "HP EliteBook", "price": 90000},
            {"name": "Office Desktop", "price": 50000},
            {"name": "Keyboard & Mouse Bundle", "price": 2500}
        ],
        "rating": 4.5,
        "delivery_days": 1,
        "location": "Bellandur",
        "metadata": {"description": "Affordable corporate workstations, business laptops, and accessories."}
    },
    # Category: transport
    {
        "vendor_name": "SwiftRide Logistics",
        "category": "transport",
        "items": [
            {"name": "City Delivery Truck Rental", "price": 5000},
            {"name": "Courier Van Rental", "price": 3000},
            {"name": "Cargo Bike Delivery Service", "price": 500}
        ],
        "rating": 4.8,
        "delivery_days": 1,
        "location": "Koramangala",
        "metadata": {"description": "Intra-city transport, van rentals, and courier bike services."}
    },
    {
        "vendor_name": "EcoTransit Wheels",
        "category": "transport",
        "items": [
            {"name": "Electric Van Hire", "price": 4000},
            {"name": "Bicycle Courier Service", "price": 400},
            {"name": "Cargo EV Hire", "price": 3500}
        ],
        "rating": 4.4,
        "delivery_days": 2,
        "location": "Indiranagar",
        "metadata": {"description": "Sustainable and eco-friendly transportation, electric vehicles, and bike messengers."}
    },
    {
        "vendor_name": "Apex Heavy Movers",
        "category": "transport",
        "items": [
            {"name": "10-Ton Truck Service", "price": 15000},
            {"name": "Flatbed Trailer Hire", "price": 25000},
            {"name": "Logistics Consulting", "price": 8000}
        ],
        "rating": 4.2,
        "delivery_days": 4,
        "location": "Peenya",
        "metadata": {"description": "Heavy-duty logistics, freight movement, bulk shipping, and warehousing."}
    },
    # Category: food
    {
        "vendor_name": "Healthy Bites Catering",
        "category": "food",
        "items": [
            {"name": "Corporate Lunch Buffet", "price": 300},
            {"name": "Fruit Platter Bundle", "price": 1500},
            {"name": "Organic Snack Box", "price": 120}
        ],
        "rating": 4.7,
        "delivery_days": 1,
        "location": "HSR Layout",
        "metadata": {"description": "Nutritious corporate meals, events catering, and healthy organic office snacks."}
    },
    {
        "vendor_name": "Saffron Flavors",
        "category": "food",
        "items": [
            {"name": "Indian Deluxe Meal Combo", "price": 250},
            {"name": "Biryani Party Pack", "price": 5000},
            {"name": "Traditional Sweet Box", "price": 450}
        ],
        "rating": 4.5,
        "delivery_days": 1,
        "location": "Jayanagar",
        "metadata": {"description": "Authentic Indian cuisine and high-volume catering for office celebrations."}
    },
    {
        "vendor_name": "Express Quick Lunch",
        "category": "food",
        "items": [
            {"name": "Office Sandwich Box", "price": 180},
            {"name": "Coffee & Cookie Flask", "price": 800},
            {"name": "Pastry Assortment", "price": 600}
        ],
        "rating": 4.1,
        "delivery_days": 1,
        "location": "Bellandur",
        "metadata": {"description": "Quick office lunch deliveries, breakfast boxes, and beverages."}
    },
    # Category: stationery
    {
        "vendor_name": "OfficeMate Depot",
        "category": "stationery",
        "items": [
            {"name": "A4 Printing Paper Carton", "price": 1500},
            {"name": "Premium Notebook Set", "price": 600},
            {"name": "Ergonomic Office Chair", "price": 8500}
        ],
        "rating": 4.6,
        "delivery_days": 2,
        "location": "Marathahalli",
        "metadata": {"description": "Full-service office supplies, writing notebooks, and office furniture."}
    },
    {
        "vendor_name": "Papercraft & Co",
        "category": "stationery",
        "items": [
            {"name": "Recycled Notebook Pack", "price": 500},
            {"name": "Eco-friendly Pens (10x)", "price": 150},
            {"name": "Whiteboard Marker Set", "price": 200}
        ],
        "rating": 4.5,
        "delivery_days": 2,
        "location": "MG Road",
        "metadata": {"description": "Eco-friendly stationery, notebooks, writing materials, and desktop tools."}
    },
    {
        "vendor_name": "Bulk Stationery Hub",
        "category": "stationery",
        "items": [
            {"name": "Magnetic Whiteboard", "price": 2500},
            {"name": "Stapler & Punches Bulk", "price": 1200},
            {"name": "A4 Paper Box", "price": 1400}
        ],
        "rating": 4.3,
        "delivery_days": 3,
        "location": "Chickpet",
        "metadata": {"description": "Wholesale supplier of stationery items, whiteboards, and office accessories."}
    }
]

def make_vendor_text(vendor: Dict[str, Any]) -> str:
    """Helper to convert vendor fields into a dense string for embedding."""
    items_str = ", ".join([f"{i['name']} (${i['price']})" for i in vendor["items"]])
    desc = vendor["metadata"].get("description", "")
    return (
        f"Vendor Name: {vendor['vendor_name']}. "
        f"Category: {vendor['category']}. "
        f"Location: {vendor['location']}. "
        f"Items: {items_str}. "
        f"Rating: {vendor['rating']}. "
        f"Delivery: {vendor['delivery_days']} days. "
        f"Description: {desc}"
    )

class BaseVectorStore:
    async def add_vendors(self, vendors: List[Dict[str, Any]]) -> None:
        pass
    async def query_vendors(self, query_emb: List[float], category: Optional[str] = None, top_k: int = 3) -> List[Dict[str, Any]]:
        return []

class PurePythonVectorStore(BaseVectorStore):
    """Fallback vector store that computes Cosine Similarity in pure python."""
    def __init__(self):
        self.vendors: List[Dict[str, Any]] = []
        self.embeddings: List[List[float]] = []

    async def add_vendors(self, vendors: List[Dict[str, Any]]) -> None:
        for v in vendors:
            text = make_vendor_text(v)
            try:
                emb = await get_embedding(text)
                self.vendors.append(v)
                self.embeddings.append(emb)
            except Exception as e:
                logger.error(f"Failed to embed vendor {v.get('vendor_name')}: {e}")

    def _cosine_similarity(self, a: List[float], b: List[float]) -> float:
        dot_product = sum(x * y for x, y in zip(a, b))
        norm_a = sum(x * x for x in a) ** 0.5
        norm_b = sum(y * y for y in b) ** 0.5
        return dot_product / (norm_a * norm_b or 1e-9)

    async def query_vendors(self, query_emb: List[float], category: Optional[str] = None, top_k: int = 3) -> List[Dict[str, Any]]:
        results = []
        for v, emb in zip(self.vendors, self.embeddings):
            if category and v["category"].lower() != category.lower():
                continue
            sim = self._cosine_similarity(query_emb, emb)
            # Map similarity from [-1, 1] to [0, 1] confidence score
            conf = (sim + 1.0) / 2.0
            vendor_copy = dict(v)
            vendor_copy["confidence"] = conf
            results.append(vendor_copy)
            
        results.sort(key=lambda x: x["confidence"], reverse=True)
        return results[:top_k]

class ChromaVectorStore(BaseVectorStore):
    """ChromaDB implementation of the Vector Store."""
    def __init__(self, persist_dir: str):
        import chromadb
        self.client = chromadb.PersistentClient(path=persist_dir)
        self.collection = self.client.get_or_create_collection("procurement_vendors")

    async def add_vendors(self, vendors: List[Dict[str, Any]]) -> None:
        ids = []
        embeddings = []
        documents = []
        metadatas = []
        
        for i, v in enumerate(vendors):
            text = make_vendor_text(v)
            try:
                emb = await get_embedding(text)
                ids.append(f"vendor_{v['category']}_{i}")
                embeddings.append(emb)
                documents.append(text)
                metadatas.append({
                    "vendor_name": v["vendor_name"],
                    "category": v["category"],
                    "location": v["location"],
                    "rating": v["rating"],
                    "delivery_days": v["delivery_days"],
                    "items": json.dumps(v["items"]),
                    "metadata": json.dumps(v["metadata"])
                })
            except Exception as e:
                logger.error(f"ChromaDB failed to embed {v.get('vendor_name')}: {e}")
                
        if ids:
            self.collection.add(
                ids=ids,
                embeddings=embeddings,
                documents=documents,
                metadatas=metadatas
            )

    async def query_vendors(self, query_emb: List[float], category: Optional[str] = None, top_k: int = 3) -> List[Dict[str, Any]]:
        where_clause = {}
        if category:
            where_clause = {"category": category}
            
        query_args = {
            "query_embeddings": [query_emb],
            "n_results": top_k
        }
        if where_clause:
            query_args["where"] = where_clause

        res = self.collection.query(**query_args)
        
        results = []
        if res and res.get("metadatas") and res["metadatas"][0]:
            metadatas = res["metadatas"][0]
            distances = res.get("distances", [[]])[0]
            
            for meta, dist in zip(metadatas, distances):
                # Chroma distance is often L2 squared distance. Let's convert to confidence score.
                # Lower distance -> higher confidence. Map distance to confidence.
                similarity = 1.0 / (1.0 + dist)
                
                results.append({
                    "vendor_name": meta["vendor_name"],
                    "category": meta["category"],
                    "location": meta["location"],
                    "rating": float(meta["rating"]),
                    "delivery_days": int(meta["delivery_days"]),
                    "items": json.loads(meta["items"]),
                    "metadata": json.loads(meta["metadata"]),
                    "confidence": float(similarity)
                })
        return results

# Central store instance
_vector_store: Optional[BaseVectorStore] = None

async def init_vector_store() -> BaseVectorStore:
    global _vector_store
    if _vector_store is not None:
        return _vector_store

    try:
        import chromadb
        logger.info(f"Initializing ChromaDB vector store at {config.CHROMA_PERSIST_PATH}")
        store = ChromaVectorStore(config.CHROMA_PERSIST_PATH)
        # Seed if collection is empty
        if store.collection.count() == 0:
            logger.info("ChromaDB collection is empty. Seeding sample vendors...")
            await store.add_vendors(SAMPLE_VENDORS)
        _vector_store = store
    except ImportError:
        logger.warning("ChromaDB package is not installed. Falling back to PurePythonVectorStore.")
        store = PurePythonVectorStore()
        await store.add_vendors(SAMPLE_VENDORS)
        _vector_store = store
    except Exception as e:
        logger.error(f"Failed to initialize ChromaDB ({e}). Falling back to PurePythonVectorStore.")
        store = PurePythonVectorStore()
        await store.add_vendors(SAMPLE_VENDORS)
        _vector_store = store

    return _vector_store

async def get_vector_store() -> BaseVectorStore:
    global _vector_store
    if _vector_store is None:
        await init_vector_store()
    return _vector_store
