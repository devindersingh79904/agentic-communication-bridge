import logging
from typing import List, Dict, Any, Optional
from app.rag.embedding_service import get_embedding
from app.rag.vector_store import get_vector_store

logger = logging.getLogger("app.rag.vendor_retriever")

async def retrieve_vendors(query: str, category: Optional[str] = None, top_k: int = 3) -> List[Dict[str, Any]]:
    """
    Semantically retrieves the top_k matching vendors for a query,
    optionally filtered by category.
    """
    logger.info(f"Retrieving vendors for query: '{query}', category: '{category}', top_k: {top_k}")
    try:
        # Generate query embedding
        query_emb = await get_embedding(query)
        
        # Get active vector store instance
        store = await get_vector_store()
        
        # Query results
        results = await store.query_vendors(query_emb, category=category, top_k=top_k)
        logger.info(f"Retrieved {len(results)} matching vendors")
        return results
    except Exception as e:
        logger.error(f"Failed to retrieve vendors: {e}")
        if "dimension" in str(e).lower():
            try:
                logger.warning("Embedding dimension mismatch detected. Rebuilding vector store and retrying query.")
                from app.rag.vector_store import reset_vector_store
                store = await reset_vector_store()
                results = await store.query_vendors(query_emb, category=category, top_k=top_k)
                logger.info(f"Retrieved {len(results)} matching vendors after vector store rebuild")
                return results
            except Exception as retry_error:
                logger.error(f"Retry after vector store rebuild failed: {retry_error}")

        # Return fallback mock list based on category matching if everything fails
        from app.rag.vector_store import SAMPLE_VENDORS
        fallback_results = []
        for v in SAMPLE_VENDORS:
            if category and v["category"].lower() != category.lower():
                continue
            v_copy = dict(v)
            v_copy["confidence"] = 0.5  # Neutral default confidence
            fallback_results.append(v_copy)
        return fallback_results[:top_k]
