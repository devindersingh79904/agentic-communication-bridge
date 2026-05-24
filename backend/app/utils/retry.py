import asyncio
import logging
from functools import wraps
from typing import Callable, Any, Tuple
from app.core import config

logger = logging.getLogger("app.utils.retry")

def retry_with_backoff(
    max_retries: int = config.MAX_RETRY_ATTEMPTS,
    initial_delay: float = config.RETRY_INITIAL_DELAY,
    backoff_factor: float = config.RETRY_BACKOFF_FACTOR,
    exceptions: Tuple[type, ...] = (Exception,)
):
    """
    A decorator that retries an async function with exponential backoff.
    """
    def decorator(func: Callable[..., Any]):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            delay = initial_delay
            for attempt in range(1, max_retries + 1):
                try:
                    return await func(*args, **kwargs)
                except exceptions as e:
                    if attempt == max_retries:
                        logger.error(
                            f"Operation '{func.__name__}' failed permanently after {max_retries} attempts. Error: {e}"
                        )
                        raise
                    logger.warning(
                        f"Operation '{func.__name__}' failed (attempt {attempt}/{max_retries}). "
                        f"Retrying in {delay:.2f}s... Error: {e}"
                    )
                    await asyncio.sleep(delay)
                    delay *= backoff_factor
        return wrapper
    return decorator
