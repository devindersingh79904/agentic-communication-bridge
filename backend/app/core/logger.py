import logging
import sys
import contextvars
from datetime import datetime
from typing import Optional

# Context variables for async-safe correlation and task tracking
correlation_id_ctx: contextvars.ContextVar[str] = contextvars.ContextVar("correlation_id", default="")
task_id_ctx: contextvars.ContextVar[str] = contextvars.ContextVar("task_id", default="")

def get_correlation_id() -> str:
    return correlation_id_ctx.get()

def set_correlation_id(correlation_id: str) -> contextvars.Token[str]:
    return correlation_id_ctx.set(correlation_id)

def get_task_id() -> str:
    return task_id_ctx.get()

def set_task_id(task_id: str) -> contextvars.Token[str]:
    return task_id_ctx.set(task_id)

class AsyncContextFormatter(logging.Formatter):
    """
    Custom formatter that injects correlation_id and task_id from contextvars.
    Format: [YYYY-MM-DD HH:MM:SS.mmm] [LEVEL] [correlation_id] [task_id] [logger_name] message
    """
    def formatTime(self, record: logging.LogRecord, datefmt: Optional[str] = None) -> str:
        # Use datetime for accurate milliseconds formatting
        created = datetime.fromtimestamp(record.created)
        return created.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

    def format(self, record: logging.LogRecord) -> str:
        # Extract context variables
        corr_id = get_correlation_id() or "-"
        task_id = get_task_id() or "-"
        
        # Get formatted time
        time_str = self.formatTime(record)
        
        # Format the basic message
        if record.name == "uvicorn.access" and record.args and len(record.args) == 5:
            # Uvicorn access log args: (client_addr, method, full_path, http_version, status_code)
            _, method, full_path, _, status_code = record.args
            original_msg = f"{method} {full_path} {status_code}"
        else:
            original_msg = record.getMessage()
        
        # Build the final log string
        # Expected: [2026-05-19 18:42:11.234] [INFO] [abc123] [task-123] [agent_service] Starting workflow
        log_msg = f"[{time_str}] [{record.levelname}] [{corr_id}] [{task_id}] [{record.name}] {original_msg}"
        
        if record.exc_info:
            log_msg += f"\n{self.formatException(record.exc_info)}"
            
        return log_msg

def get_logger(name: str, level: int = logging.INFO) -> logging.Logger:
    """
    Returns a configured logger with the centralized AsyncContextFormatter.
    """
    logger = logging.getLogger(name)
    
    # Only configure if it doesn't already have handlers to avoid duplicate logs
    if not logger.handlers:
        logger.setLevel(level)
        logger.propagate = False
        
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(AsyncContextFormatter())
        logger.addHandler(handler)
        
    return logger

def setup_logging():
    """
    Centralized logging configuration.
    Configures root logger and intercepts Uvicorn loggers to use our custom formatter.
    Should be called at application startup.
    """
    # Create the centralized handler
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(AsyncContextFormatter())
    
    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    
    # Configure Uvicorn and FastAPI loggers
    for logger_name in ("uvicorn", "uvicorn.access", "uvicorn.error", "fastapi"):
        app_logger = logging.getLogger(logger_name)
        # Clear default handlers to prevent duplicate logs
        app_logger.handlers = []
        # Propagate to the root logger which now has our custom handler
        app_logger.propagate = True
