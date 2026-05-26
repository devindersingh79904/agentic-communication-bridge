import logging
import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Centralized configuration variables
APPROVAL_TIMEOUT_SECONDS: int = int(os.getenv("APPROVAL_TIMEOUT_SECONDS", "60"))
HUMAN_IN_LOOP: bool = os.getenv("HUMAN_IN_LOOP", "true").lower() == "true"
AUTO_APPROVE: bool = os.getenv("AUTO_APPROVE", "false").lower() == "true"
WAIT_FOR_HUMAN_TIMEOUT: int = int(os.getenv("WAIT_FOR_HUMAN_TIMEOUT", "300"))
ENABLE_SELF_REFLECTION: bool = os.getenv("ENABLE_SELF_REFLECTION", "true").lower() == "true"
ENABLE_EXTERNAL_VENDOR_SEARCH: bool = os.getenv("ENABLE_EXTERNAL_VENDOR_SEARCH", "true").lower() == "true"


# Agent provider selection: "openai" or "ollama"
AGENT_PROVIDER: str = os.getenv("AGENT_PROVIDER", "openai").strip().lower()

# --- OpenAI configuration ---
OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-4.1-mini").strip()
OPENAI_TEMPERATURE: float = float(os.getenv("OPENAI_TEMPERATURE", "0.3"))

# --- Ollama configuration ---
_raw_ollama_base_url: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").strip()
# Ensure the base URL ends with /v1 so the OpenAI-compatible client works correctly
OLLAMA_BASE_URL: str = _raw_ollama_base_url.rstrip("/") + "/v1"
OLLAMA_MODEL: str = os.getenv("OLLAMA_MODEL", "qwen2.5-coder:7b").strip()

# Validate provider-specific requirements
if AGENT_PROVIDER == "openai":
    if not OPENAI_API_KEY:
        raise ValueError("OPENAI_API_KEY environment variable is required when AGENT_PROVIDER='openai'")
elif AGENT_PROVIDER == "ollama":
    # Ollama can run without an API key; user may set one if their setup requires it
    pass
else:
    raise ValueError(f"Unsupported AGENT_PROVIDER '{AGENT_PROVIDER}'. Must be 'openai' or 'ollama'.")

AGENT_STEP_DELAY_SECONDS: int = int(os.getenv("AGENT_STEP_DELAY_SECONDS", "2"))

MAX_REGENERATION_ATTEMPTS: int = int(os.getenv("MAX_REGENERATION_ATTEMPTS", "3"))

# Localized Procurement Context Configs
DEFAULT_CITY: str = os.getenv("DEFAULT_CITY", "").strip()
DEFAULT_LOCALITY: str = os.getenv("DEFAULT_LOCALITY", "").strip()
DEFAULT_PINCODE: str = os.getenv("DEFAULT_PINCODE", "").strip()

DEFAULT_USER_NAME: str = os.getenv("DEFAULT_USER_NAME", "Devinder Singh").strip()
DEFAULT_COMPANY_NAME: str = os.getenv("DEFAULT_COMPANY_NAME", "DSP Technologies").strip()
REFLECTION_TEMPERATURE: float = float(os.getenv("REFLECTION_TEMPERATURE", "0.6"))

# Database & Vector Store Settings
DB_PATH: str = os.getenv("DB_PATH", "agent_procurement.db").strip()
CHROMA_PERSIST_PATH: str = os.getenv("CHROMA_PERSIST_PATH", "./chroma_db").strip()

# External Vendor Search Keys
TAVILY_API_KEY: str = os.getenv("TAVILY_API_KEY", "").strip()
SERPAPI_API_KEY: str = os.getenv("SERPAPI_API_KEY", "").strip()

# WebSocket Heartbeat Settings
HEARTBEAT_INTERVAL_SECONDS: int = int(os.getenv("HEARTBEAT_INTERVAL_SECONDS", "15"))
HEARTBEAT_TIMEOUT_SECONDS: int = int(os.getenv("HEARTBEAT_TIMEOUT_SECONDS", "30"))

# Retry Settings
MAX_RETRY_ATTEMPTS: int = int(os.getenv("MAX_RETRY_ATTEMPTS", "3"))
RETRY_INITIAL_DELAY: float = float(os.getenv("RETRY_INITIAL_DELAY", "1.0"))
RETRY_BACKOFF_FACTOR: float = float(os.getenv("RETRY_BACKOFF_FACTOR", "2.0"))

# ---------------------------------------------------------------------------
# Log effective provider configuration on startup
# ---------------------------------------------------------------------------
_logger = logging.getLogger(__name__)
if AGENT_PROVIDER == "openai":
    # Mask the API key for safe logging
    _masked_key = (OPENAI_API_KEY[:8] + "..." + OPENAI_API_KEY[-4:]) if len(OPENAI_API_KEY) > 12 else "***"
    _logger.info(
        "Provider=openai Model=%s ApiKey=%s Temperature=%s",
        OPENAI_MODEL, _masked_key, OPENAI_TEMPERATURE,
    )
else:
    _logger.info(
        "Provider=ollama Model=%s BaseUrl=%s",
        OLLAMA_MODEL, OLLAMA_BASE_URL,
    )
