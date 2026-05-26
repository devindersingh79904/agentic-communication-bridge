import os
import json
import logging
from typing import List, Dict, Any, Optional
from app.core import config
from app.rag.embedding_service import get_embedding

logger = logging.getLogger("app.rag.vector_store")

# Load sample vendors from JSON
RAG_DIR = os.path.dirname(os.path.abspath(__file__))
SAMPLE_VENDORS_PATH = os.path.join(RAG_DIR, "sample_vendors.json")

try:
    with open(SAMPLE_VENDORS_PATH, "r") as f:
        SAMPLE_VENDORS = json.load(f)
except Exception as e:
    logger.error(f"Failed to load sample vendors from {SAMPLE_VENDORS_PATH}: {e}")
    SAMPLE_VENDORS = []


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
                    "confidence": float(similarity),
                    "source_type": "db",
                    "source": "rag"
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
        # Seed if collection is empty or contains the old sample set size (12)
        current_count = store.collection.count()
        if current_count == 0 or current_count == 12:
            logger.info(f"ChromaDB collection contains {current_count} vendors. Re-seeding with updated 40 sample vendors...")
            try:
                store.client.delete_collection("procurement_vendors")
            except Exception:
                pass
            store.collection = store.client.get_or_create_collection("procurement_vendors")
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


async def reset_vector_store() -> BaseVectorStore:
    """
    Rebuilds the persisted vendor collection with embeddings from the active provider.
    Useful after switching embedding models, which changes vector dimensions.
    """
    global _vector_store

    try:
        import chromadb
        logger.info("Resetting ChromaDB vendor collection at %s", config.CHROMA_PERSIST_PATH)
        store = ChromaVectorStore(config.CHROMA_PERSIST_PATH)
        try:
            store.client.delete_collection("procurement_vendors")
        except Exception:
            pass
        store.collection = store.client.get_or_create_collection("procurement_vendors")
        await store.add_vendors(SAMPLE_VENDORS)
        _vector_store = store
    except Exception as e:
        logger.error("Failed to reset ChromaDB (%s). Falling back to PurePythonVectorStore.", e)
        store = PurePythonVectorStore()
        await store.add_vendors(SAMPLE_VENDORS)
        _vector_store = store

    return _vector_store
