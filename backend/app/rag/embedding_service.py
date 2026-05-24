import logging
from typing import List
from app.core import config
from app.services.llm_service import get_client

logger = logging.getLogger("app.rag.embedding_service")

async def get_embedding(text: str) -> List[float]:
    """
    Generates a vector embedding for the input text using the configured LLM provider.
    - If provider is 'openai', uses 'text-embedding-3-small'.
    - If provider is 'ollama', uses OLLAMA_MODEL (assuming it supports embeddings or fallback).
    """
    client = get_client()
    
    if config.AGENT_PROVIDER == "ollama":
        # Check if a custom Ollama embedding model is configured, otherwise fallback to config.OLLAMA_MODEL
        model_name = config.OLLAMA_MODEL
        logger.debug(f"Generating Ollama embedding for text length {len(text)} using model {model_name}")
        try:
            response = await client.embeddings.create(
                input=[text],
                model=model_name
            )
            return response.data[0].embedding
        except Exception as e:
            logger.warning(f"Ollama embeddings API call failed with model '{model_name}': {e}. Trying fallback 'nomic-embed-text'.")
            response = await client.embeddings.create(
                input=[text],
                model="nomic-embed-text"
            )
            return response.data[0].embedding
            
    # Default: OpenAI
    model_name = "text-embedding-3-small"
    logger.debug(f"Generating OpenAI embedding for text length {len(text)} using model {model_name}")
    response = await client.embeddings.create(
        input=[text],
        model=model_name
    )
    return response.data[0].embedding
